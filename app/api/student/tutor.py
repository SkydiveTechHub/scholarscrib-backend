"""Student AI tutor chat routes."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import StudentPrincipal, require_feature, require_student
from app.api.responses import (
    OkOut,
    TutorCheckpointHistoryOut,
    TutorMessagesOut,
    TutorPostMessageOut,
    TutorStreamStopOut,
    TutorThreadOut,
    TutorThreadsOut,
    TutorTimeTravelOut,
)
from app.api.schemas import (
    TutorFeedbackIn,
    TutorForkIn,
    TutorMessageIn,
    TutorReplayIn,
    TutorStreamStopIn,
    TutorThreadCreateIn,
)
from app.database.db import AnSession
from app.services.rag.service import (
    CreateTutorThreadService,
    DeleteTutorThreadService,
    GetTutorThreadService,
    ListTutorMessagesService,
    ListTutorThreadsService,
    PostTutorMessageService,
    TutorFeedbackService,
    stop_tutor_stream,
    stream_tutor_reply,
)
from app.services.rag.sse import sse_response
from app.services.rag.time_travel import (
    fork_from_checkpoint,
    list_checkpoints,
    replay_from_checkpoint,
)

router = APIRouter(
    prefix="/tutor",
    tags=["Student / Tutor"],
    dependencies=[Depends(require_feature("aiTutor"))],
)


@router.post("/threads", status_code=201, response_model=TutorThreadOut)
async def create_thread(
    body: TutorThreadCreateIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await CreateTutorThreadService(session, student.id, body).process()


@router.get("/threads", response_model=TutorThreadsOut)
async def list_threads(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await ListTutorThreadsService(session, student.id).process()


@router.get("/threads/{thread_id}", response_model=TutorThreadOut)
async def get_thread(
    thread_id: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await GetTutorThreadService(session, student.id, thread_id).process()


@router.delete("/threads/{thread_id}", response_model=OkOut)
async def delete_thread(
    thread_id: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await DeleteTutorThreadService(session, student.id, thread_id).process()


@router.get(
    "/threads/{thread_id}/messages",
    response_model=TutorMessagesOut,
)
async def list_messages(
    thread_id: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
    order_by: Annotated[
        Literal["asc", "desc"],
        Query(
            alias="orderBy",
            description="Sort by createdAt. asc=oldest first, desc=newest first.",
        ),
    ] = "asc",
):
    return await ListTutorMessagesService(
        session, student.id, thread_id, order_by=order_by
    ).process()


@router.post(
    "/threads/{thread_id}/messages",
    response_model=TutorPostMessageOut,
)
async def post_message(
    thread_id: str,
    body: TutorMessageIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await PostTutorMessageService(session, student.id, thread_id, body).process()


@router.post("/threads/{thread_id}/messages/stream")
async def post_message_stream(
    thread_id: str,
    body: TutorMessageIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await sse_response(
        stream_tutor_reply(session, student.id, thread_id, body.content)
    )


@router.post(
    "/threads/{thread_id}/messages/stream/stop",
    response_model=TutorStreamStopOut,
)
async def stop_message_stream(
    thread_id: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
    body: TutorStreamStopIn | None = None,
):
    """Stop the in-flight SSE reply so the student can ask a different question."""
    stream_id = body.streamId if body is not None else None
    return await stop_tutor_stream(session, student.id, thread_id, stream_id=stream_id)


@router.get(
    "/threads/{thread_id}/history",
    response_model=TutorCheckpointHistoryOut,
)
async def thread_history(
    thread_id: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    await GetTutorThreadService(session, student.id, thread_id).process()
    checkpoints = await list_checkpoints(thread_id)
    return {"checkpoints": checkpoints}


@router.post(
    "/threads/{thread_id}/replay",
    response_model=TutorTimeTravelOut,
)
async def thread_replay(
    thread_id: str,
    body: TutorReplayIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    await GetTutorThreadService(session, student.id, thread_id).process()
    return await replay_from_checkpoint(thread_id, body.checkpointId)


@router.post(
    "/threads/{thread_id}/fork",
    response_model=TutorTimeTravelOut,
)
async def thread_fork(
    thread_id: str,
    body: TutorForkIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    await GetTutorThreadService(session, student.id, thread_id).process()
    return await fork_from_checkpoint(
        thread_id, body.checkpointId, question=body.question
    )


@router.post("/messages/{message_id}/feedback", response_model=OkOut)
async def message_feedback(
    message_id: str,
    body: TutorFeedbackIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await TutorFeedbackService(session, student.id, message_id, body).process()
