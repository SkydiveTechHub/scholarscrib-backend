"""Student AI tutor chat routes."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query

from app.ai.core.sse import sse_response
from app.ai.tutor.service import (
    CreateTutorThreadService,
    DeleteTutorThreadService,
    GetTutorThreadService,
    ListTutorMessagesService,
    ListTutorThreadsService,
    PostTutorMessageService,
    TutorFeedbackService,
    open_run_replay,
    prepare_new_thread_stream,
    resume_tutor_action,
    start_message_run,
    start_new_thread_run,
    stop_tutor_stream,
    stream_resume_tutor_action,
)
from app.ai.tutor.time_travel import (
    fork_from_checkpoint,
    list_checkpoints,
    replay_from_checkpoint,
)
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
    TutorActionResumeIn,
    TutorFeedbackIn,
    TutorForkIn,
    TutorMessageIn,
    TutorReplayIn,
    TutorStreamStopIn,
    TutorThreadCreateIn,
)
from app.database.db import AnSession

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


@router.post("/threads/stream")
async def create_thread_stream(
    body: TutorThreadCreateIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    """Create a thread with its first message and stream the reply.

    When the student already has a thread for the same question the existing
    thread is resumed instead of creating a duplicate.  The ``streamStarted``
    event always carries the authoritative ``threadId`` (new or existing).
    """
    prepared, existing_thread_id = await prepare_new_thread_stream(
        session, student.id, body
    )
    run = start_new_thread_run(
        prepared, student.id, existing_thread_id=existing_thread_id
    )
    return await sse_response(run.iter_after(0))


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
    run = await start_message_run(session, student.id, thread_id, body.content)
    return await sse_response(run.iter_after(0))


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


@router.get("/threads/{thread_id}/runs/{run_id}/stream")
async def rejoin_run_stream(
    thread_id: str,
    run_id: str,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
    last_event_id: Annotated[str | None, Header()] = None,
):
    """Replay this run after ``Last-Event-ID``, then follow it live.

    A finished run is 404. Load the thread instead.
    """
    events = await open_run_replay(
        session, student.id, thread_id, run_id, last_event_id
    )
    return await sse_response(events)


@router.post("/threads/{thread_id}/actions/resume")
async def resume_action(
    thread_id: str,
    body: TutorActionResumeIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    """Approve or reject a pending study-plan card. Optional SSE when stream=true."""
    if body.stream:
        return await sse_response(
            stream_resume_tutor_action(student.id, thread_id, body)
        )
    return await resume_tutor_action(session, student.id, thread_id, body)


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
    return await replay_from_checkpoint(
        thread_id, body.checkpointId, user_id=student.id
    )


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
        thread_id,
        body.checkpointId,
        user_id=student.id,
        question=body.question,
    )


@router.post("/messages/{message_id}/feedback", response_model=OkOut)
async def message_feedback(
    message_id: str,
    body: TutorFeedbackIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await TutorFeedbackService(session, student.id, message_id, body).process()
