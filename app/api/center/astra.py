"""Tutor Astra routes — center-scoped, under /center/api (not /api/tutor)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query

from app.ai.center_astra.service import (
    CreateCenterAstraThreadService,
    DeleteCenterAstraThreadService,
    GetCenterAstraThreadService,
    ListCenterAstraMessagesService,
    ListCenterAstraThreadsService,
    PostCenterAstraMessageService,
    open_run_replay,
    resume_center_astra_action,
    start_message_run,
    stop_center_astra_stream,
    stream_resume_center_astra_action,
)
from app.ai.core.sse import sse_response
from app.api.deps import TutorPrincipal, require_tutor
from app.api.responses import (
    CenterAstraThreadOut,
    CenterAstraThreadsOut,
    OkOut,
    TutorMessagesOut,
    TutorPostMessageOut,
    TutorStreamStopOut,
)
from app.api.schemas import (
    CenterAstraMessageIn,
    CenterAstraResumeIn,
    CenterAstraStreamStopIn,
    CenterAstraThreadCreateIn,
)
from app.database.db import AnSession

router = APIRouter(tags=["Center / Astra"])


@router.post(
    "/centers/{center_id}/astra/threads",
    status_code=201,
    response_model=CenterAstraThreadOut,
)
async def create_thread(
    center_id: str,
    body: CenterAstraThreadCreateIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await CreateCenterAstraThreadService(
        session, tutor.id, center_id, body
    ).process()


@router.get(
    "/centers/{center_id}/astra/threads",
    response_model=CenterAstraThreadsOut,
)
async def list_threads(
    center_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await ListCenterAstraThreadsService(session, tutor.id, center_id).process()


@router.get(
    "/centers/{center_id}/astra/threads/{thread_id}",
    response_model=CenterAstraThreadOut,
)
async def get_thread(
    center_id: str,
    thread_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await GetCenterAstraThreadService(
        session, tutor.id, center_id, thread_id
    ).process()


@router.delete(
    "/centers/{center_id}/astra/threads/{thread_id}",
    response_model=OkOut,
)
async def delete_thread(
    center_id: str,
    thread_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await DeleteCenterAstraThreadService(
        session, tutor.id, center_id, thread_id
    ).process()


@router.get(
    "/centers/{center_id}/astra/threads/{thread_id}/messages",
    response_model=TutorMessagesOut,
)
async def list_messages(
    center_id: str,
    thread_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
    order_by: Annotated[
        Literal["asc", "desc"],
        Query(alias="orderBy"),
    ] = "asc",
):
    return await ListCenterAstraMessagesService(
        session, tutor.id, center_id, thread_id, order_by=order_by
    ).process()


@router.post(
    "/centers/{center_id}/astra/threads/{thread_id}/messages",
    response_model=TutorPostMessageOut,
)
async def post_message(
    center_id: str,
    thread_id: str,
    body: CenterAstraMessageIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await PostCenterAstraMessageService(
        session, tutor.id, center_id, thread_id, body
    ).process()


@router.post("/centers/{center_id}/astra/threads/{thread_id}/messages/stream")
async def post_message_stream(
    center_id: str,
    thread_id: str,
    body: CenterAstraMessageIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    run = await start_message_run(session, tutor.id, center_id, thread_id, body.content)
    return await sse_response(run.iter_after(0))


@router.post(
    "/centers/{center_id}/astra/threads/{thread_id}/messages/stream/stop",
    response_model=TutorStreamStopOut,
)
async def stop_message_stream(
    center_id: str,
    thread_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
    body: CenterAstraStreamStopIn | None = None,
):
    stream_id = body.streamId if body is not None else None
    return await stop_center_astra_stream(
        session, tutor.id, center_id, thread_id, stream_id=stream_id
    )


@router.get("/centers/{center_id}/astra/threads/{thread_id}/runs/{run_id}/stream")
async def rejoin_run_stream(
    center_id: str,
    thread_id: str,
    run_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
    last_event_id: Annotated[str | None, Header()] = None,
):
    events = await open_run_replay(
        session, tutor.id, center_id, thread_id, run_id, last_event_id
    )
    return await sse_response(events)


@router.post("/centers/{center_id}/astra/threads/{thread_id}/actions/resume")
async def resume_action(
    center_id: str,
    thread_id: str,
    body: CenterAstraResumeIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    if body.stream:
        return await sse_response(
            stream_resume_center_astra_action(tutor.id, center_id, thread_id, body)
        )
    return await resume_center_astra_action(
        session, tutor.id, center_id, thread_id, body
    )
