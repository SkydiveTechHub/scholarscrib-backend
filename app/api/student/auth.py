from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse

from app.api.deps import StudentPrincipal, optional_student, require_student
from app.api.responses import (
    MessageOut,
    OkOut,
    OtpStatusOut,
    RegisterOut,
    SessionOut,
    TokenOut,
)
from app.api.schemas import (
    ForgotPasswordIn,
    GoogleTokensIn,
    LoginIn,
    OTPRequestIn,
    OTPVerifyIn,
    RegisterIn,
    ResetPasswordIn,
)
from app.api.student.cookies import _cookie, _ip
from app.core.config import settings
from app.core.errors import ApiError
from app.core.timeutil import utcnow
from app.database.db import AnSession
from app.database.repositories.identity import devices_repository
from app.database.repositories.notification import push_subscriptions_repository
from app.services.auth import (
    DirectGoogleSignInService,
    GoogleSignInService,
    LoginStudentService,
    RegisterStudentService,
    StudentForgotPasswordService,
    StudentOTPService,
    StudentResetPasswordService,
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
async def logout(request: Request, response: Response, session: AnSession):
    student = await optional_student(request, session)
    if student is not None and student.device_id:
        device = await devices_repository.by_id(session, student.device_id)
        if device is not None and device.revoked_at is None:
            # Kills this device's token server-side: replaying it after sign-out
            # fails the device check in optional_student.
            device.revoked_at = utcnow()
            await push_subscriptions_repository.delete_for_devices(session, [device.id])
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
    query = urlencode(
        {
            "client_id": settings.auth_google_id,
            "redirect_uri": settings.frontend_url,
            "response_type": "code",
            "scope": "openid email profile",
        }
    )
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{query}")


@router.get("/google/callback", name="google_callback")
async def google_callback(
    code: str, request: Request, session: AnSession
) -> RedirectResponse:
    profile = await exchange_google_code(code, settings.frontend_url)
    result = await GoogleSignInService(
        session, profile, request.headers.get("user-agent", "Browser")
    ).process()
    return RedirectResponse(
        f"{settings.app_url.rstrip('/')}/complete-profile#token={result['accessToken']}"
    )


@router.post("/forgot-password", response_model=MessageOut)
async def forgot_password(body: ForgotPasswordIn, request: Request, session: AnSession):
    return await StudentForgotPasswordService(
        session, body.email, _ip(request)
    ).process()


@router.post("/reset-password", response_model=MessageOut)
async def reset_password(body: ResetPasswordIn, session: AnSession):
    return await StudentResetPasswordService(
        session, body.email, body.token, body.newPassword
    ).process()


@router.post("/otp/send", response_model=MessageOut)
async def send_otp(body: OTPRequestIn, session: AnSession):
    await StudentOTPService(session).send_otp(body.email)
    return {"message": "Verification code sent to your email"}


@router.post("/otp/verify", response_model=OtpStatusOut)
async def verify_otp(body: OTPVerifyIn, session: AnSession):
    ok, detail = await StudentOTPService(session).verify_otp(body.email, body.otp)
    if not ok:
        raise ApiError(400, detail)
    return {"detail": detail, "status": True}


@router.post("/google/tokens", response_model=TokenOut)
async def google_tokens(
    body: GoogleTokensIn, request: Request, response: Response, session: AnSession
):
    result = await DirectGoogleSignInService(
        session,
        body.code,
        body.redirectUri,
        body.deviceLabel or request.headers.get("user-agent", "Browser"),
    ).process()
    _cookie(response, result["accessToken"])
    return result
