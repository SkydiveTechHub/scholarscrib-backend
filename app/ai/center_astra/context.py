"""Request-scoped center Astra context. Never taken from the model."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.services.centers.access import load_staff_center


@dataclass
class CenterAstraContext:
    session: AsyncSession
    center_id: str
    tutor_id: str
    is_owner: bool
    staff_role: str


_center_astra_context: ContextVar[CenterAstraContext | None] = ContextVar(
    "center_astra_context", default=None
)


def set_center_astra_context(ctx: CenterAstraContext) -> Token:
    return _center_astra_context.set(ctx)


def reset_center_astra_context(token: Token) -> None:
    _center_astra_context.reset(token)


def get_center_astra_context() -> CenterAstraContext:
    ctx = _center_astra_context.get()
    if ctx is None:
        raise ApiError(500, "Center Astra context is not available")
    return ctx


async def load_center_astra_context(
    session: AsyncSession, center_id: str, tutor_id: str
) -> CenterAstraContext:
    _center, membership = await load_staff_center(session, center_id, tutor_id)
    role = str(membership.kind or "").upper()
    return CenterAstraContext(
        session=session,
        center_id=center_id,
        tutor_id=tutor_id,
        is_owner=role == "OWNER",
        staff_role=role,
    )
