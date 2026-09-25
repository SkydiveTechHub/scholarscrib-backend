from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import OkOut, RegisterOut, SessionOut, TokenOut
from app.api.schemas import LoginIn, RegisterIn
from app.api.student.cookies import _cookie, _ip
from app.core.config import settings
from app.core.errors import ApiError
from app.database.db import AnSession
from app.services.auth import (
    GoogleSignInService,
    LoginStudentService,
    RegisterStudentService,
    exchange_google_code,
)

router = APIRouter(prefix="/auth", tags=["Student / Auth"])


@router.post("/register", status_code=201, response_model=RegisterOut)
async def register(body: RegisterIn, request: Request, session: AnSession):
    return await RegisterStudentService(session, body, _ip(request)).process()


@router.post("/login", response_model=TokenOut)
async def login(
    body: LoginIn, request: Request, response: Response, session: AnSession
):
    if len(body.password) < 6:
        raise ApiError(401, "Invalid email or password")
    result = await LoginStudentService(
        session,
        body.email,
        body.password,
        _ip(request),
        body.deviceLabel or request.headers.get("user-agent", "Browser"),
    ).process()
    if not result:
        raise ApiError(401, "Invalid email or password")
    _cookie(response, result["accessToken"])
    return result


@router.post("/logout", response_model=OkOut)
async def logout(response: Response):
    response.delete_cookie("scholarscrib.session", path="/")
    return {"ok": True}


@router.get("/session", response_model=SessionOut)
async def session_view(student: Annotated[StudentPrincipal, Depends(require_student)]):
    return {
        "user": {
            "id": student.id,
            "email": student.email,
            "firstName": student.first_name,
            "lastName": student.last_name,
            "classLevel": student.class_level,
            "track": student.track,
            "state": student.state,
            "tier": student.tier,
            "image": student.image,
            "deviceId": student.device_id,
        }
    }


@router.get("/google")
async def google_start(request: Request) -> RedirectResponse:
    if not settings.google_enabled:
        raise ApiError(503, "Google sign-in is not configured")
    redirect_uri = str(request.url_for("google_callback"))
    query = urlencode(
        {
            "client_id": settings.auth_google_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
        }
    )
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{query}")


@router.get("/google/callback", name="google_callback")
async def google_callback(
    code: str, request: Request, session: AnSession
) -> RedirectResponse:
    profile = await exchange_google_code(code, str(request.url_for("google_callback")))
    result = await GoogleSignInService(
        session, profile, request.headers.get("user-agent", "Browser")
    ).process()
    return RedirectResponse(
        f"{settings.app_url.rstrip('/')}/complete-profile#token={result['accessToken']}"
    )
