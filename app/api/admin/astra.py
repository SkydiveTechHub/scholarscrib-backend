"""Admin Astra chat routes."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query

from app.api.deps import require_admin
from app.api.responses import (
    AdminAstraThreadOut,
    AdminAstraThreadsOut,
    OkOut,
    TutorMessagesOut,
    TutorPostMessageOut,
    TutorStreamStopOut,
)
from app.api.schemas import (
    AdminAstraMessageIn,
    AdminAstraResumeIn,
    AdminAstraStreamStopIn,
    AdminAstraThreadCreateIn,
)
from app.database.db import AnSession
from app.database.models import Admin
from app.services.admin_astra.service import (
    CreateAdminAstraThreadService,
    DeleteAdminAstraThreadService,
    GetAdminAstraThreadService,
    ListAdminAstraMessagesService,
    ListAdminAstraThreadsService,
    PostAdminAstraMessageService,
    open_run_replay,
    resume_admin_astra_action,
    start_message_run,
    stop_admin_astra_stream,
    stream_resume_admin_astra_action,
)
from app.services.rag.sse import sse_response

router = APIRouter(prefix="/astra", tags=["Admin / Astra"])


@router.post("/threads", status_code=201, response_model=AdminAstraThreadOut)
async def create_thread(
    body: AdminAstraThreadCreateIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await CreateAdminAstraThreadService(session, admin.id, body).process()


@router.get("/threads", response_model=AdminAstraThreadsOut)
async def list_threads(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await ListAdminAstraThreadsService(session, admin.id).process()


@router.get("/threads/{thread_id}", response_model=AdminAstraThreadOut)
async def get_thread(
    thread_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await GetAdminAstraThreadService(session, admin.id, thread_id).process()


@router.delete("/threads/{thread_id}", response_model=OkOut)
async def delete_thread(
    thread_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await DeleteAdminAstraThreadService(session, admin.id, thread_id).process()


@router.get("/threads/{thread_id}/messages", response_model=TutorMessagesOut)
async def list_messages(
    thread_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    order_by: Annotated[
        Literal["asc", "desc"],
        Query(alias="orderBy"),
    ] = "asc",
):
    return await ListAdminAstraMessagesService(
        session, admin.id, thread_id, order_by=order_by
    ).process()


@router.post(
    "/threads/{thread_id}/messages",
    response_model=TutorPostMessageOut,
)
async def post_message(
    thread_id: str,
    body: AdminAstraMessageIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    return await PostAdminAstraMessageService(
        session, admin.id, thread_id, body
    ).process()


@router.post("/threads/{thread_id}/messages/stream")
async def post_message_stream(
    thread_id: str,
    body: AdminAstraMessageIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    run = await start_message_run(session, admin.id, thread_id, body.content)
    return await sse_response(run.iter_after(0))


@router.post(
    "/threads/{thread_id}/messages/stream/stop",
    response_model=TutorStreamStopOut,
)
async def stop_message_stream(
    thread_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    body: AdminAstraStreamStopIn | None = None,
):
    stream_id = body.streamId if body is not None else None
    return await stop_admin_astra_stream(
        session, admin.id, thread_id, stream_id=stream_id
    )


@router.get("/threads/{thread_id}/runs/{run_id}/stream")
async def rejoin_run_stream(
    thread_id: str,
    run_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    last_event_id: Annotated[str | None, Header()] = None,
):
    events = await open_run_replay(session, admin.id, thread_id, run_id, last_event_id)
    return await sse_response(events)


@router.post("/threads/{thread_id}/actions/resume")
async def resume_action(
    thread_id: str,
    body: AdminAstraResumeIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    if body.stream:
        return await sse_response(
            stream_resume_admin_astra_action(admin.id, thread_id, body)
        )
    return await resume_admin_astra_action(session, admin.id, thread_id, body)
