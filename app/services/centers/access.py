"""Center staff vs owner authorization (T-7–T-10, D2)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.database.models import Center, CenterMembership
from app.database.repositories.centers import centers_repository, memberships_repository
from app.database.repositories.identity import users_repository

STAFF_KINDS = frozenset({"OWNER", "FACILITATOR"})


async def load_owned_center(
    session: AsyncSession, center_id: str, tutor_id: str
) -> Center:
    """Owner-only: billing, facilitator manage, archive."""
    center = await centers_repository.owned_by(session, center_id, tutor_id)
    if center is None:
        raise ApiError(404, "Center not found")
    return center


async def load_staff_center(
    session: AsyncSession, center_id: str, tutor_id: str
) -> tuple[Center, CenterMembership]:
    """Owner or facilitator; blocked if owner is suspended (T-10)."""
    center = await centers_repository.by_id(session, center_id)
    if center is None or center.archived_at is not None:
        raise ApiError(404, "Center not found")
    membership = await memberships_repository.by_center_user(
        session, center_id, tutor_id
    )
    if (
        membership is None
        or membership.status != "ACTIVE"
        or membership.kind not in STAFF_KINDS
    ):
        raise ApiError(404, "Center not found")
    if membership.kind == "FACILITATOR":
        owner = await users_repository.by_id(session, center.owner_id)
        if owner is None or not owner.is_active or owner.suspended_at is not None:
            raise ApiError(
                403,
                "This center is unavailable because the owner account is suspended.",
                code="CENTER_OWNER_SUSPENDED",
            )
    return center, membership


def staff_role(membership: CenterMembership) -> str:
    return membership.kind.lower()
