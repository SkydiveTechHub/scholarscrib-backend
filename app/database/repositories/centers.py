"""Repositories for Tutor Centers roster / invite tables."""

from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Assignment,
    AssignmentTarget,
    Center,
    CenterAbuseReport,
    CenterGroup,
    CenterGroupMember,
    CenterInvite,
    CenterMembership,
    CenterPlanCheckout,
    CenterTokenCheckout,
    CenterTokenLedger,
    CenterTokenPool,
    Recommendation,
    TutorAccessAudit,
    TutorWallet,
    TutorWalletLedger,
    TutorWithdrawRequest,
    User,
)
from app.database.repositories.base import BaseRepository


class CenterRepository(BaseRepository[Center]):
    model = Center

    async def for_owner(
        self, session: AsyncSession, owner_id: str, *, include_archived: bool = False
    ) -> list[Center]:
        criteria = [Center.owner_id == owner_id]
        if not include_archived:
            criteria.append(Center.archived_at.is_(None))
        return await self.list_where(
            session, *criteria, order_by=(Center.created_at.desc(),)
        )

    async def owned_by(
        self, session: AsyncSession, center_id: str, owner_id: str
    ) -> Center | None:
        return await self.first(
            session,
            Center.id == center_id,
            Center.owner_id == owner_id,
            Center.archived_at.is_(None),
        )

    async def for_owner_ids(
        self, session: AsyncSession, owner_ids: list[str]
    ) -> list[Center]:
        if not owner_ids:
            return []
        return await self.list_where(
            session,
            Center.owner_id.in_(owner_ids),
            Center.archived_at.is_(None),
            order_by=(Center.created_at.desc(),),
        )

    async def for_staff_user(
        self, session: AsyncSession, user_id: str
    ) -> list[tuple[Center, CenterMembership]]:
        """Centers where the tutor is an active owner or facilitator."""
        statement = (
            select(Center, CenterMembership)
            .join(
                CenterMembership,
                CenterMembership.center_id == Center.id,
            )
            .where(
                CenterMembership.user_id == user_id,
                CenterMembership.status == "ACTIVE",
                CenterMembership.kind.in_(("OWNER", "FACILITATOR")),
                Center.archived_at.is_(None),
            )
            .order_by(Center.created_at.desc())
        )
        result = await session.execute(statement)
        return [(row[0], row[1]) for row in result.all()]


class CenterMembershipRepository(BaseRepository[CenterMembership]):
    model = CenterMembership

    async def by_center_user(
        self, session: AsyncSession, center_id: str, user_id: str
    ) -> CenterMembership | None:
        return await self.first(
            session,
            CenterMembership.center_id == center_id,
            CenterMembership.user_id == user_id,
        )

    async def active_student_count(self, session: AsyncSession, center_id: str) -> int:
        return await self.count(
            session,
            CenterMembership.center_id == center_id,
            CenterMembership.kind == "STUDENT",
            CenterMembership.status == "ACTIVE",
        )

    async def active_facilitator_count(
        self, session: AsyncSession, center_id: str
    ) -> int:
        return await self.count(
            session,
            CenterMembership.center_id == center_id,
            CenterMembership.kind == "FACILITATOR",
            CenterMembership.status == "ACTIVE",
        )

    async def facilitators_for_center(
        self, session: AsyncSession, center_id: str
    ) -> list[tuple[CenterMembership, User]]:
        statement = (
            select(CenterMembership, User)
            .join(User, User.id == CenterMembership.user_id)
            .where(
                CenterMembership.center_id == center_id,
                CenterMembership.kind == "FACILITATOR",
                CenterMembership.status.in_(("ACTIVE", "INVITED", "REMOVED")),
            )
            .order_by(
                CenterMembership.status.asc(),
                CenterMembership.joined_at.desc().nulls_last(),
                CenterMembership.created_at.desc(),
            )
        )
        result = await session.execute(statement)
        return [(row[0], row[1]) for row in result.all()]

    async def active_facilitators_for_centers(
        self, session: AsyncSession, center_ids: list[str]
    ) -> list[CenterMembership]:
        if not center_ids:
            return []
        return await self.list_where(
            session,
            CenterMembership.center_id.in_(center_ids),
            CenterMembership.kind == "FACILITATOR",
            CenterMembership.status == "ACTIVE",
        )

    async def roster_rows(
        self, session: AsyncSession, center_id: str
    ) -> list[tuple[CenterMembership, User]]:
        statement = (
            select(CenterMembership, User)
            .join(User, User.id == CenterMembership.user_id)
            .where(
                CenterMembership.center_id == center_id,
                CenterMembership.kind == "STUDENT",
            )
            .order_by(
                CenterMembership.status.asc(),
                CenterMembership.joined_at.desc().nulls_last(),
                CenterMembership.created_at.desc(),
            )
        )
        result = await session.execute(statement)
        return [(row[0], row[1]) for row in result.all()]

    async def for_student(
        self, session: AsyncSession, user_id: str
    ) -> list[CenterMembership]:
        return await self.list_where(
            session,
            CenterMembership.user_id == user_id,
            CenterMembership.kind == "STUDENT",
            order_by=(CenterMembership.joined_at.desc().nulls_last(),),
        )

    async def active_student_memberships_oldest_first(
        self, session: AsyncSession, user_id: str
    ) -> list[CenterMembership]:
        """D12: first-accepted-still-active membership wins attribution."""
        return await self.list_where(
            session,
            CenterMembership.user_id == user_id,
            CenterMembership.kind == "STUDENT",
            CenterMembership.status == "ACTIVE",
            order_by=(
                CenterMembership.joined_at.asc().nulls_last(),
                CenterMembership.created_at.asc(),
            ),
        )


class CenterInviteRepository(BaseRepository[CenterInvite]):
    model = CenterInvite

    async def by_code_or_token(
        self, session: AsyncSession, value: str
    ) -> CenterInvite | None:
        return await self.first(
            session,
            or_(CenterInvite.code == value, CenterInvite.token == value),
        )

    async def by_code(self, session: AsyncSession, code: str) -> CenterInvite | None:
        return await self.first(session, CenterInvite.code == code)

    async def for_center(
        self,
        session: AsyncSession,
        center_id: str,
        *,
        active_only: bool = False,
        kind: str | None = None,
    ) -> list[CenterInvite]:
        criteria = [CenterInvite.center_id == center_id]
        if active_only:
            criteria.append(CenterInvite.revoked_at.is_(None))
        if kind is not None:
            criteria.append(CenterInvite.kind == kind)
        return await self.list_where(
            session, *criteria, order_by=(CenterInvite.created_at.desc(),)
        )

    async def open_facilitator_invite_count(
        self, session: AsyncSession, center_id: str
    ) -> int:
        """Open facilitator invites that still have remaining uses (seat holds)."""
        now = datetime.now(UTC)
        rows = await self.list_where(
            session,
            CenterInvite.center_id == center_id,
            CenterInvite.kind == "FACILITATOR",
            CenterInvite.revoked_at.is_(None),
        )
        open_count = 0
        for invite in rows:
            if invite.expires_at is not None and invite.expires_at <= now:
                continue
            if invite.use_limit is not None and invite.use_count >= invite.use_limit:
                continue
            open_count += 1
        return open_count

    async def code_taken(self, session: AsyncSession, code: str) -> bool:
        return (
            await session.scalar(
                select(func.count())
                .select_from(CenterInvite)
                .where(CenterInvite.code == code)
            )
            or 0
        ) > 0


class CenterGroupRepository(BaseRepository[CenterGroup]):
    model = CenterGroup

    async def for_center(
        self, session: AsyncSession, center_id: str
    ) -> list[CenterGroup]:
        return await self.list_where(
            session,
            CenterGroup.center_id == center_id,
            order_by=(CenterGroup.name.asc(),),
        )

    async def by_center_name(
        self, session: AsyncSession, center_id: str, name: str
    ) -> CenterGroup | None:
        return await self.first(
            session, CenterGroup.center_id == center_id, CenterGroup.name == name
        )


class CenterGroupMemberRepository(BaseRepository[CenterGroupMember]):
    model = CenterGroupMember

    async def for_group(
        self, session: AsyncSession, group_id: str
    ) -> list[CenterGroupMember]:
        return await self.list_where(session, CenterGroupMember.group_id == group_id)

    async def by_group_membership(
        self, session: AsyncSession, group_id: str, membership_id: str
    ) -> CenterGroupMember | None:
        return await self.first(
            session,
            CenterGroupMember.group_id == group_id,
            CenterGroupMember.membership_id == membership_id,
        )

    async def membership_ids_for_groups(
        self, session: AsyncSession, group_ids: list[str]
    ) -> list[CenterGroupMember]:
        if not group_ids:
            return []
        return await self.list_where(session, CenterGroupMember.group_id.in_(group_ids))


class AssignmentRepository(BaseRepository[Assignment]):
    model = Assignment

    async def for_center(
        self, session: AsyncSession, center_id: str
    ) -> list[Assignment]:
        return await self.list_where(
            session,
            Assignment.center_id == center_id,
            order_by=(Assignment.created_at.desc(),),
        )

    async def by_center(
        self, session: AsyncSession, center_id: str, assignment_id: str
    ) -> Assignment | None:
        return await self.first(
            session,
            Assignment.id == assignment_id,
            Assignment.center_id == center_id,
        )

    async def due_between(
        self,
        session: AsyncSession,
        *,
        status: str,
        due_from: datetime,
        due_to: datetime,
        limit: int = 200,
    ) -> list[Assignment]:
        return await self.list_where(
            session,
            Assignment.status == status,
            Assignment.due_at.is_not(None),
            Assignment.due_at >= due_from,
            Assignment.due_at < due_to,
            limit=limit,
            order_by=(Assignment.due_at.asc(),),
        )

    async def open_for_centers(
        self, session: AsyncSession, center_ids: list[str]
    ) -> list[Assignment]:
        if not center_ids:
            return []
        return await self.list_where(
            session,
            Assignment.center_id.in_(center_ids),
            Assignment.status.in_(("OPEN", "SCHEDULED")),
            order_by=(Assignment.created_at.desc(),),
        )


class AssignmentTargetRepository(BaseRepository[AssignmentTarget]):
    model = AssignmentTarget

    async def for_assignment(
        self, session: AsyncSession, assignment_id: str
    ) -> list[AssignmentTarget]:
        return await self.list_where(
            session, AssignmentTarget.assignment_id == assignment_id
        )

    async def delete_for_assignment(
        self, session: AsyncSession, assignment_id: str
    ) -> None:
        await self.delete_where(
            session, AssignmentTarget.assignment_id == assignment_id
        )


class RecommendationRepository(BaseRepository[Recommendation]):
    model = Recommendation

    async def for_center(
        self, session: AsyncSession, center_id: str, *, limit: int = 100
    ) -> list[Recommendation]:
        return await self.list_where(
            session,
            Recommendation.center_id == center_id,
            limit=limit,
            order_by=(Recommendation.created_at.desc(),),
        )

    async def for_student(
        self, session: AsyncSession, student_id: str, *, limit: int = 100
    ) -> list[Recommendation]:
        return await self.list_where(
            session,
            Recommendation.student_id == student_id,
            limit=limit,
            order_by=(Recommendation.created_at.desc(),),
        )

    async def by_center(
        self, session: AsyncSession, center_id: str, recommendation_id: str
    ) -> Recommendation | None:
        return await self.first(
            session,
            Recommendation.id == recommendation_id,
            Recommendation.center_id == center_id,
        )


class TutorAccessAuditRepository(BaseRepository[TutorAccessAudit]):
    model = TutorAccessAudit

    async def recent(
        self,
        session: AsyncSession,
        *,
        page: int = 1,
        page_size: int = 50,
        center_id: str | None = None,
        actor_id: str | None = None,
    ) -> tuple[list[TutorAccessAudit], int]:
        criteria = []
        if center_id:
            criteria.append(TutorAccessAudit.center_id == center_id)
        if actor_id:
            criteria.append(TutorAccessAudit.actor_id == actor_id)
        total = await self.count(session, *criteria)
        rows = await self.list_where(
            session,
            *criteria,
            order_by=(TutorAccessAudit.created_at.desc(),),
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return rows, total


class CenterPlanCheckoutRepository(BaseRepository[CenterPlanCheckout]):
    model = CenterPlanCheckout

    async def by_reference(
        self, session: AsyncSession, reference: str
    ) -> CenterPlanCheckout | None:
        return await self.first(session, CenterPlanCheckout.reference == reference)

    async def by_reference_for_update(
        self, session: AsyncSession, reference: str
    ) -> CenterPlanCheckout | None:
        statement = (
            select(CenterPlanCheckout)
            .where(CenterPlanCheckout.reference == reference)
            .with_for_update()
        )
        return await session.scalar(statement)

    async def abandon_pending(
        self, session: AsyncSession, user_id: str, center_id: str
    ) -> int:
        rows = await self.list_where(
            session,
            CenterPlanCheckout.user_id == user_id,
            CenterPlanCheckout.center_id == center_id,
            CenterPlanCheckout.status == "PENDING",
        )
        for row in rows:
            row.status = "ABANDONED"
        return len(rows)


class CenterAbuseReportRepository(BaseRepository[CenterAbuseReport]):
    model = CenterAbuseReport

    async def queue(
        self,
        session: AsyncSession,
        *,
        page: int = 1,
        page_size: int = 50,
        status: str | None = None,
        kind: str | None = None,
    ) -> tuple[list[CenterAbuseReport], int]:
        criteria = []
        if status:
            criteria.append(CenterAbuseReport.status == status)
        if kind:
            criteria.append(CenterAbuseReport.kind == kind)
        total = await self.count(session, *criteria)
        rows = await self.list_where(
            session,
            *criteria,
            order_by=(CenterAbuseReport.created_at.desc(),),
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return rows, total


class CenterTokenPoolRepository(BaseRepository[CenterTokenPool]):
    model = CenterTokenPool

    async def for_center(
        self, session: AsyncSession, center_id: str
    ) -> CenterTokenPool | None:
        return await self.first(session, CenterTokenPool.center_id == center_id)

    async def for_center_for_update(
        self, session: AsyncSession, center_id: str
    ) -> CenterTokenPool | None:
        statement = (
            select(CenterTokenPool)
            .where(CenterTokenPool.center_id == center_id)
            .with_for_update()
        )
        return await session.scalar(statement)


class CenterTokenLedgerRepository(BaseRepository[CenterTokenLedger]):
    model = CenterTokenLedger

    async def by_idempotency_key(
        self, session: AsyncSession, key: str
    ) -> CenterTokenLedger | None:
        return await self.first(session, CenterTokenLedger.idempotency_key == key)

    async def for_center(
        self,
        session: AsyncSession,
        center_id: str,
        *,
        limit: int = 50,
    ) -> list[CenterTokenLedger]:
        return await self.list_where(
            session,
            CenterTokenLedger.center_id == center_id,
            order_by=(CenterTokenLedger.created_at.desc(),),
            limit=limit,
        )


class CenterTokenCheckoutRepository(BaseRepository[CenterTokenCheckout]):
    model = CenterTokenCheckout

    async def by_reference(
        self, session: AsyncSession, reference: str
    ) -> CenterTokenCheckout | None:
        return await self.first(session, CenterTokenCheckout.reference == reference)

    async def by_reference_for_update(
        self, session: AsyncSession, reference: str
    ) -> CenterTokenCheckout | None:
        statement = (
            select(CenterTokenCheckout)
            .where(CenterTokenCheckout.reference == reference)
            .with_for_update()
        )
        return await session.scalar(statement)

    async def abandon_pending(
        self, session: AsyncSession, user_id: str, center_id: str
    ) -> int:
        rows = await self.list_where(
            session,
            CenterTokenCheckout.user_id == user_id,
            CenterTokenCheckout.center_id == center_id,
            CenterTokenCheckout.status == "PENDING",
        )
        for row in rows:
            row.status = "ABANDONED"
        return len(rows)


class TutorWalletRepository(BaseRepository[TutorWallet]):
    model = TutorWallet

    async def for_owner(
        self, session: AsyncSession, owner_id: str
    ) -> TutorWallet | None:
        return await self.first(session, TutorWallet.owner_id == owner_id)

    async def for_owner_for_update(
        self, session: AsyncSession, owner_id: str
    ) -> TutorWallet | None:
        statement = (
            select(TutorWallet)
            .where(TutorWallet.owner_id == owner_id)
            .with_for_update()
        )
        return await session.scalar(statement)


class TutorWalletLedgerRepository(BaseRepository[TutorWalletLedger]):
    model = TutorWalletLedger

    async def by_idempotency_key(
        self, session: AsyncSession, key: str
    ) -> TutorWalletLedger | None:
        return await self.first(session, TutorWalletLedger.idempotency_key == key)

    async def for_owner(
        self,
        session: AsyncSession,
        owner_id: str,
        *,
        limit: int = 50,
    ) -> list[TutorWalletLedger]:
        return await self.list_where(
            session,
            TutorWalletLedger.owner_id == owner_id,
            order_by=(TutorWalletLedger.created_at.desc(),),
            limit=limit,
        )

    async def commission_for_subscription(
        self, session: AsyncSession, subscription_id: str
    ) -> TutorWalletLedger | None:
        return await self.first(
            session,
            TutorWalletLedger.subscription_id == subscription_id,
            TutorWalletLedger.kind == "COMMISSION",
        )


class TutorWithdrawRequestRepository(BaseRepository[TutorWithdrawRequest]):
    model = TutorWithdrawRequest

    async def for_owner(
        self,
        session: AsyncSession,
        owner_id: str,
        *,
        limit: int = 50,
    ) -> list[TutorWithdrawRequest]:
        return await self.list_where(
            session,
            TutorWithdrawRequest.owner_id == owner_id,
            order_by=(TutorWithdrawRequest.created_at.desc(),),
            limit=limit,
        )

    async def queue(
        self,
        session: AsyncSession,
        *,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[TutorWithdrawRequest], int]:
        criteria = []
        if status:
            criteria.append(TutorWithdrawRequest.status == status)
        total = (
            await self.count(session, *criteria)
            if criteria
            else await self.count(session)
        )
        rows = await self.list_where(
            session,
            *criteria,
            order_by=(TutorWithdrawRequest.created_at.asc(),),
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return rows, total


centers_repository = CenterRepository()
memberships_repository = CenterMembershipRepository()
invites_repository = CenterInviteRepository()
groups_repository = CenterGroupRepository()
group_members_repository = CenterGroupMemberRepository()
assignments_repository = AssignmentRepository()
assignment_targets_repository = AssignmentTargetRepository()
recommendations_repository = RecommendationRepository()
tutor_access_audits_repository = TutorAccessAuditRepository()
center_plan_checkouts_repository = CenterPlanCheckoutRepository()
center_abuse_reports_repository = CenterAbuseReportRepository()
center_token_pools_repository = CenterTokenPoolRepository()
center_token_ledgers_repository = CenterTokenLedgerRepository()
center_token_checkouts_repository = CenterTokenCheckoutRepository()
tutor_wallets_repository = TutorWalletRepository()
tutor_wallet_ledgers_repository = TutorWalletLedgerRepository()
tutor_withdraw_requests_repository = TutorWithdrawRequestRepository()
