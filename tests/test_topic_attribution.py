"""Unit tests for Jev topic attribution after assessment submit."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.learning import topic_attribution as attr_mod
from app.services.learning.topic_attribution import (
    MAX_TOPICS_PER_BATCH,
    AttributeAttemptTopicsService,
    attribute_attempt_topics_safe,
)


def _choice_batch(mapping: dict[str, tuple[str, float]]):
    """Build a fake ClassifierResponse.

    The new per-question call always uses the key ``"topic"`` for the single
    Choice in each request, so ``mapping`` should be ``{"topic": (choice_id,
    confidence)}``.  Tests that were written against the old batch shape (keyed
    by question ID) still work by passing a single-entry dict; the helper
    ignores the key name and just reads the value.
    """
    choices = {
        key: SimpleNamespace(
            type="choice",
            choice=choice,
            confidence=confidence,
            probabilities={choice: confidence},
        )
        for key, (choice, confidence) in mapping.items()
    }
    return SimpleNamespace(choices=choices, nouls={}, answers=choices)


@pytest.fixture
def jev_on(monkeypatch):
    monkeypatch.setattr(attr_mod.settings, "jev_enabled", True)
    monkeypatch.setattr(attr_mod.settings, "typesafe_api_key", "test-key")
    monkeypatch.setattr(attr_mod.settings, "jev_attribution_threshold", 0.55)


@pytest.mark.asyncio
async def test_skips_when_jev_off(monkeypatch):
    monkeypatch.setattr(attr_mod.settings, "jev_enabled", False)
    monkeypatch.setattr(attr_mod.settings, "typesafe_api_key", None)
    result = await AttributeAttemptTopicsService(
        MagicMock(), "student-1", "attempt-1"
    ).process()
    assert result == {"attributed": 0, "skipped": "jev_off"}


@pytest.mark.asyncio
async def test_skips_when_all_questions_mapped(monkeypatch, jev_on):
    attempt = SimpleNamespace(
        id="attempt-1", student_id="student-1", assessment_id="a1"
    )
    paper = [
        SimpleNamespace(
            id="q1",
            subject_id="bio",
            topic_id="already",
            question_text="What is a cell?",
            options={"A": "x"},
            is_coverage=False,
        )
    ]
    monkeypatch.setattr(
        attr_mod.attempts_repository, "by_id", AsyncMock(return_value=attempt)
    )
    monkeypatch.setattr(attr_mod, "_paper_questions", AsyncMock(return_value=paper))
    result = await AttributeAttemptTopicsService(
        MagicMock(), "student-1", "attempt-1"
    ).process()
    assert result == {"attributed": 0, "skipped": "all_mapped"}


@pytest.mark.asyncio
async def test_accepts_high_confidence_choice_and_persists(monkeypatch, jev_on):
    attempt = SimpleNamespace(
        id="attempt-1", student_id="student-1", assessment_id="a1"
    )
    question = SimpleNamespace(
        id="q1",
        subject_id="bio",
        topic_id=None,
        question_text="What is a living thing?",
        options={"A": "rock", "B": "plant"},
        is_coverage=False,
    )
    topic = SimpleNamespace(id="topic-living", title="Living things", slug="living")
    subject = SimpleNamespace(id="bio", name="Biology")
    session = MagicMock()

    monkeypatch.setattr(
        attr_mod.attempts_repository, "by_id", AsyncMock(return_value=attempt)
    )
    monkeypatch.setattr(
        attr_mod, "_paper_questions", AsyncMock(return_value=[question])
    )
    monkeypatch.setattr(
        attr_mod.users_repository,
        "by_id",
        AsyncMock(return_value=SimpleNamespace(class_level="SS1")),
    )
    monkeypatch.setattr(
        attr_mod.subjects_repository, "by_id", AsyncMock(return_value=subject)
    )
    monkeypatch.setattr(attr_mod, "MIN_CLASS_TOPICS", 1)
    monkeypatch.setattr(
        attr_mod.topics_repository,
        "with_levels",
        AsyncMock(return_value=[(topic, "SS1", "FIRST")]),
    )
    monkeypatch.setattr(
        attr_mod,
        "decide_batch",
        AsyncMock(return_value=_choice_batch({"q1": ("topic-living", 0.91)})),
    )
    update_events = AsyncMock()
    update_questions = AsyncMock()
    monkeypatch.setattr(
        attr_mod.learning_events_repository, "update_where", update_events
    )
    monkeypatch.setattr(attr_mod.questions_repository, "update_where", update_questions)
    fold = AsyncMock(return_value={})
    monkeypatch.setattr(
        attr_mod,
        "GetTopicMasteryService",
        lambda *_a, **_k: SimpleNamespace(process=fold),
    )

    result = await AttributeAttemptTopicsService(
        session, "student-1", "attempt-1"
    ).process()

    assert result["attributed"] == 1
    assert result["topicIds"] == ["topic-living"]
    update_events.assert_awaited()
    update_questions.assert_awaited()
    fold.assert_awaited()


@pytest.mark.asyncio
async def test_rejects_none_and_low_confidence(monkeypatch, jev_on):
    attempt = SimpleNamespace(
        id="attempt-1", student_id="student-1", assessment_id="a1"
    )
    questions = [
        SimpleNamespace(
            id="q1",
            subject_id="bio",
            topic_id=None,
            question_text="Ambiguous",
            options={},
            is_coverage=False,
        ),
        SimpleNamespace(
            id="q2",
            subject_id="bio",
            topic_id=None,
            question_text="Uncertain",
            options={},
            is_coverage=False,
        ),
    ]
    topic = SimpleNamespace(id="topic-living", title="Living things", slug="living")
    subject = SimpleNamespace(id="bio", name="Biology")

    monkeypatch.setattr(
        attr_mod.attempts_repository, "by_id", AsyncMock(return_value=attempt)
    )
    monkeypatch.setattr(attr_mod, "_paper_questions", AsyncMock(return_value=questions))
    monkeypatch.setattr(
        attr_mod.users_repository,
        "by_id",
        AsyncMock(return_value=SimpleNamespace(class_level=None)),
    )
    monkeypatch.setattr(
        attr_mod.subjects_repository, "by_id", AsyncMock(return_value=subject)
    )
    monkeypatch.setattr(
        attr_mod.topics_repository,
        "for_subject",
        AsyncMock(return_value=[topic]),
    )
    # Single batched call; q1 returns "none", q2 returns below-threshold.
    # Both are rejected. Response is keyed by question ID.
    monkeypatch.setattr(
        attr_mod,
        "decide_batch",
        AsyncMock(
            return_value=_choice_batch(
                {"q1": ("none", 0.99), "q2": ("topic-living", 0.4)}
            )
        ),
    )
    update_events = AsyncMock()
    monkeypatch.setattr(
        attr_mod.learning_events_repository, "update_where", update_events
    )
    monkeypatch.setattr(attr_mod.questions_repository, "update_where", AsyncMock())

    result = await AttributeAttemptTopicsService(
        MagicMock(), "student-1", "attempt-1"
    ).process()

    assert result["attributed"] == 0
    update_events.assert_not_awaited()


# ---------------------------------------------------------------------------
# Threshold boundary: jev_attribution_threshold (0.55), not jev_confidence_threshold (0.7) #noqa
# ---------------------------------------------------------------------------


def _common_patches(monkeypatch, jev_on, question, subject, topic, batch_response):
    """Wire up all the external deps for a single-question attribution run."""
    attempt = SimpleNamespace(
        id="attempt-1", student_id="student-1", assessment_id="a1"
    )
    monkeypatch.setattr(
        attr_mod.attempts_repository, "by_id", AsyncMock(return_value=attempt)
    )
    monkeypatch.setattr(
        attr_mod, "_paper_questions", AsyncMock(return_value=[question])
    )
    monkeypatch.setattr(
        attr_mod.users_repository,
        "by_id",
        AsyncMock(return_value=SimpleNamespace(class_level=None)),
    )
    monkeypatch.setattr(
        attr_mod.subjects_repository, "by_id", AsyncMock(return_value=subject)
    )
    monkeypatch.setattr(
        attr_mod.topics_repository, "for_subject", AsyncMock(return_value=[topic])
    )
    monkeypatch.setattr(
        attr_mod, "decide_batch", AsyncMock(return_value=batch_response)
    )
    monkeypatch.setattr(
        attr_mod.learning_events_repository, "update_where", AsyncMock()
    )
    monkeypatch.setattr(attr_mod.questions_repository, "update_where", AsyncMock())
    monkeypatch.setattr(
        attr_mod,
        "GetTopicMasteryService",
        lambda *_a, **_k: SimpleNamespace(process=AsyncMock(return_value={})),
    )


@pytest.mark.asyncio
async def test_accepts_confidence_at_attribution_threshold(monkeypatch, jev_on):
    """A choice at exactly the attribution threshold (0.55) is accepted."""
    question = SimpleNamespace(
        id="q1",
        subject_id="eng",
        topic_id=None,
        question_text="Define a noun.",
        options={},
        is_coverage=False,
    )
    topic = SimpleNamespace(id="topic-grammar", title="Grammar", slug="grammar")
    subject = SimpleNamespace(id="eng", name="English")
    batch = _choice_batch({"q1": ("topic-grammar", 0.55)})

    _common_patches(monkeypatch, jev_on, question, subject, topic, batch)

    result = await AttributeAttemptTopicsService(
        MagicMock(), "student-1", "attempt-1"
    ).process()

    assert result["attributed"] == 1
    assert "topic-grammar" in result["topicIds"]


@pytest.mark.asyncio
async def test_rejects_confidence_just_below_attribution_threshold(monkeypatch, jev_on):
    """A choice just below the attribution threshold (0.54) is rejected."""
    question = SimpleNamespace(
        id="q1",
        subject_id="eng",
        topic_id=None,
        question_text="Define an adverb.",
        options={},
        is_coverage=False,
    )
    topic = SimpleNamespace(id="topic-grammar", title="Grammar", slug="grammar")
    subject = SimpleNamespace(id="eng", name="English")
    batch = _choice_batch({"topic": ("topic-grammar", 0.54)})

    _common_patches(monkeypatch, jev_on, question, subject, topic, batch)
    update_events = AsyncMock()
    monkeypatch.setattr(
        attr_mod.learning_events_repository, "update_where", update_events
    )

    result = await AttributeAttemptTopicsService(
        MagicMock(), "student-1", "attempt-1"
    ).process()

    assert result["attributed"] == 0
    update_events.assert_not_awaited()


@pytest.mark.asyncio
async def test_attribution_threshold_is_lower_than_routing_threshold(monkeypatch):
    """jev_attribution_threshold must be strictly less than jev_confidence_threshold
    so unmapped questions get a broader acceptance window than intent routing."""
    from app.core.config import settings

    assert settings.jev_attribution_threshold < settings.jev_confidence_threshold


# ---------------------------------------------------------------------------
# attribute_attempt_topics_safe: outcome logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_wrapper_logs_skipped_outcome(monkeypatch, caplog):
    """When attribution is skipped (jev off), the reason is info-logged."""
    import logging

    monkeypatch.setattr(attr_mod.settings, "jev_enabled", False)
    monkeypatch.setattr(attr_mod.settings, "typesafe_api_key", None)

    session = MagicMock()
    with caplog.at_level(logging.INFO, logger="app"):
        await attribute_attempt_topics_safe(session, "student-1", "attempt-1")

    assert any("jev_off" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_safe_wrapper_logs_attributed_count(monkeypatch, caplog, jev_on):
    """When attribution succeeds, the count of attributed topics is info-logged."""
    import logging

    monkeypatch.setattr(
        attr_mod,
        "AttributeAttemptTopicsService",
        lambda *_a, **_k: SimpleNamespace(
            process=AsyncMock(
                return_value={"attributed": 3, "topicIds": ["t1", "t2", "t3"]}
            )
        ),
    )

    session = MagicMock()
    with caplog.at_level(logging.INFO, logger="app"):
        await attribute_attempt_topics_safe(session, "student-1", "attempt-1")

    assert any("3" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_safe_wrapper_logs_exception_without_raising(monkeypatch, caplog):
    """Exceptions inside attribution are logged but never propagated."""
    import logging

    monkeypatch.setattr(
        attr_mod,
        "AttributeAttemptTopicsService",
        lambda *_a, **_k: SimpleNamespace(
            process=AsyncMock(side_effect=RuntimeError("boom"))
        ),
    )
    monkeypatch.setattr(attr_mod.settings, "jev_enabled", True)
    monkeypatch.setattr(attr_mod.settings, "typesafe_api_key", "test-key")

    session = MagicMock()
    with caplog.at_level(logging.ERROR, logger="app"):
        await attribute_attempt_topics_safe(session, "student-1", "attempt-1")

    assert any(
        "Topic attribution failed" in record.message for record in caplog.records
    )


# ---------------------------------------------------------------------------
# _after_submit: isolated steps — attribution runs even when earlier steps fail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_submit_runs_attribution_even_when_achievements_fail(monkeypatch):
    """A crash in AwardAchievementsService must not prevent attribution."""
    import app.api.student.assessments as route_mod

    attribution_called = {"n": 0}

    async def boom_awards(session, student_id):
        raise RuntimeError("awards exploded")

    async def fake_attribution(session, student_id, attempt_id):
        attribution_called["n"] += 1

    monkeypatch.setattr(
        route_mod,
        "AwardAchievementsService",
        lambda session, sid: SimpleNamespace(process=lambda: boom_awards(session, sid)),
    )
    monkeypatch.setattr(
        route_mod,
        "MarkStudyPlanItemService",
        lambda *_a: SimpleNamespace(process=AsyncMock()),
    )
    monkeypatch.setattr(route_mod, "attribute_attempt_topics_safe", fake_attribution)

    # Patch get_session to return a context manager with a fake session
    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def begin_nested(self):
            return _FakeSavepoint()

        async def commit(self):
            pass

        async def rollback(self):
            pass

    class _FakeSavepoint:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, *_):
            return False  # don't suppress

    monkeypatch.setattr(route_mod, "get_session", lambda: _FakeSession())

    from app.api.student.assessments import _after_submit

    await _after_submit("student-1", "attempt-1", "TOPIC_QUIZ", None)

    assert attribution_called["n"] == 1


@pytest.mark.asyncio
async def test_after_submit_runs_attribution_even_when_plan_mark_fails(monkeypatch):
    """A crash in MarkStudyPlanItemService must not prevent attribution."""
    import app.api.student.assessments as route_mod

    attribution_called = {"n": 0}

    async def fake_attribution(session, student_id, attempt_id):
        attribution_called["n"] += 1

    monkeypatch.setattr(
        route_mod,
        "AwardAchievementsService",
        lambda *_a: SimpleNamespace(process=AsyncMock()),
    )
    monkeypatch.setattr(
        route_mod,
        "MarkStudyPlanItemService",
        lambda *_a: SimpleNamespace(
            process=AsyncMock(side_effect=RuntimeError("plan exploded"))
        ),
    )
    monkeypatch.setattr(route_mod, "attribute_attempt_topics_safe", fake_attribution)

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def begin_nested(self):
            return _FakeSavepoint()

        async def commit(self):
            pass

        async def rollback(self):
            pass

    class _FakeSavepoint:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, *_):
            return False

    monkeypatch.setattr(route_mod, "get_session", lambda: _FakeSession())

    from app.api.student.assessments import _after_submit

    await _after_submit("student-1", "attempt-1", "TOPIC_QUIZ", None)

    assert attribution_called["n"] == 1


async def test_max_topics_per_batch_caps_criteria(monkeypatch, jev_on):
    """_map_chunk only sends MAX_TOPICS_PER_BATCH topics to Jev even when
    the subject has more distinct topics available."""
    n = MAX_TOPICS_PER_BATCH + 5
    topics = [SimpleNamespace(id=f"t{i}", title=f"Topic {i}") for i in range(n)]

    captured_criteria: dict = {}

    async def fake_decide_batch(state, questions):
        first_q = next(iter(questions.values()))
        captured_criteria["count"] = len(first_q.criteria)
        return SimpleNamespace(choices={})

    monkeypatch.setattr(attr_mod, "decide_batch", fake_decide_batch)

    svc = AttributeAttemptTopicsService.__new__(AttributeAttemptTopicsService)
    svc.session = MagicMock()
    svc.student_id = "student-1"
    svc.attempt_id = "attempt-1"

    question = SimpleNamespace(
        id="q1",
        subject_id="maths",
        topic_id=None,
        question_text="What is 2+2?",
        options={},
    )

    await svc._map_chunk(
        subject_name="Mathematics",
        class_level="SS2",
        topics=topics,
        questions=[question],
    )

    # criteria includes the capped topics + the "none" option
    assert captured_criteria["count"] == MAX_TOPICS_PER_BATCH + 1
