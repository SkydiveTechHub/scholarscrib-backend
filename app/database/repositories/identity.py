"""Repositories for accounts, devices, and schools."""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.database.models import (
    Account,
    AuthSession,
    School,
    StudyPlan,
    User,
    UserDevice,
    VerificationToken,
)
from app.database.repositories.base import BaseRepository


def user_audience_filters(audience: dict) -> list[ColumnElement[bool]]:
    """WHERE clauses on User for announcement audience targeting."""
    filters: list[ColumnElement[bool]] = []
    if audience.get("classLevels"):
        filters.append(User.class_level.in_(audience["classLevels"]))
    if audience.get("tracks"):
        filters.append(User.track.in_(audience["tracks"]))
    if audience.get("tiers"):
        filters.append(User.tier.in_(audience["tiers"]))
    if audience.get("userIds"):
        filters.append(User.id.in_(audience["userIds"]))
    if audience.get("examTargets"):
        filters.append(
            User.id.in_(
                select(StudyPlan.student_id).where(
                    StudyPlan.is_active.is_(True),
                    StudyPlan.target_exam.in_(audience["examTargets"]),
                )
            )
        )
    return filters


class UserRepository(BaseRepository[User]):
    model = User

    async def by_email(self, session: AsyncSession, email: str) -> User | None:
        return await self.first(session, User.email == email)

    async def lock_by_id(self, session: AsyncSession, user_id: str) -> User | None:
        return await self.lock(session, User.id == user_id)

    async def by_student_contact(
        self, session: AsyncSession, contact: str
    ) -> User | None:
        return await self.first(
            session,
            User.role == "STUDENT",
            or_(User.email == contact.lower(), User.phone == contact),
        )

    async def search_students(
        self,
        session: AsyncSession,
        query: str | None,
        page: int,
        page_size: int,
        *,
        class_level: str | None = None,
        track: str | None = None,
        tier: str | None = None,
        status: str | None = None,
        state: str | None = None,
    ) -> tuple[list[User], int]:
        statement = select(User).where(User.role == "STUDENT")
        if query:
            like = f"%{query.lower()}%"
            statement = statement.where(
                or_(
                    User.email.ilike(like),
                    User.phone.ilike(like),
                    User.first_name.ilike(like),
                    User.last_name.ilike(like),
                )
            )
        if class_level:
            statement = statement.where(User.class_level == class_level)
        if track:
            statement = statement.where(User.track == track)
        if tier:
            statement = statement.where(User.tier == tier)
        if status == "active":
            statement = statement.where(User.is_active.is_(True))
        elif status == "suspended":
            statement = statement.where(User.is_active.is_(False))
        if state == "none":
            statement = statement.where(User.state.is_(None))
        elif state:
            statement = statement.where(User.state == state)
        total = await session.scalar(
            select(func.count()).select_from(statement.subquery())
        )
        rows = await self.many(
            session,
            statement.order_by(User.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size),
        )
        return rows, int(total or 0)

    async def for_audience(self, session: AsyncSession, audience: dict) -> list[User]:
        statement = select(User).where(
            User.role == "STUDENT",
            User.is_active.is_(True),
            *user_audience_filters(audience),
        )
        return await self.many(session, statement)


class AccountRepository(BaseRepository[Account]):
    model = Account

    async def by_provider(
        self, session: AsyncSession, provider: str, provider_account_id: str
    ) -> Account | None:
        return await self.first(
            session,
            Account.provider == provider,
            Account.provider_account_id == provider_account_id,
        )


class UserDeviceRepository(BaseRepository[UserDevice]):
    model = UserDevice

    async def for_user(self, session: AsyncSession, user_id: str) -> list[UserDevice]:
        return await self.list_where(session, UserDevice.user_id == user_id)

    async def active_except(
        self,
        session: AsyncSession,
        user_id: str,
        device_id: str | None,
    ) -> list[UserDevice]:
        criteria = [
            UserDevice.user_id == user_id,
            UserDevice.revoked_at.is_(None),
        ]
        if device_id is not None:
            criteria.append(UserDevice.id != device_id)
        return list((await session.scalars(select(UserDevice).where(*criteria))).all())


class SchoolRepository(BaseRepository[School]):
    model = School


class AuthSessionRepository(BaseRepository[AuthSession]):
    model = AuthSession


class VerificationTokenRepository(BaseRepository[VerificationToken]):
    model = VerificationToken


users_repository = UserRepository()
accounts_repository = AccountRepository()
devices_repository = UserDeviceRepository()
schools_repository = SchoolRepository()
auth_sessions_repository = AuthSessionRepository()
verification_tokens_repository = VerificationTokenRepository()
