"""Admin catalog edits for plans and entitlements."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import require_admin
from app.core.config import settings
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.logger import logger
from app.database.db import AnSession
from app.database.models import Admin
from app.database.models.billing_catalog import (
    BillingFeature,
    BillingPlan,
    BillingPlanEntitlement,
)
from app.services.billing import lago_client
from app.services.billing.catalog import invalidate_catalog_cache, seed_catalog
from app.services.billing.providers import get_billing_provider

router = APIRouter(prefix="/billing", tags=["Admin / Billing"])


class PlanPatch(BaseModel):
    amountKobo: int | None = Field(default=None, ge=0)
    displayName: str | None = None
    blurb: str | None = None
    isPurchasable: bool | None = None
    active: bool | None = None
    rank: int | None = None


class PlanCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    displayName: str = Field(min_length=1, max_length=120)
    amountKobo: int = Field(default=0, ge=0)
    blurb: str | None = None
    isPurchasable: bool = False
    rank: int = 0
    active: bool = True


class FeatureCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=120)
    valueType: Literal["boolean", "integer", "select"] = "boolean"
    selectOptions: list[str] | None = None
    gateKey: str | None = None
    description: str | None = None


class EntitlementPatch(BaseModel):
    enabled: bool | None = None
    privilegeValue: Any | None = None


def _feature_payload(feature: BillingFeature) -> dict[str, Any]:
    return {
        "code": feature.code,
        "name": feature.name,
        "valueType": feature.value_type,
        "selectOptions": feature.select_options,
        "gateKey": feature.gate_key,
        "description": feature.description,
    }


def _admin_plan_payload(plan: BillingPlan, entitlements: list[dict]) -> dict[str, Any]:
    return {
        "code": plan.code,
        "displayName": plan.display_name,
        "amountKobo": plan.amount_kobo,
        "currency": plan.currency,
        "interval": plan.interval,
        "isPurchasable": plan.is_purchasable,
        "rank": plan.rank,
        "active": plan.active,
        "blurb": plan.blurb,
        "lagoPlanCode": plan.lago_plan_code,
        "features": entitlements,
    }


@router.get("/plans")
async def admin_list_plans(
    session: AnSession,
    _admin: Annotated[Admin, Depends(require_admin)],
):
    """Full catalog for the admin console (includes inactive plans)."""
    plans = list(
        (await session.execute(select(BillingPlan).order_by(BillingPlan.rank)))
        .scalars()
        .all()
    )
    if not plans:
        await seed_catalog(session)
        plans = list(
            (await session.execute(select(BillingPlan).order_by(BillingPlan.rank)))
            .scalars()
            .all()
        )

    features = {
        f.id: f for f in (await session.execute(select(BillingFeature))).scalars().all()
    }
    rows = (await session.execute(select(BillingPlanEntitlement))).scalars().all()
    by_plan: dict[str, list[dict]] = {p.id: [] for p in plans}
    for row in rows:
        feature = features.get(row.feature_id)
        if feature is None or row.plan_id not in by_plan:
            continue
        by_plan[row.plan_id].append(
            {
                "code": feature.code,
                "name": feature.name,
                "valueType": feature.value_type,
                "selectOptions": feature.select_options,
                "gateKey": feature.gate_key,
                "enabled": row.enabled,
                "value": row.privilege_value,
            }
        )

    # Ensure every feature appears on every plan (missing → disabled stub).
    for plan in plans:
        present = {f["code"] for f in by_plan[plan.id]}
        for feature in features.values():
            if feature.code in present:
                continue
            by_plan[plan.id].append(
                {
                    "code": feature.code,
                    "name": feature.name,
                    "valueType": feature.value_type,
                    "selectOptions": feature.select_options,
                    "gateKey": feature.gate_key,
                    "enabled": False,
                    "value": None,
                }
            )
        by_plan[plan.id].sort(key=lambda f: f["code"])

    return {
        "plans": [_admin_plan_payload(p, by_plan[p.id]) for p in plans],
        "featureCatalog": [_feature_payload(f) for f in features.values()],
    }


@router.get("/features")
async def admin_list_features(
    session: AnSession,
    _admin: Annotated[Admin, Depends(require_admin)],
):
    features = (await session.execute(select(BillingFeature))).scalars().all()
    return {"features": [_feature_payload(f) for f in features]}


@router.post("/plans/seed")
async def admin_seed_plans(
    session: AnSession,
    _admin: Annotated[Admin, Depends(require_admin)],
):
    counts = await seed_catalog(session)
    try:
        await _push_catalog_to_provider(session)
    except Exception:
        logger.exception("Billing catalog sync failed")
    return {"ok": True, **counts}


@router.post("/plans")
async def admin_create_plan(
    body: PlanCreate,
    session: AnSession,
    _admin: Annotated[Admin, Depends(require_admin)],
):
    existing = await session.scalar(
        select(BillingPlan).where(BillingPlan.code == body.code)
    )
    if existing is not None:
        raise ApiError(409, "Plan code already exists")
    plan = BillingPlan(
        id=cuid(),
        code=body.code,
        display_name=body.displayName,
        amount_kobo=body.amountKobo,
        currency="NGN",
        interval="monthly",
        lago_plan_code=body.code,
        is_purchasable=body.isPurchasable,
        rank=body.rank,
        active=body.active,
        blurb=body.blurb,
    )
    session.add(plan)
    # Attach every known feature as disabled until edited.
    for feature in (await session.execute(select(BillingFeature))).scalars().all():
        session.add(
            BillingPlanEntitlement(
                id=cuid(),
                plan_id=plan.id,
                feature_id=feature.id,
                enabled=False,
                privilege_value=False if feature.value_type == "boolean" else None,
            )
        )
    await session.flush()
    invalidate_catalog_cache()
    try:
        await get_billing_provider().sync_plan(session, plan)
    except Exception:
        logger.exception("Remote plan create failed")
    return {"ok": True, "code": plan.code}


@router.post("/features")
async def admin_create_feature(
    body: FeatureCreate,
    session: AnSession,
    _admin: Annotated[Admin, Depends(require_admin)],
):
    existing = await session.scalar(
        select(BillingFeature).where(BillingFeature.code == body.code)
    )
    if existing is not None:
        raise ApiError(409, "Feature code already exists")
    if body.gateKey:
        gate_taken = await session.scalar(
            select(BillingFeature).where(BillingFeature.gate_key == body.gateKey)
        )
        if gate_taken is not None:
            raise ApiError(409, "gateKey already used by another feature")
    if body.valueType == "select" and not body.selectOptions:
        raise ApiError(400, "select features need selectOptions")

    feature = BillingFeature(
        id=cuid(),
        code=body.code,
        name=body.name,
        value_type=body.valueType,
        select_options=body.selectOptions,
        gate_key=body.gateKey,
        description=body.description,
    )
    session.add(feature)
    await session.flush()
    for plan in (await session.execute(select(BillingPlan))).scalars().all():
        session.add(
            BillingPlanEntitlement(
                id=cuid(),
                plan_id=plan.id,
                feature_id=feature.id,
                enabled=False,
                privilege_value=False if body.valueType == "boolean" else None,
            )
        )
    await session.flush()
    invalidate_catalog_cache()
    if settings.lago_enabled:
        try:
            privileges = []
            if feature.value_type == "integer":
                privileges.append(
                    {
                        "code": "daily_requests",
                        "name": "Daily requests",
                        "value_type": "integer",
                    }
                )
            elif feature.value_type == "select":
                privileges.append(
                    {
                        "code": "level",
                        "name": "Level",
                        "value_type": "select",
                        "config": {"select_options": feature.select_options or []},
                    }
                )
            await lago_client.create_feature(
                code=feature.code,
                name=feature.name,
                description=feature.description,
                privileges=privileges or None,
            )
        except Exception:
            logger.exception("Lago feature create failed")
    return {"ok": True, "feature": _feature_payload(feature)}


@router.patch("/plans/{code}")
async def admin_patch_plan(
    code: str,
    body: PlanPatch,
    session: AnSession,
    _admin: Annotated[Admin, Depends(require_admin)],
):
    plan = await session.scalar(select(BillingPlan).where(BillingPlan.code == code))
    if plan is None:
        raise ApiError(404, "Plan not found")
    if body.amountKobo is not None:
        plan.amount_kobo = body.amountKobo
    if body.displayName is not None:
        plan.display_name = body.displayName
    if body.blurb is not None:
        plan.blurb = body.blurb
    if body.isPurchasable is not None:
        plan.is_purchasable = body.isPurchasable
    if body.active is not None:
        plan.active = body.active
    if body.rank is not None:
        plan.rank = body.rank
    await session.flush()
    invalidate_catalog_cache()
    try:
        # Re-sync: clear cached Paystack code if amount changed so a new plan
        # can be created when amount differs.
        if body.amountKobo is not None and get_billing_provider().name == "paystack":
            plan.paystack_plan_code = None
        await get_billing_provider().sync_plan(session, plan)
    except Exception:
        logger.exception("Remote plan update failed")
    return {"ok": True, "code": plan.code}


@router.patch("/plans/{code}/entitlements/{feature_code}")
async def admin_patch_entitlement(
    code: str,
    feature_code: str,
    body: EntitlementPatch,
    session: AnSession,
    _admin: Annotated[Admin, Depends(require_admin)],
):
    plan = await session.scalar(select(BillingPlan).where(BillingPlan.code == code))
    feature = await session.scalar(
        select(BillingFeature).where(BillingFeature.code == feature_code)
    )
    if plan is None or feature is None:
        raise ApiError(404, "Plan or feature not found")
    row = await session.scalar(
        select(BillingPlanEntitlement).where(
            BillingPlanEntitlement.plan_id == plan.id,
            BillingPlanEntitlement.feature_id == feature.id,
        )
    )
    if row is None:
        row = BillingPlanEntitlement(
            id=cuid(),
            plan_id=plan.id,
            feature_id=feature.id,
            enabled=True,
            privilege_value=None,
        )
        session.add(row)
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.privilegeValue is not None:
        row.privilege_value = body.privilegeValue
    await session.flush()
    invalidate_catalog_cache()
    if settings.lago_enabled:
        try:
            await _push_plan_entitlements(session, plan)
        except Exception:
            logger.exception("Lago entitlement sync failed")
    return {"ok": True}


async def _push_catalog_to_provider(session: AnSession) -> None:
    provider = get_billing_provider()
    plans = (await session.execute(select(BillingPlan))).scalars().all()
    if provider.name == "lago" and settings.lago_enabled:
        await _push_catalog_to_lago(session, plans)
        return
    if provider.name == "paystack":
        for plan in plans:
            await provider.sync_plan(session, plan)


async def _push_catalog_to_lago(session: AnSession, plans=None) -> None:
    plans = plans or (await session.execute(select(BillingPlan))).scalars().all()
    features = (await session.execute(select(BillingFeature))).scalars().all()
    for feature in features:
        privileges = []
        if feature.value_type == "integer":
            privileges.append(
                {
                    "code": "daily_requests",
                    "name": "Daily requests",
                    "value_type": "integer",
                }
            )
        elif feature.value_type == "select":
            privileges.append(
                {
                    "code": "level",
                    "name": "Level",
                    "value_type": "select",
                    "config": {"select_options": feature.select_options or []},
                }
            )
        await lago_client.create_feature(
            code=feature.code,
            name=feature.name,
            description=feature.description,
            privileges=privileges or None,
        )
    for plan in plans:
        await lago_client.create_plan(
            code=plan.lago_plan_code or plan.code,
            name=plan.display_name,
            amount_cents=plan.amount_kobo,
            description=plan.blurb,
        )
        await _push_plan_entitlements(session, plan)


async def _push_plan_entitlements(session: AnSession, plan: BillingPlan) -> None:
    features = {
        f.id: f for f in (await session.execute(select(BillingFeature))).scalars().all()
    }
    rows = (
        (
            await session.execute(
                select(BillingPlanEntitlement).where(
                    BillingPlanEntitlement.plan_id == plan.id
                )
            )
        )
        .scalars()
        .all()
    )
    entitlements: dict[str, Any] = {}
    for row in rows:
        feature = features.get(row.feature_id)
        if feature is None or not row.enabled:
            continue
        if feature.value_type == "boolean":
            entitlements[feature.code] = {}
        elif feature.value_type == "integer":
            entitlements[feature.code] = {"daily_requests": row.privilege_value}
        elif feature.value_type == "select":
            entitlements[feature.code] = {"level": row.privilege_value}
        else:
            entitlements[feature.code] = {}
    if entitlements:
        await lago_client.set_plan_entitlements(
            plan.lago_plan_code or plan.code, entitlements
        )
