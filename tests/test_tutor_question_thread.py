"""Tests for question-scoped thread persistence and resume-session logic.

Covers:
- exam context (examType / examYear) snapshotted onto ChatThread at creation
- _thread_out includes the new fields
- ChatThreadRepository.for_user_question lookup
- prepare_new_thread_stream: returns existing thread id when one exists,
  None when there is no match
- start_new_thread_run: passes existing_thread_id through to _produce_tutor_run
  so the background task attaches to the existing thread instead of creating a
  new one
- TutorThreadOut response model includes examType / examYear
- CreateTutorThreadService._validate_refs returns the Question object
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.tutor.service import (
    _thread_out,
    prepare_new_thread_stream,
    start_new_thread_run,
)
from app.api.responses import TutorThreadOut
from app.api.schemas import TutorThreadCreateIn
from app.core.errors import ApiError
from app.database.models.chat import ChatThread
from app.database.repositories.chat import ChatThreadRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_thread(**overrides) -> SimpleNamespace:
    """Lightweight stand-in for ChatThread. SimpleNamespace avoids the SQLAlchemy
    ORM instrumentation that makes __new__ unusable outside a session."""
    defaults = dict(
        id="thread-1",
        user_id="user-1",
        title="Test thread",
        subject_id="sub-1",
        topic_id="topic-1",
        question_id="q-1",
        lesson_id=None,
        exam_type="WAEC",
        exam_year=2023,
        archived=False,
        active_run=None,
        run_events=None,
        created_at=None,
        updated_at=None,
    )
    return SimpleNamespace(**{**defaults, **overrides})


def _make_question(**overrides) -> SimpleNamespace:
    defaults = dict(
        id="q-1",
        exam_type="WAEC",
        exam_year=2023,
        subject_id="sub-1",
        topic_id="topic-1",
    )
    return SimpleNamespace(**{**defaults, **overrides})


def _make_body(**overrides) -> TutorThreadCreateIn:
    defaults = dict(
        questionId="q-1",
        subjectId="sub-1",
        topicId="topic-1",
        firstMessage="Explain this question",
    )
    return TutorThreadCreateIn(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# 1. _thread_out includes examType and examYear
# ---------------------------------------------------------------------------


def test_thread_out_includes_exam_fields():
    thread = _make_thread(exam_type="JAMB", exam_year=2024)
    payload = _thread_out(thread)
    assert payload["examType"] == "JAMB"
    assert payload["examYear"] == 2024


def test_thread_out_exam_fields_none_when_not_set():
    thread = _make_thread(exam_type=None, exam_year=None)
    payload = _thread_out(thread)
    assert payload["examType"] is None
    assert payload["examYear"] is None


def test_thread_out_includes_all_context_ids():
    thread = _make_thread()
    payload = _thread_out(thread)
    assert payload["questionId"] == "q-1"
    assert payload["subjectId"] == "sub-1"
    assert payload["topicId"] == "topic-1"


# ---------------------------------------------------------------------------
# 2. TutorThreadOut response model accepts the new fields
# ---------------------------------------------------------------------------


def test_tutor_thread_out_accepts_exam_fields():
    out = TutorThreadOut(id="t-1", examType="NECO", examYear=2022)
    assert out.examType == "NECO"
    assert out.examYear == 2022


def test_tutor_thread_out_exam_fields_default_to_none():
    out = TutorThreadOut(id="t-1")
    assert out.examType is None
    assert out.examYear is None


# ---------------------------------------------------------------------------
# 3. ChatThreadRepository.for_user_question
# ---------------------------------------------------------------------------


async def test_for_user_question_returns_matching_thread():
    repo = ChatThreadRepository()
    expected = _make_thread()
    mock_session = MagicMock()

    # first() is the underlying query; patch it to return our thread
    repo.first = AsyncMock(return_value=expected)

    result = await repo.for_user_question(mock_session, "user-1", "q-1")
    assert result is expected
    repo.first.assert_awaited_once()


async def test_for_user_question_returns_none_when_no_thread():
    repo = ChatThreadRepository()
    mock_session = MagicMock()
    repo.first = AsyncMock(return_value=None)

    result = await repo.for_user_question(mock_session, "user-1", "q-missing")
    assert result is None


# ---------------------------------------------------------------------------
# 4. prepare_new_thread_stream: existing thread detection
# ---------------------------------------------------------------------------


def _patch_prepare_deps(monkeypatch, *, existing_thread=None, question=None):
    """Patch all external calls that prepare_new_thread_stream makes."""
    import app.ai.tutor.service as svc

    monkeypatch.setattr(svc, "require_rag", lambda: None)
    monkeypatch.setattr(svc, "enforce_daily_message_limit", lambda _uid: None)

    # _validate_refs on CreateTutorThreadService — patch to a no-op that
    # returns the supplied question object
    async def fake_validate_refs(self):
        return question

    monkeypatch.setattr(
        svc.CreateTutorThreadService, "_validate_refs", fake_validate_refs
    )

    monkeypatch.setattr(
        svc.chat_threads_repository,
        "for_user_question",
        AsyncMock(return_value=existing_thread),
    )


async def test_prepare_returns_none_thread_id_when_no_existing(monkeypatch):
    _patch_prepare_deps(monkeypatch, existing_thread=None)

    session = MagicMock()
    body = _make_body()

    result_body, existing_id = await prepare_new_thread_stream(session, "user-1", body)

    assert existing_id is None
    assert result_body.questionId == "q-1"


async def test_prepare_returns_existing_thread_id_when_question_matches(monkeypatch):
    existing = _make_thread(id="thread-existing")
    _patch_prepare_deps(monkeypatch, existing_thread=existing)

    session = MagicMock()
    body = _make_body()

    result_body, existing_id = await prepare_new_thread_stream(session, "user-1", body)

    assert existing_id == "thread-existing"


async def test_prepare_skips_lookup_when_no_question_id(monkeypatch):
    """No questionId → lookup never runs, always returns None."""
    import app.ai.tutor.service as svc

    monkeypatch.setattr(svc, "require_rag", lambda: None)
    monkeypatch.setattr(svc, "enforce_daily_message_limit", lambda _uid: None)

    async def fake_validate_refs(self):
        return None

    monkeypatch.setattr(
        svc.CreateTutorThreadService, "_validate_refs", fake_validate_refs
    )

    lookup = AsyncMock(return_value=_make_thread())
    monkeypatch.setattr(svc.chat_threads_repository, "for_user_question", lookup)

    session = MagicMock()
    body = _make_body(questionId=None)

    _, existing_id = await prepare_new_thread_stream(session, "user-1", body)

    assert existing_id is None
    lookup.assert_not_awaited()


async def test_prepare_raises_when_first_message_missing(monkeypatch):
    import app.ai.tutor.service as svc

    monkeypatch.setattr(svc, "require_rag", lambda: None)
    monkeypatch.setattr(svc, "enforce_daily_message_limit", lambda _uid: None)

    session = MagicMock()
    body = _make_body(firstMessage=None)

    with pytest.raises(ApiError) as exc_info:
        await prepare_new_thread_stream(session, "user-1", body)

    assert exc_info.value.status_code == 400


async def test_prepare_raises_when_existing_thread_is_running(monkeypatch):
    """If the matched thread already has an active run, the stream is rejected."""
    from datetime import UTC, datetime

    # Simulate a live (non-stale) active run
    existing = _make_thread(
        id="thread-running",
        active_run={
            "runId": "run-abc",
            "status": "running",
            "beat": datetime.now(UTC).isoformat(),
        },
    )
    _patch_prepare_deps(monkeypatch, existing_thread=existing)

    import app.ai.tutor.service as svc

    # hub has no live run, but the stored run is non-stale → 409
    monkeypatch.setattr(svc.hub, "for_thread", lambda _tid: None)

    session = MagicMock()
    body = _make_body()

    with pytest.raises(ApiError) as exc_info:
        await prepare_new_thread_stream(session, "user-1", body)

    assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# 5. start_new_thread_run routes to existing thread vs. new thread
# ---------------------------------------------------------------------------


def test_start_new_thread_run_creates_fresh_thread(monkeypatch):
    """Without existing_thread_id, _produce_tutor_run is called with create=body."""
    import app.ai.tutor.service as svc

    captured: dict = {}

    def fake_produce(run, user_id, thread_id, content, *, create=None):
        captured["thread_id"] = thread_id
        captured["create"] = create
        return asyncio.sleep(0)

    # Intercept _spawn so the unawaited coroutine is closed cleanly.
    def fake_spawn(run, coro):
        coro.close()
        return run

    monkeypatch.setattr(svc, "_produce_tutor_run", fake_produce)
    monkeypatch.setattr(svc, "_spawn", fake_spawn)

    body = _make_body()
    start_new_thread_run(body, "user-1")

    assert captured["thread_id"] is None  # new thread, no id yet
    assert captured["create"] is body


def test_start_new_thread_run_attaches_to_existing_thread(monkeypatch):
    """With existing_thread_id, create=None so the task resumes the existing thread."""
    import app.ai.tutor.service as svc

    captured: dict = {}

    def fake_produce(run, user_id, thread_id, content, *, create=None):
        captured["thread_id"] = thread_id
        captured["create"] = create
        return asyncio.sleep(0)

    def fake_spawn(run, coro):
        coro.close()
        return run

    monkeypatch.setattr(svc, "_produce_tutor_run", fake_produce)
    monkeypatch.setattr(svc, "_spawn", fake_spawn)

    body = _make_body()
    start_new_thread_run(body, "user-1", existing_thread_id="thread-existing")

    assert captured["thread_id"] == "thread-existing"
    assert captured["create"] is None  # must NOT create a new thread


# ---------------------------------------------------------------------------
# 6. CreateTutorThreadService._validate_refs returns the Question
# ---------------------------------------------------------------------------


async def test_validate_refs_returns_question_when_found(monkeypatch):
    import app.ai.tutor.service as svc

    question = _make_question()

    monkeypatch.setattr(
        svc.questions_repository, "by_id", AsyncMock(return_value=question)
    )
    monkeypatch.setattr(svc.topics_repository, "by_id", AsyncMock(return_value=None))
    monkeypatch.setattr(svc.lessons_repository, "by_id", AsyncMock(return_value=None))

    from app.ai.tutor.service import CreateTutorThreadService

    session = MagicMock()
    body = _make_body(topicId=None, lessonId=None)
    svc_instance = CreateTutorThreadService(session, "user-1", body)
    result = await svc_instance._validate_refs()

    assert result is question


async def test_validate_refs_returns_none_when_no_question_id(monkeypatch):
    import app.ai.tutor.service as svc

    monkeypatch.setattr(svc.topics_repository, "by_id", AsyncMock(return_value=None))
    monkeypatch.setattr(svc.lessons_repository, "by_id", AsyncMock(return_value=None))

    from app.ai.tutor.service import CreateTutorThreadService

    session = MagicMock()
    body = _make_body(questionId=None, topicId=None, lessonId=None)
    svc_instance = CreateTutorThreadService(session, "user-1", body)
    result = await svc_instance._validate_refs()

    assert result is None


async def test_validate_refs_raises_on_unknown_question(monkeypatch):
    import app.ai.tutor.service as svc

    monkeypatch.setattr(svc.questions_repository, "by_id", AsyncMock(return_value=None))

    from app.ai.tutor.service import CreateTutorThreadService

    session = MagicMock()
    body = _make_body(topicId=None, lessonId=None)
    svc_instance = CreateTutorThreadService(session, "user-1", body)

    with pytest.raises(ApiError) as exc_info:
        await svc_instance._validate_refs()

    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# 7. Exam context is snapshotted when CreateTutorThreadService creates a thread
# ---------------------------------------------------------------------------


async def test_create_service_snapshots_exam_context(monkeypatch):
    """process() should set exam_type and exam_year from the fetched Question."""
    import app.ai.tutor.service as svc

    question = _make_question(exam_type="NECO", exam_year=2021)
    created_threads: list[ChatThread] = []

    async def fake_validate_refs(self):
        return question

    monkeypatch.setattr(
        svc.CreateTutorThreadService, "_validate_refs", fake_validate_refs
    )
    monkeypatch.setattr(svc, "require_rag", lambda: None)
    monkeypatch.setattr(svc, "enforce_daily_message_limit", lambda _uid: None)
    monkeypatch.setattr(svc, "_auto_title", AsyncMock(return_value="My title"))

    async def fake_add(session, entity, *, flush=False):
        if isinstance(entity, ChatThread):
            created_threads.append(entity)
        return entity

    monkeypatch.setattr(svc.chat_threads_repository, "add", fake_add)
    monkeypatch.setattr(svc.chat_messages_repository, "add", AsyncMock())
    monkeypatch.setattr(
        svc.chat_messages_repository, "for_thread", AsyncMock(return_value=[])
    )

    session = MagicMock()
    session.flush = AsyncMock()

    from app.ai.tutor.service import CreateTutorThreadService

    body = _make_body(firstMessage=None)
    await CreateTutorThreadService(session, "user-1", body).process()

    assert len(created_threads) == 1
    thread = created_threads[0]
    assert thread.exam_type == "NECO"
    assert thread.exam_year == 2021


async def test_create_service_exam_context_none_when_no_question(monkeypatch):
    """When no questionId, exam_type and exam_year remain None."""
    import app.ai.tutor.service as svc

    async def fake_validate_refs(self):
        return None

    monkeypatch.setattr(
        svc.CreateTutorThreadService, "_validate_refs", fake_validate_refs
    )
    monkeypatch.setattr(svc, "require_rag", lambda: None)
    monkeypatch.setattr(svc, "enforce_daily_message_limit", lambda _uid: None)

    created_threads: list[ChatThread] = []

    async def fake_add(session, entity, *, flush=False):
        if isinstance(entity, ChatThread):
            created_threads.append(entity)
        return entity

    monkeypatch.setattr(svc.chat_threads_repository, "add", fake_add)
    monkeypatch.setattr(svc.chat_messages_repository, "add", AsyncMock())
    monkeypatch.setattr(
        svc.chat_messages_repository, "for_thread", AsyncMock(return_value=[])
    )

    session = MagicMock()
    session.flush = AsyncMock()

    from app.ai.tutor.service import CreateTutorThreadService

    body = _make_body(questionId=None, firstMessage=None)
    await CreateTutorThreadService(session, "user-1", body).process()

    assert created_threads[0].exam_type is None
    assert created_threads[0].exam_year is None
