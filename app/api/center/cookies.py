from fastapi import Request, Response

from app.core.config import settings
from app.core.rate_limit import client_ip


def _ip(request: Request) -> str:
    return client_ip(
        request.headers.get("x-forwarded-for"), request.headers.get("x-real-ip")
    )


def _tutor_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "scholarscrib.tutor-session",
        token,
        httponly=True,
        samesite="lax",
        secure=not settings.debug,
        path="/center",
        max_age=60 * 60 * 24 * 30,
    )
