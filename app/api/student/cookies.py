from fastapi import Request, Response

from app.core.config import settings
from app.core.rate_limit import client_ip


def _ip(request: Request) -> str:
    return client_ip(
        request.headers.get("x-forwarded-for"), request.headers.get("x-real-ip")
    )


def _cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "scholarscrib.session",
        token,
        httponly=True,
        samesite="lax",
        secure=not settings.debug,
        path="/",
        max_age=60 * 60 * 24 * 30,
    )
