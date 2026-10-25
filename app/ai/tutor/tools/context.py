"""Request-scoped student context for tutor tools (not model-supplied)."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.database.repositories.identity import users_repository


@dataclass
class TutorToolContext:
    session: AsyncSession
    user_id: str
    class_level: str | None
    track: str | None
    tier: str
    first_name: str | None = None


_tool_context: ContextVar[TutorToolContext | None] = ContextVar(
    "tutor_tool_context", default=None
)


def set_tool_context(ctx: TutorToolContext) -> Token:
    return _tool_context.set(ctx)


def reset_tool_context(token: Token) -> None:
    _tool_context.reset(token)


def peek_tool_context() -> TutorToolContext | None:
    """The context if a tutor turn set one, else None. Never raises."""
    return _tool_context.get()


def get_tool_context() -> TutorToolContext:
    ctx = _tool_context.get()
    if ctx is None:
        raise ApiError(500, "Tutor tool context is not available")
    return ctx


async def load_tool_context(session: AsyncSession, user_id: str) -> TutorToolContext:
    user = await users_repository.by_id(session, user_id)
    if user is None:
        raise ApiError(404, "Student not found")
    return TutorToolContext(
        session=session,
        user_id=user.id,
        class_level=user.class_level,
        track=user.track,
        tier=user.tier,
        first_name=user.first_name,
    )
