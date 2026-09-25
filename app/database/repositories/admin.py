"""Repositories for admin accounts and the audit log."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Admin, AdminAudit
from app.database.repositories.base import BaseRepository


class AdminRepository(BaseRepository[Admin]):
    model = Admin

    async def by_email(self, session: AsyncSession, email: str) -> Admin | None:
        return await self.first(session, Admin.email == email)

    async def by_username(self, session: AsyncSession, username: str) -> Admin | None:
        return await self.first(session, Admin.username == username)

    async def list_created(self, session: AsyncSession) -> list[Admin]:
        return await self.all_ordered(session, Admin.created_at)

    async def actors(self, session: AsyncSession) -> list[Admin]:
        actor_ids = select(AdminAudit.actor_id).distinct()
        return await self.many(
            session,
            select(Admin)
            .where(Admin.id.in_(actor_ids))
            .order_by(Admin.email, Admin.username),
        )


class AdminAuditRepository(BaseRepository[AdminAudit]):
    model = AdminAudit

    async def page(
        self,
        session: AsyncSession,
        page: int,
        *,
        page_size: int = 50,
        action: str | None = None,
    ) -> tuple[list[AdminAudit], int]:
        statement = select(AdminAudit)
        if action:
            statement = statement.where(AdminAudit.action == action)
        total = await session.scalar(
            select(func.count()).select_from(statement.subquery())
        )
        rows = await self.many(
            session,
            statement.order_by(AdminAudit.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size),
        )
        return rows, int(total or 0)


admins_repository = AdminRepository()
audits_repository = AdminAuditRepository()
