"""Center Pro/Team yearly Paystack checkout and activation (M4/M5).

Free is the solo (owner-only) plan. Paid mid-tier is Pro.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.logger import logger
from app.core.timeutil import utcnow
from app.database.models import Center, User
from app.database.repositories.centers import (
    center_plan_checkouts_repository,
    centers_repository,
    memberships_repository,
)
from app.domain import (
    CENTER_FREE_ACTIVE_STUDENT_LIMIT,
    CENTER_PRO_MAX_FACILITATORS,
    CENTER_PRO_YEARLY_KOBO,
    CENTER_TEAM_MAX_FACILITATORS,
    CENTER_TEAM_YEARLY_KOBO,
    center_max_facilitators,
    center_max_staff,
)
from app.services.billing.catalog import plan_by_code, seed_catalog
from app.services.billing.rules import settlement_outcome, term_end
from app.services.billing.service import CheckoutService, VerifyPaymentService
from app.services.product_events import capture


async def ensure_center_catalog(session: AsyncSession) -> None:
    """Seed student + center BillingPlan rows when the cache is cold."""
    plan = await plan_by_code(session, "center_pro")
    if plan is None:
        await seed_catalog(session)


class CenterCheckoutService:
    """Owner starts yearly Pro or Team checkout for a center."""

    def __init__(
        self,
        session: AsyncSession,
        user: User,
        center_id: str,
        plan: str = "PRO",
    ) -> None:
        self.session = session
        self.user = user
        self.center_id = center_id
        self.plan = (plan or "PRO").upper()

    async def process(self) -> dict[str, Any]:
        await ensure_center_catalog(self.session)
        return await CheckoutService(
            self.session,
            self.user,
            self.plan,
            "YEARLY",
            center_id=self.center_id,
        ).process()


class ApplyCenterChargeSuccessService:
    """Activate a pending CenterPlanCheckout and lift the center to Pro/Team."""

    def __init__(
        self, session: AsyncSession, reference: str, transaction: dict
    ) -> None:
        self.session = session
        self.reference = reference
        self.transaction = transaction

    async def process(self) -> str | None:
        row = await center_plan_checkouts_repository.by_reference_for_update(
            self.session, self.reference
        )
        if row is None:
            return None
        outcome = settlement_outcome(
            row_status=row.status,
            row_amount=row.amount_kobo,
            row_currency=row.currency,
            transaction_status=self.transaction.get("status"),
            transaction_amount=self.transaction.get("amount"),
            transaction_currency=self.transaction.get("currency"),
        )
        logger.info(
            "ApplyCenterChargeSuccess reference=%s outcome=%s center=%s plan=%s",
            self.reference,
            outcome,
            row.center_id,
            row.plan,
        )
        if outcome == "not-successful" and row.status == "PENDING":
            row.status = "FAILED"
            capture(
                row.user_id,
                "center_checkout_failed",
                {
                    "center_id": row.center_id,
                    "plan": row.plan,
                    "plan_code": f"center_{row.plan.lower()}",
                    "amount_kobo": row.amount_kobo,
                    "currency": row.currency,
                    "source": "center_checkout",
                },
            )
            return outcome
        if outcome != "activate":
            return outcome
        if row.status == "ACTIVE":
            return "already-applied"
        now = utcnow()
        end = term_end(now, row.period or "YEARLY")
        row.status = "ACTIVE"
        row.paid_at = now
        row.starts_at = now
        row.ends_at = end
        row.channel = self.transaction.get("channel")
        center = await centers_repository.by_id(self.session, row.center_id)
        if center is None:
            raise ApiError(404, "Center not found")
        await activate_center_plan(self.session, center, row.plan, end)
        capture(
            row.user_id,
            "center_subscription_activated",
            {
                "center_id": row.center_id,
                "plan": row.plan,
                "plan_code": f"center_{row.plan.lower()}",
                "period": row.period,
                "amount_kobo": row.amount_kobo,
                "currency": row.currency,
                "source": "center_checkout",
            },
            set_props={"center_plan": row.plan},
        )
        await self.session.flush()
        return "activate"


async def activate_center_plan(
    session: AsyncSession,
    center: Center,
    plan: str,
    paid_until,
) -> Center:
    """Set center.plan so Free roster cap / Team facilitator gates unlock."""
    center.plan = plan
    center.plan_paid_until = paid_until
    await session.flush()
    return center


class VerifyCenterPaymentService:
    """Client-side settle after Paystack redirect (mirrors student /billing/verify)."""

    def __init__(
        self, session: AsyncSession, user_id: str, center_id: str, reference: str
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.center_id = center_id
        self.reference = reference

    async def process(self) -> dict[str, Any]:
        center = await centers_repository.owned_by(
            self.session, self.center_id, self.user_id
        )
        if center is None:
            raise ApiError(404, "Center not found")
        row = await center_plan_checkouts_repository.by_reference(
            self.session, self.reference
        )
        if row is None or row.center_id != center.id or row.user_id != self.user_id:
            raise ApiError(404, "Checkout not found")
        if row.status == "ACTIVE":
            return {
                "outcome": "already-applied",
                "center": {
                    "id": center.id,
                    "plan": center.plan,
                    "planPaidUntil": (
                        center.plan_paid_until.isoformat()
                        if center.plan_paid_until
                        else None
                    ),
                },
            }
        transaction = await VerifyPaymentService(self.session, self.reference).process()
        if not transaction:
            raise ApiError(502, "Could not verify payment")
        outcome = await ApplyCenterChargeSuccessService(
            self.session, self.reference, transaction
        ).process()
        await self.session.refresh(center)
        return {
            "outcome": outcome or "unknown",
            "center": {
                "id": center.id,
                "plan": center.plan,
                "planPaidUntil": (
                    center.plan_paid_until.isoformat()
                    if center.plan_paid_until
                    else None
                ),
            },
        }


class GetCenterBillingService:
    def __init__(self, session: AsyncSession, user_id: str, center_id: str) -> None:
        self.session = session
        self.user_id = user_id
        self.center_id = center_id

    async def process(self) -> dict[str, Any]:
        center = await centers_repository.owned_by(
            self.session, self.center_id, self.user_id
        )
        if center is None:
            raise ApiError(404, "Center not found")
        active = await memberships_repository.active_student_count(
            self.session, center.id
        )
        facilitators = await memberships_repository.active_facilitator_count(
            self.session, center.id
        )
        await ensure_center_catalog(self.session)
        pro = await plan_by_code(self.session, "center_pro")
        team = await plan_by_code(self.session, "center_team")
        from app.domain import CENTER_ASTRA_TOKENS_PER_TURN
        from app.services.centers.tokens import ensure_token_pool, list_token_packs

        pool = await ensure_token_pool(self.session, center.id, actor_id=self.user_id)
        return {
            "billing": {
                "plan": center.plan,
                "planPaidUntil": (
                    center.plan_paid_until.isoformat()
                    if center.plan_paid_until
                    else None
                ),
                "activeStudentCount": active,
                "freeStudentLimit": CENTER_FREE_ACTIVE_STUDENT_LIMIT,
                "atFreeCap": center.plan == "FREE"
                and active + 1 >= CENTER_FREE_ACTIVE_STUDENT_LIMIT,
                "facilitatorCount": facilitators,
                "maxFacilitators": center_max_facilitators(center.plan),
                "maxStaff": center_max_staff(center.plan),
                "pro": {
                    "code": "center_pro",
                    "displayName": "Pro",
                    "amountKobo": (pro.amount_kobo if pro else CENTER_PRO_YEARLY_KOBO),
                    "interval": "yearly",
                    "purchasable": center.plan in {"FREE"},
                    "maxFacilitators": CENTER_PRO_MAX_FACILITATORS,
                    "maxStaff": 1 + CENTER_PRO_MAX_FACILITATORS,
                },
                "team": {
                    "code": "center_team",
                    "displayName": "Team",
                    "amountKobo": (
                        team.amount_kobo if team else CENTER_TEAM_YEARLY_KOBO
                    ),
                    "interval": "yearly",
                    "purchasable": center.plan in {"FREE", "PRO"},
                    "maxFacilitators": CENTER_TEAM_MAX_FACILITATORS,
                    "maxStaff": 1 + CENTER_TEAM_MAX_FACILITATORS,
                },
                "tokens": {
                    "balance": pool.balance,
                    "tokensPerTurn": CENTER_ASTRA_TOKENS_PER_TURN,
                    "packs": list_token_packs(),
                },
            }
        }
