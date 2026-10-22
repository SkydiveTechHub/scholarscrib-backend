"""Request-scoped admin context for Astra tools. Never taken from the model."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.database.repositories.admin import admins_repository


@dataclass
class AdminToolContext:
    session: AsyncSession
    admin_id: str
    is_owner: bool


_admin_tool_context: ContextVar[AdminToolContext | None] = ContextVar(
    "admin_tool_context", default=None
)


def set_admin_tool_context(ctx: AdminToolContext) -> Token:
    return _admin_tool_context.set(ctx)


def reset_admin_tool_context(token: Token) -> None:
    _admin_tool_context.reset(token)


def get_admin_tool_context() -> AdminToolContext:
    ctx = _admin_tool_context.get()
    if ctx is None:
        raise ApiError(500, "Admin tool context is not available")
    return ctx


async def load_admin_tool_context(
    session: AsyncSession, admin_id: str
) -> AdminToolContext:
    admin = await admins_repository.by_id(session, admin_id)
    if admin is None or not admin.is_active:
        raise ApiError(401, "Unauthorized")
    return AdminToolContext(
        session=session, admin_id=admin.id, is_owner=bool(admin.is_owner)
    )
