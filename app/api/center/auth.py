from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field

from app.api.center.cookies import _ip, _tutor_cookie
from app.api.deps import TutorPrincipal, optional_tutor, require_tutor
from app.core.errors import ApiError
from app.core.timeutil import utcnow
from app.database.db import AnSession
from app.database.repositories.identity import devices_repository
from app.database.repositories.notification import push_subscriptions_repository
from app.services.auth import LoginTutorService

router = APIRouter(prefix="/auth", tags=["Center / Auth"])


class TutorLoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)
    deviceLabel: str | None = None


@router.post("/login")
async def tutor_login(
    body: TutorLoginIn, request: Request, response: Response, session: AnSession
):
    if len(body.password) < 6:
        raise ApiError(401, "Invalid email or password")
    result = await LoginTutorService(
        session,
        body.email,
        body.password,
        _ip(request),
        body.deviceLabel or request.headers.get("user-agent", "Browser"),
    ).process()
    if not result:
        raise ApiError(401, "Invalid email or password")
    _tutor_cookie(response, result["accessToken"])
    return result


@router.post("/logout")
async def tutor_logout(request: Request, response: Response, session: AnSession):
    tutor = await optional_tutor(request, session)
    if tutor is not None and tutor.device_id:
        device = await devices_repository.by_id(session, tutor.device_id)
        if device is not None and device.revoked_at is None:
            device.revoked_at = utcnow()
            await push_subscriptions_repository.delete_for_devices(session, [device.id])
    response.delete_cookie("scholarscrib.tutor-session", path="/center")
    return {"ok": True}


@router.get("/session")
async def tutor_session(tutor: Annotated[TutorPrincipal, Depends(require_tutor)]):
    return {
        "user": {
            "id": tutor.id,
            "email": tutor.email,
            "role": "TUTOR",
            "firstName": tutor.first_name,
            "lastName": tutor.last_name,
            "state": tutor.state,
            "image": tutor.image,
            "deviceId": tutor.device_id,
            "emailVerified": tutor.email_verified,
        }
    }
