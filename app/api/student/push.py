from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import OkOut
from app.api.schemas import EndpointIn, PushIn
from app.core.errors import ApiError
from app.core.rate_limit import hit
from app.database.db import AnSession
from app.database.repositories.notification import push_subscriptions_repository

router = APIRouter(prefix="/push", tags=["Student / Push"])


@router.post("/subscription", response_model=OkOut)
async def push_subscribe(
    body: PushIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    hit(f"push-subscribe:{student.id}", 10, 60)
    if not str(body.endpoint).startswith("https://"):
        raise ApiError(400, "endpoint must be an https URL")
    await push_subscriptions_repository.subscribe(
        session,
        user_id=student.id,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        device_id=student.device_id,
    )
    return {"ok": True}


@router.delete("/subscription", response_model=OkOut)
async def push_unsubscribe(
    body: EndpointIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    await push_subscriptions_repository.unsubscribe(
        session, user_id=student.id, endpoint=body.endpoint
    )
    return {"ok": True}
