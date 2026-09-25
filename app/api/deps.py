from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from app.core.config import settings
from app.core.errors import ApiError, unauthorized
from app.core.security import decode_token, read_bearer
from app.core.timeutil import as_utc, utcnow
from app.database.db import AnSession
from app.database.models import Admin
from app.database.repositories.admin import admins_repository
from app.database.repositories.identity import devices_repository, users_repository
from app.domain import DEVICE_TOUCH_SECONDS, can, entitlement_denial
from app.services.auth.rules import is_session_revoked


@dataclass
class StudentPrincipal:
    id: str
    email: str | None
    tier: str
    device_id: str | None
    class_level: str | None
    track: str | None
    state: str | None
    first_name: str | None
    last_name: str | None
    image: str | None
    has_password: bool
    issued_at: int | None


def _student_token(request: Request) -> str | None:
    return read_bearer(request.headers.get("authorization")) or request.cookies.get(
        "scholarscrib.session"
    )


async def optional_student(
    request: Request, session: AnSession
) -> StudentPrincipal | None:
    raw = _student_token(request)
    if not raw:
        return None
    claims = decode_token(raw, settings.auth_secret)
    if not claims or not claims.get("sub"):
        return None
    user = await users_repository.by_id(session, claims["sub"])
    if user is None:
        return None
    issued = claims.get("sessionStartedAt") or claims.get("iat")
    if is_session_revoked(user.is_active, user.sessions_valid_from, issued):
        return None
    device_id = claims.get("deviceId")
    if device_id:
        device = await devices_repository.by_id(session, device_id)
        if device is None or device.revoked_at is not None or device.user_id != user.id:
            return None
        if (
            utcnow() - as_utc(device.last_seen_at)
        ).total_seconds() >= DEVICE_TOUCH_SECONDS:
            device.last_seen_at = utcnow()
    return StudentPrincipal(
        id=user.id,
        email=user.email,
        tier=user.tier,
        device_id=device_id,
        class_level=user.class_level,
        track=user.track,
        state=user.state,
        first_name=user.first_name,
        last_name=user.last_name,
        image=user.image,
        has_password=bool(user.password_hash),
        issued_at=issued,
    )


async def require_student(request: Request, session: AnSession) -> StudentPrincipal:
    student = await optional_student(request, session)
    if student is None:
        raise unauthorized()
    return student


def require_feature(feature: str):
    async def checker(
        request: Request,
        session: AnSession,
        student: Annotated[StudentPrincipal, Depends(require_student)],
    ) -> StudentPrincipal:
        tier = student.tier
        if not can(tier, feature):
            fresh = await users_repository.by_id(session, student.id)
            tier = fresh.tier if fresh else tier
            if not can(tier, feature):
                denial = entitlement_denial(feature)
                raise ApiError(
                    403,
                    str(denial["error"]),
                    requiredTier=denial["requiredTier"],
                    feature=feature,
                )
            student.tier = tier
        return student

    return checker


async def require_admin(request: Request, session: AnSession) -> Admin:
    raw = read_bearer(request.headers.get("authorization")) or request.cookies.get(
        "scholarscrib.admin-session"
    )
    claims = decode_token(raw, settings.admin_auth_secret) if raw else None
    if not claims or not claims.get("sub"):
        raise unauthorized()
    admin = await admins_repository.by_id(session, claims["sub"])
    if admin is None or not admin.is_active:
        raise unauthorized()
    return admin


async def require_owner(admin: Annotated[Admin, Depends(require_admin)]) -> Admin:
    if not admin.is_owner:
        raise ApiError(403, "Owner access required")
    return admin


async def library_student(request: Request, session: AnSession) -> StudentPrincipal:
    """Library accepts a decoded token without the full profile refresh."""
    raw = _student_token(request)
    claims = decode_token(raw, settings.auth_secret) if raw else None
    if not claims or not claims.get("sub"):
        raise unauthorized()
    profile = claims.get("profile") or {}
    user = await users_repository.by_id(session, claims["sub"])
    return StudentPrincipal(
        id=claims["sub"],
        email=user.email if user else None,
        tier=profile.get("tier") or (user.tier if user else "FREEMIUM"),
        device_id=claims.get("deviceId"),
        class_level=profile.get("classLevel"),
        track=profile.get("track"),
        state=profile.get("state"),
        first_name=profile.get("firstName"),
        last_name=profile.get("lastName"),
        image=profile.get("image"),
        has_password=bool(user and user.password_hash),
        issued_at=claims.get("iat"),
    )
