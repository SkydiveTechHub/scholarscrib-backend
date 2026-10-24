"""Unit tests for Jev topic attribution after assessment submit."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.learning import topic_attribution as attr_mod
from app.services.learning.topic_attribution import AttributeAttemptTopicsService


def _choice_batch(mapping: dict[str, tuple[str, float]]):
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
    monkeypatch.setattr(attr_mod.settings, "jev_confidence_threshold", 0.7)


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
