"""Raw inputs for the admin overview's analytics panel.

The rules (windows, month keys, subscriber counts, renewals) live in the
frontend's `admin-analytics`; this only counts rows. Every window boundary
arrives as a parameter so there is a single definition of "this month so far".
"""

from datetime import datetime

from sqlalchemy import case, distinct, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import as_utc
from app.database.models.assessment import AssessmentAttempt
from app.database.models.billing import Subscription
from app.database.models.identity import User
from app.database.models.learning import LearningEvent


def _month_bucket(col):
    """Bucket a timestamptz column into a 'YYYY-MM' string in Lagos time."""
    return func.to_char(
        func.timezone("Africa/Lagos", col),
        "YYYY-MM",
    )


def _iso(value: datetime | None) -> str | None:
    return as_utc(value).isoformat() if value else None


class GetAdminAnalyticsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        since: datetime,
        current_start: datetime,
        previous_start: datetime,
        previous_end: datetime,
        last_month: datetime,
        cohort_start: datetime,
    ) -> None:
        self.session = session
        self.now = as_utc(now)
        self.since = as_utc(since)
        self.current_start = as_utc(current_start)
        self.previous_start = as_utc(previous_start)
        self.previous_end = as_utc(previous_end)
        self.last_month = as_utc(last_month)
        self.cohort_start = as_utc(cohort_start)

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    async def _users(self) -> dict:
        stmt = select(
            func.count().label("total"),
            func.count(case((User.created_at < self.since, 1))).label("before"),
            func.count(case((User.created_at < self.last_month, 1))).label("lastMonth"),
            func.count(
                case(
                    (
                        (User.created_at >= self.current_start)
                        & (User.created_at < self.now),
                        1,
                    )
                )
            ).label("cur"),
            func.count(
                case(
                    (
                        (User.created_at >= self.previous_start)
                        & (User.created_at < self.previous_end),
                        1,
                    )
                )
            ).label("prev"),
        ).where(User.role == "STUDENT")

        row = (await self.session.execute(stmt)).mappings().one()
        return dict(row)

    async def _signups_by_month(self) -> list[dict]:
        stmt = (
            select(
                _month_bucket(User.created_at).label("month"),
                func.count().label("n"),
            )
            .where(User.role == "STUDENT", User.created_at >= self.since)
            .group_by("month")
        )
        result = await self.session.execute(stmt)
        return [dict(r) for r in result.mappings().all()]

    # ------------------------------------------------------------------
    # Learners
    # ------------------------------------------------------------------

    async def _learners(self) -> dict:
        stmt = select(
            func.count(
                case(
                    (
                        LearningEvent.occurred_at >= self.current_start,
                        LearningEvent.student_id,
                    ),
                    else_=None,
                ).distinct()
            ).label("cur"),
            func.count(
                case(
                    (
                        LearningEvent.occurred_at < self.previous_end,
                        LearningEvent.student_id,
                    ),
                    else_=None,
                ).distinct()
            ).label("prev"),
        ).where(
            LearningEvent.occurred_at >= self.previous_start,
            LearningEvent.occurred_at < self.now,
        )

        row = (await self.session.execute(stmt)).mappings().one()
        return dict(row)

    async def _learners_by_month(self) -> list[dict]:
        stmt = (
            select(
                _month_bucket(LearningEvent.occurred_at).label("month"),
                func.count(distinct(LearningEvent.student_id)).label("n"),
            )
            .where(LearningEvent.occurred_at >= self.since)
            .group_by("month")
        )
        result = await self.session.execute(stmt)
        return [dict(r) for r in result.mappings().all()]

    # ------------------------------------------------------------------
    # Assessments
    # ------------------------------------------------------------------

    async def _assessments(self) -> dict:
        stmt = select(
            func.count(
                case((AssessmentAttempt.completed_at >= self.current_start, 1))
            ).label("cur"),
            func.count(
                case((AssessmentAttempt.completed_at < self.previous_end, 1))
            ).label("prev"),
        ).where(
            AssessmentAttempt.status == "COMPLETED",
            AssessmentAttempt.completed_at >= self.previous_start,
            AssessmentAttempt.completed_at < self.now,
        )

        row = (await self.session.execute(stmt)).mappings().one()
        return dict(row)

    async def _assessments_by_month(self) -> list[dict]:
        stmt = (
            select(
                _month_bucket(AssessmentAttempt.completed_at).label("month"),
                func.count().label("n"),
            )
            .where(
                AssessmentAttempt.status == "COMPLETED",
                AssessmentAttempt.completed_at >= self.since,
            )
            .group_by("month")
        )
        result = await self.session.execute(stmt)
        return [dict(r) for r in result.mappings().all()]

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    async def _subscriptions(self) -> list[dict]:
        stmt = (
            select(
                Subscription.user_id,
                Subscription.tier,
                Subscription.status,
                Subscription.source,
                Subscription.amount_kobo,
                Subscription.paid_at,
                Subscription.starts_at,
                Subscription.ends_at,
            )
            .join(User, User.id == Subscription.user_id)
            .where(
                Subscription.status == "ACTIVE",
                User.role == "STUDENT",
                or_(
                    Subscription.ends_at >= self.since,
                    Subscription.paid_at >= self.since,
                ),
            )
        )
        result = await self.session.execute(stmt)
        rows = [dict(r) for r in result.mappings().all()]
        for row in rows:
            for key in ("tier", "status", "source"):
                if row[key] is not None:
                    row[key] = str(row[key])
            row["paidAt"] = _iso(row.pop("paid_at", None))
            row["startsAt"] = _iso(row.pop("starts_at", None))
            row["endsAt"] = _iso(row.pop("ends_at", None))
            row["userId"] = row.pop("user_id", None)
            row["amountKobo"] = row.pop("amount_kobo", None)
        return rows

    # ------------------------------------------------------------------
    # Breakdowns
    # ------------------------------------------------------------------

    def _live_subscription_exists(self):
        """Correlated subquery: user has an active subscription right now."""
        return exists(
            select(1).where(
                Subscription.user_id == User.id,
                Subscription.status == "ACTIVE",
                Subscription.ends_at > self.now,
                or_(
                    Subscription.starts_at.is_(None),
                    Subscription.starts_at <= self.now,
                ),
            )
        )

    async def _class_levels(self) -> list[dict]:
        live = self._live_subscription_exists()
        stmt = (
            select(
                User.class_level.label("label"),
                func.count().label("students"),
                func.count(case((live, 1))).label("subscribers"),
            )
            .where(User.role == "STUDENT")
            .group_by(User.class_level)
        )
        result = await self.session.execute(stmt)
        return [dict(r) for r in result.mappings().all()]

    async def _states(self) -> list[dict]:
        live = self._live_subscription_exists()
        stmt = (
            select(
                func.nullif(User.state, "").label("label"),
                func.count().label("students"),
                func.count(case((live, 1))).label("subscribers"),
            )
            .where(User.role == "STUDENT")
            .group_by(User.state)
        )
        result = await self.session.execute(stmt)
        return [dict(r) for r in result.mappings().all()]

    # ------------------------------------------------------------------
    # Funnel
    # ------------------------------------------------------------------

    async def _funnel(self) -> dict:
        has_learning = exists(select(1).where(LearningEvent.student_id == User.id))
        has_assessment = exists(
            select(1).where(
                AssessmentAttempt.student_id == User.id,
                AssessmentAttempt.status == "COMPLETED",
            )
        )
        has_paid = exists(
            select(1).where(
                Subscription.user_id == User.id,
                Subscription.source == "PAYSTACK",
                Subscription.status == "ACTIVE",
            )
        )

        stmt = select(
            func.count().label("signedUp"),
            func.count(case((has_learning, 1))).label("practised"),
            func.count(case((has_assessment, 1))).label("assessed"),
            func.count(case((has_paid, 1))).label("paid"),
        ).where(
            User.role == "STUDENT",
            User.created_at >= self.cohort_start,
        )

        row = (await self.session.execute(stmt)).mappings().one()
        return dict(row)

    # ------------------------------------------------------------------
    # Entrypoint
    # ------------------------------------------------------------------

    async def process(self) -> dict:
        return {
            "users": await self._users(),
            "signupsByMonth": await self._signups_by_month(),
            "learners": await self._learners(),
            "learnersByMonth": await self._learners_by_month(),
            "assessments": await self._assessments(),
            "assessmentsByMonth": await self._assessments_by_month(),
            "subscriptions": await self._subscriptions(),
            "classLevels": await self._class_levels(),
            "states": await self._states(),
            "funnel": await self._funnel(),
        }
