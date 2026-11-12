"""Local billing catalog: seed data, cache, and entitlement resolution."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import cuid
from app.database.models.billing_catalog import (
    BillingFeature,
    BillingPlan,
    BillingPlanEntitlement,
)

# Legacy require_feature keys → catalog feature codes.
GATE_TO_FEATURE = {
    "flashcards": "flashcards",
    "studyPlanner": "study_plan",
    "premiumLibrary": "classroom_pdf",
    "advancedAnalytics": "performance",
    "aiTutor": "ai_tutor",
}

PLAN_CODE_TO_TIER = {
    "freemium": "FREEMIUM",
    "standard": "STANDARD",
    "premium": "PREMIUM",
}
TIER_TO_PLAN_CODE = {v: k for k, v in PLAN_CODE_TO_TIER.items()}

_CACHE_TTL = 60.0
_CATALOG_VERSION_KEY = "billing:catalog:version"
_cache: dict[str, Any] = {
    "at": 0.0,
    "version": 0,
    "plans": None,
    "by_code": None,
    "gates": None,
}


def _redis_catalog_version() -> int | None:
    """Current shared catalog version, or None when Upstash is unavailable."""
    import httpx

    from app.core.config import settings

    url = settings.upstash_redis_rest_url
    token = settings.upstash_redis_rest_token
    if not url or not token:
        return None
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=["GET", _CATALOG_VERSION_KEY],
            timeout=2.0,
        )
        response.raise_for_status()
        result = response.json().get("result")
        if result is None:
            return 0
        return int(result)
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return None


def _redis_bump_catalog_version() -> int | None:
    import httpx

    from app.core.config import settings

    url = settings.upstash_redis_rest_url
    token = settings.upstash_redis_rest_token
    if not url or not token:
        return None
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=["INCR", _CATALOG_VERSION_KEY],
            timeout=2.0,
        )
        response.raise_for_status()
        return int(response.json()["result"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class PlanSnapshot:
    code: str
    display_name: str
    amount_kobo: int
    currency: str
    interval: str
    is_purchasable: bool
    rank: int
    blurb: str | None
    entitlements: dict[str, dict[str, Any]]  # feature_code → {enabled, value, gate_key}


SEED_PLANS = [
    {
        "code": "freemium",
        "display_name": "Free",
        "amount_kobo": 0,
        "is_purchasable": False,
        "rank": 0,
        "blurb": "To get started — first month includes Standard free",
    },
    {
        "code": "standard",
        "display_name": "Standard",
        "amount_kobo": 300_000,
        "is_purchasable": True,
        "rank": 1,
        "blurb": "Explanations, study plan, and more tutor capacity",
    },
    {
        "code": "premium",
        "display_name": "Premium",
        "amount_kobo": 1_000_000,
        "is_purchasable": True,
        "rank": 2,
        "blurb": "AI explanations, subject analytics, and classroom PDFs",
    },
]

SEED_FEATURES = [
    {
        "code": "past_questions",
        "name": "Past questions",
        "value_type": "select",
        "select_options": ["answers", "explanation", "ai_explanation"],
        "gate_key": None,
        "description": "Depth of past-question feedback",
    },
    {
        "code": "performance",
        "name": "Performance",
        "value_type": "select",
        "select_options": ["overview", "by_subject"],
        "gate_key": "advancedAnalytics",
        "description": "Performance analytics scope",
    },
    {
        "code": "ai_tutor",
        "name": "AI tutor",
        "value_type": "integer",
        "select_options": None,
        "gate_key": "aiTutor",
        "description": "Daily AI tutor request allowance",
    },
    {
        "code": "study_plan",
        "name": "Study plan",
        "value_type": "boolean",
        "select_options": None,
        "gate_key": "studyPlanner",
        "description": "Personalized study planner",
    },
    {
        "code": "class_notes",
        "name": "Class notes",
        "value_type": "boolean",
        "select_options": None,
        "gate_key": None,
        "description": "Classroom lesson notes",
    },
    {
        "code": "classroom_pdf",
        "name": "Classroom PDFs",
        "value_type": "boolean",
        "select_options": None,
        "gate_key": "premiumLibrary",
        "description": "Premium library and PDF materials",
    },
    {
        "code": "flashcards",
        "name": "Flashcards",
        "value_type": "boolean",
        "select_options": None,
        "gate_key": "flashcards",
        "description": "Spaced-repetition flashcards",
    },
]

# feature_code → privilege value per plan_code (enabled implied by presence).
SEED_ENTITLEMENTS: dict[str, dict[str, Any]] = {
    "freemium": {
        "past_questions": "answers",
        "performance": "overview",
        "ai_tutor": 2,
        "study_plan": False,
        "class_notes": True,
        "classroom_pdf": False,
        "flashcards": False,
    },
    "standard": {
        "past_questions": "explanation",
        "performance": "overview",
        "ai_tutor": 20,
        "study_plan": True,
        "class_notes": True,
        "classroom_pdf": False,
        "flashcards": True,
    },
    "premium": {
        "past_questions": "ai_explanation",
        "performance": "by_subject",
        "ai_tutor": 60,
        "study_plan": True,
        "class_notes": True,
        "classroom_pdf": True,
        "flashcards": True,
    },
}


def invalidate_catalog_cache() -> None:
    """Drop local cache and bump the shared Redis version (sync; admin path)."""
    bumped = _redis_bump_catalog_version()
    _cache["at"] = 0.0
    if bumped is not None:
        _cache["version"] = bumped
    else:
        _cache["version"] = (_cache.get("version") or 0) + 1
    _cache["plans"] = None
    _cache["by_code"] = None
    _cache["gates"] = None


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        return value.lower() not in {"", "false", "0", "off", "none"}
    return bool(value)


async def seed_catalog(session: AsyncSession) -> dict[str, int]:
    """Idempotent upsert of plans, features, and entitlements."""
    plans_by_code: dict[str, BillingPlan] = {}
    for row in SEED_PLANS:
        existing = await session.scalar(
            select(BillingPlan).where(BillingPlan.code == row["code"])
        )
        if existing is None:
            existing = BillingPlan(
                id=cuid(),
                code=row["code"],
                display_name=row["display_name"],
                amount_kobo=row["amount_kobo"],
                currency="NGN",
                interval="monthly",
                lago_plan_code=row["code"],
                is_purchasable=row["is_purchasable"],
                rank=row["rank"],
                blurb=row["blurb"],
                active=True,
            )
            session.add(existing)
        else:
            existing.display_name = row["display_name"]
            existing.amount_kobo = row["amount_kobo"]
            existing.is_purchasable = row["is_purchasable"]
            existing.rank = row["rank"]
            existing.blurb = row["blurb"]
            existing.lago_plan_code = row["code"]
            existing.active = True
        plans_by_code[row["code"]] = existing

    await session.flush()

    features_by_code: dict[str, BillingFeature] = {}
    for row in SEED_FEATURES:
        existing = await session.scalar(
            select(BillingFeature).where(BillingFeature.code == row["code"])
        )
        if existing is None:
            existing = BillingFeature(
                id=cuid(),
                code=row["code"],
                name=row["name"],
                value_type=row["value_type"],
                select_options=row["select_options"],
                description=row["description"],
                gate_key=row["gate_key"],
            )
            session.add(existing)
        else:
            existing.name = row["name"]
            existing.value_type = row["value_type"]
            existing.select_options = row["select_options"]
            existing.description = row["description"]
            existing.gate_key = row["gate_key"]
        features_by_code[row["code"]] = existing

    await session.flush()

    entitlement_count = 0
    for plan_code, features in SEED_ENTITLEMENTS.items():
        plan = plans_by_code[plan_code]
        for feature_code, value in features.items():
            feature = features_by_code[feature_code]
            existing = await session.scalar(
                select(BillingPlanEntitlement).where(
                    BillingPlanEntitlement.plan_id == plan.id,
                    BillingPlanEntitlement.feature_id == feature.id,
                )
            )
            enabled = _boolish(value) if feature.value_type == "boolean" else True
            if existing is None:
                session.add(
                    BillingPlanEntitlement(
                        id=cuid(),
                        plan_id=plan.id,
                        feature_id=feature.id,
                        enabled=enabled,
                        privilege_value=value,
                    )
                )
            else:
                existing.enabled = enabled
                existing.privilege_value = value
            entitlement_count += 1

    await session.flush()
    from app.services.centers.plans import seed_center_catalog

    center_plans = await seed_center_catalog(session)
    invalidate_catalog_cache()
    return {
        "plans": len(plans_by_code),
        "features": len(features_by_code),
        "entitlements": entitlement_count,
        "centerPlans": center_plans,
    }


async def load_catalog(session: AsyncSession) -> list[PlanSnapshot]:
    """Serve catalog from memory within TTL; Redis version only after TTL expiry.

    Avoids a synchronous Upstash round-trip on every can_gate / privilege read.
    When Redis is checked, it runs in a worker thread so the event loop stays free.
    """
    import asyncio

    now = time.monotonic()
    if _cache["plans"] is not None and now - _cache["at"] < _CACHE_TTL:
        return _cache["plans"]

    # TTL expired: compare shared version before hitting the DB.
    remote_version = await asyncio.to_thread(_redis_catalog_version)
    if (
        _cache["plans"] is not None
        and remote_version is not None
        and remote_version == (_cache.get("version") or 0)
    ):
        _cache["at"] = now
        return _cache["plans"]

    result = await session.execute(
        select(BillingPlan)
        .where(BillingPlan.active.is_(True))
        .order_by(BillingPlan.rank)
    )
    plans = list(result.scalars().all())
    feature_rows = {
        f.id: f for f in (await session.execute(select(BillingFeature))).scalars().all()
    }
    entitlement_rows = (
        (await session.execute(select(BillingPlanEntitlement))).scalars().all()
    )

    by_plan: dict[str, dict[str, dict[str, Any]]] = {p.id: {} for p in plans}
    for row in entitlement_rows:
        feature = feature_rows.get(row.feature_id)
        if feature is None or row.plan_id not in by_plan:
            continue
        by_plan[row.plan_id][feature.code] = {
            "enabled": row.enabled,
            "value": row.privilege_value,
            "gate_key": feature.gate_key,
            "value_type": feature.value_type,
        }

    snapshots = [
        PlanSnapshot(
            code=plan.code,
            display_name=plan.display_name,
            amount_kobo=plan.amount_kobo,
            currency=plan.currency,
            interval=plan.interval,
            is_purchasable=plan.is_purchasable,
            rank=plan.rank,
            blurb=plan.blurb,
            entitlements=by_plan.get(plan.id, {}),
        )
        for plan in plans
    ]
    _cache["at"] = now
    _cache["version"] = (
        remote_version if remote_version is not None else (_cache.get("version") or 0)
    )
    _cache["plans"] = snapshots
    _cache["by_code"] = {p.code: p for p in snapshots}
    gates: dict[str, dict[str, Any]] = {}
    for plan in snapshots:
        for _feature_code, meta in plan.entitlements.items():
            gate = meta.get("gate_key")
            if not gate:
                continue
            gates.setdefault(gate, {})
            gates[gate][plan.code] = meta
    _cache["gates"] = gates
    return snapshots


async def plan_by_code(session: AsyncSession, code: str) -> PlanSnapshot | None:
    await load_catalog(session)
    return (_cache["by_code"] or {}).get(code)


def plan_code_for_tier(tier: str) -> str:
    return TIER_TO_PLAN_CODE.get(tier, "freemium")


def tier_for_plan_code(code: str) -> str:
    return PLAN_CODE_TO_TIER.get(code, "FREEMIUM")


async def privilege_for_tier(
    session: AsyncSession, tier: str, feature_code: str
) -> dict[str, Any] | None:
    plan = await plan_by_code(session, plan_code_for_tier(tier))
    if plan is None:
        return None
    return plan.entitlements.get(feature_code)


async def catalog_is_empty(session: AsyncSession) -> bool:
    """True when there are no active BillingPlan rows (static fallback allowed)."""
    plans = await load_catalog(session)
    return not plans


async def can_gate(session: AsyncSession, tier: str, gate_key: str) -> bool:
    """Whether the user's plan unlocks a legacy gate key."""
    feature_code = GATE_TO_FEATURE.get(gate_key, gate_key)
    meta = await privilege_for_tier(session, tier, feature_code)
    if meta is None:
        return False
    if not meta.get("enabled", False):
        return False
    value = meta.get("value")
    value_type = meta.get("value_type")
    if value_type == "boolean":
        return _boolish(value)
    if value_type == "integer":
        return int(value or 0) > 0
    if value_type == "select":
        # performance / past_questions: presence means at least base access.
        if feature_code == "performance":
            return value == "by_subject" if gate_key == "advancedAnalytics" else True
        return True
    return True


async def required_tier_for_gate(session: AsyncSession, gate_key: str) -> str:
    await load_catalog(session)
    feature_code = GATE_TO_FEATURE.get(gate_key, gate_key)
    plans: list[PlanSnapshot] = _cache["plans"] or []
    for plan in sorted(plans, key=lambda p: p.rank):
        meta = plan.entitlements.get(feature_code)
        if not meta or not meta.get("enabled"):
            continue
        value = meta.get("value")
        value_type = meta.get("value_type")
        ok = True
        if value_type == "boolean":
            ok = _boolish(value)
        elif value_type == "integer":
            ok = int(value or 0) > 0
        elif feature_code == "performance" and gate_key == "advancedAnalytics":
            ok = value == "by_subject"
        if ok:
            return tier_for_plan_code(plan.code)
    return "PREMIUM"


async def ai_tutor_daily_limit(session: AsyncSession, tier: str) -> int:
    meta = await privilege_for_tier(session, tier, "ai_tutor")
    if meta is None or not meta.get("enabled"):
        return 0
    try:
        return int(meta.get("value") or 0)
    except (TypeError, ValueError):
        return 0


async def past_questions_detail(session: AsyncSession, tier: str) -> str:
    meta = await privilege_for_tier(session, tier, "past_questions")
    if meta is None or not meta.get("enabled", False):
        return "answers"
    return str(meta.get("value") or "answers")


async def performance_scope(session: AsyncSession, tier: str) -> str:
    meta = await privilege_for_tier(session, tier, "performance")
    if meta is None or not meta.get("enabled", False):
        return "overview"
    return str(meta.get("value") or "overview")


def public_plan_payload(plan: PlanSnapshot) -> dict:
    features = []
    for code, meta in plan.entitlements.items():
        features.append(
            {
                "code": code,
                "enabled": meta.get("enabled", False),
                "value": meta.get("value"),
                "gateKey": meta.get("gate_key"),
            }
        )
    return {
        "code": plan.code,
        "tier": tier_for_plan_code(plan.code),
        "displayName": plan.display_name,
        "amountKobo": plan.amount_kobo,
        "currency": plan.currency,
        "interval": plan.interval,
        "isPurchasable": plan.is_purchasable,
        "rank": plan.rank,
        "blurb": plan.blurb,
        "features": features,
    }
