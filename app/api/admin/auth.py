from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.api.responses import AdminSessionOut, AdminTokenOut, OkOut
from app.core.config import settings
from app.core.errors import ApiError
from app.core.security import admin_token, verify_password
from app.core.timeutil import utcnow
from app.database.db import AnSession
from app.database.models import Admin
from app.database.repositories.admin import admins_repository
from app.services.admin import admin_row
from app.services.auth import ADMIN_IDENTIFIER

router = APIRouter(prefix="/auth", tags=["Admin / Auth"])


class AdminLogin(BaseModel):
    identifier: str = Field(min_length=3)
    password: str = Field(min_length=1)


def _admin_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "scholarscrib.admin-session",
        token,
        httponly=True,
        samesite="lax",
        secure=not settings.debug,
        path="/admin",
        max_age=60 * 60 * 8,
    )


@router.post("/login", response_model=AdminTokenOut)
async def admin_login(body: AdminLogin, response: Response, session: AnSession):
    ident = body.identifier.strip().lower()
    if "@" in ident:
        admin = await admins_repository.by_email(session, ident)
    elif ADMIN_IDENTIFIER.match(ident):
        admin = await admins_repository.by_username(session, ident)
    else:
        admin = None
    if (
        admin is None
        or not admin.is_active
        or not verify_password(body.password, admin.password_hash)
    ):
        raise ApiError(401, "Unauthorized")
    admin.last_login_at = utcnow()
    token = admin_token(admin.id)
    _admin_cookie(response, token)
    return {"accessToken": token, "admin": admin_row(admin)}


@router.post("/logout", response_model=OkOut)
async def admin_logout(response: Response):
    response.delete_cookie("scholarscrib.admin-session", path="/admin")
    return {"ok": True}


@router.get("/session", response_model=AdminSessionOut)
async def admin_session(admin: Annotated[Admin, Depends(require_admin)]):
    return {
        "admin": {
            "id": admin.id,
            "isOwner": admin.is_owner,
            "isActive": admin.is_active,
        }
    }
