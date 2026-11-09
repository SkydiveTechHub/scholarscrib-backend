"""Repositories for subscriptions and Paystack events."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PaystackEvent, Subscription
from app.database.repositories.base import BaseRepository
from app.domain import TIER_RANK


class SubscriptionRepository(BaseRepository[Subscription]):
    model = Subscription

    async def for_user(self, session: AsyncSession, user_id: str) -> list[Subscription]:
        return await self.list_where(session, Subscription.user_id == user_id)

    async def by_reference(
        self, session: AsyncSession, reference: str
    ) -> Subscription | None:
        return await self.first(session, Subscription.reference == reference)

    async def by_reference_for_update(
        self, session: AsyncSession, reference: str
    ) -> Subscription | None:
        """Lock the row for settlement so verify + webhook cannot race."""
        return await session.scalar(
            select(Subscription)
            .where(Subscription.reference == reference)
            .with_for_update()
        )

    async def latest_live_end(
        self, session: AsyncSession, user_id: str, now: datetime
    ) -> datetime | None:
        return await session.scalar(
            select(Subscription.ends_at)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == "ACTIVE",
                Subscription.ends_at > now,
            )
            .order_by(Subscription.ends_at.desc())
        )

    async def abandon_pending(self, session: AsyncSession, user_id: str) -> None:
        await self.update_where(
            session,
            {"status": "ABANDONED"},
            Subscription.user_id == user_id,
            Subscription.status == "PENDING",
        )

    async def activate(
        self,
        session: AsyncSession,
        subscription_id: str,
        values: dict,
    ):
        return await self.update_where(
            session,
            values,
            Subscription.id == subscription_id,
            Subscription.status.in_(("PENDING", "ABANDONED")),
        )

    async def revoke_active(self, session: AsyncSession, user_id: str) -> None:
        await self.update_where(
            session,
            {"status": "REVOKED"},
            Subscription.user_id == user_id,
            Subscription.status == "ACTIVE",
        )

    async def end_active_below(
        self, session: AsyncSession, user_id: str, tier: str, now: datetime
    ) -> int:
        """Cut short lower-tier ACTIVE terms so an upgrade becomes live immediately."""
        target_rank = TIER_RANK.get(tier, 0)
        ended = 0
        for row in await self.for_user(session, user_id):
            if row.status != "ACTIVE":
                continue
            if TIER_RANK.get(row.tier, 0) >= target_rank:
                continue
            if row.ends_at is None or row.ends_at > now:
                row.ends_at = now
                ended += 1
        return ended


class PaystackEventRepository(BaseRepository[PaystackEvent]):
    model = PaystackEvent


subscriptions_repository = SubscriptionRepository()
paystack_events_repository = PaystackEventRepository()
