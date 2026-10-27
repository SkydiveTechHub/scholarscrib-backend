from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api.schemas import TopicAnswerIn, TopicAnswersIn
from app.services.assessments import service as assessments
from app.services.learning import service as learning

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def question(question_id="q1", topic_id="t1", answer="A"):
    return SimpleNamespace(
        id=question_id,
        topic_id=topic_id,
        subject_id="s1",
        difficulty="BASIC",
        correct_answer=answer,
    )


class Recorder:
    """Records the order of calls made by the code under test."""

    def __init__(self):
        self.calls = []
        self.logged = []


@pytest.fixture
def recorder():
    return Recorder()


# --- assessments -----------------------------------------------------------


def patch_assessments(monkeypatch, recorder):
    async def fake_record(session, student_id, source, answered):
        recorder.calls.append("predict")
        recorder.logged.extend((source, item) for item in answered)

    async def fake_add(session, entity):
        recorder.calls.append("event")

    async def fake_response_add(session, entity):
        recorder.calls.append("response")

    from app.services.learning import prediction_log

    monkeypatch.setattr(prediction_log, "record_predictions", fake_record)
    monkeypatch.setattr(assessments.learning_events_repository, "add", fake_add)
    monkeypatch.setattr(assessments.responses_repository, "add", fake_response_add)
    monkeypatch.setattr(assessments, "is_coverage", lambda _question: False)


def submit_service():
    service = assessments.SubmitAttemptService.__new__(assessments.SubmitAttemptService)
    service.session = object()
    service.student_id = "stu"
    return service


async def test_assessment_predictions_are_logged_before_any_event(
    monkeypatch, recorder
):
    patch_assessments(monkeypatch, recorder)
    rows = [
        {
            "question": question("q1"),
            "selected": "A",
            "correct": True,
            "seconds": 10,
            "flagged": False,
        },
        {
            "question": question("q2"),
            "selected": "B",
            "correct": False,
            "seconds": 12,
            "flagged": False,
        },
    ]
    await submit_service()._persist_responses(SimpleNamespace(id="a1"), rows, NOW)
    assert recorder.calls[0] == "predict"
    assert recorder.calls.count("predict") == 1
    assert recorder.calls.count("event") == 2
    assert {item.question_id for _source, item in recorder.logged} == {"q1", "q2"}
    assert {source for source, _item in recorder.logged} == {"ASSESSMENT"}


async def test_unanswered_questions_are_not_logged(monkeypatch, recorder):
    patch_assessments(monkeypatch, recorder)
    rows = [
        {
            "question": question("q1"),
            "selected": None,
            "correct": False,
            "seconds": 0,
            "flagged": False,
        },
    ]
    await submit_service()._persist_responses(SimpleNamespace(id="a1"), rows, NOW)
    assert recorder.calls[0] == "predict"
    assert len(recorder.logged) == 1
    assert recorder.logged[0][1].correct is None


async def test_unmapped_questions_are_passed_with_no_topic(monkeypatch, recorder):
    patch_assessments(monkeypatch, recorder)
    rows = [
        {
            "question": question("q1", topic_id=None),
            "selected": "A",
            "correct": True,
            "seconds": 5,
            "flagged": False,
        },
    ]
    await submit_service()._persist_responses(SimpleNamespace(id="a1"), rows, NOW)
    assert recorder.logged[0][1].topic_id is None


# --- lesson checks ---------------------------------------------------------


def patch_learning(monkeypatch, recorder, bank):
    async def fake_record(session, student_id, source, answered):
        recorder.calls.append("predict")
        recorder.logged.extend((source, item) for item in answered)

    async def fake_add(session, entity):
        recorder.calls.append("event")

    async def fake_recorded(session, student_id, topic_id, kind):
        return set()

    async def fake_subject(session, key):
        return SimpleNamespace(id="s1")

    async def fake_topic(session, subject_id, slug):
        return SimpleNamespace(id="t1", subject_id="s1")

    async def fake_many(session, statement):
        return list(bank)

    async def fake_checks(self, topic):
        return {}

    monkeypatch.setattr(learning, "record_predictions", fake_record)
    monkeypatch.setattr(learning.learning_events_repository, "add", fake_add)
    monkeypatch.setattr(
        learning.learning_events_repository, "recorded_sources", fake_recorded
    )
    monkeypatch.setattr(learning.subjects_repository, "by_id_or_slug", fake_subject)
    monkeypatch.setattr(learning.topics_repository, "by_slug", fake_topic)
    monkeypatch.setattr(learning.questions_repository, "many", fake_many)
    monkeypatch.setattr(
        learning.RecordTopicAnswersService, "_lesson_checks", fake_checks
    )


class FlushSession:
    async def flush(self):
        pass


async def test_lesson_predictions_are_logged_once_before_events_and_deduped(
    monkeypatch, recorder
):
    patch_learning(monkeypatch, recorder, [question("q1"), question("q2")])
    body = TopicAnswersIn(
        subjectId="maths",
        topic="algebra",
        answers=[
            TopicAnswerIn(questionId="q1", selectedAnswer="A"),
            TopicAnswerIn(questionId="q1", selectedAnswer="B"),
            TopicAnswerIn(questionId="q2", firstTry=False),
        ],
    )
    result = await learning.RecordTopicAnswersService(
        FlushSession(), "stu", body
    ).process()
    assert result == {"recorded": 2}
    assert recorder.calls == ["predict", "event", "event"]
    logged = {item.question_id: item.correct for _source, item in recorder.logged}
    assert logged == {"q1": True, "q2": False}
    assert {source for source, _item in recorder.logged} == {"LESSON_CHECK"}


async def test_lesson_blank_answer_is_logged_unknown_but_event_is_unchanged(
    monkeypatch, recorder
):
    patch_learning(monkeypatch, recorder, [question("q1")])
    body = TopicAnswersIn(
        subjectId="maths",
        topic="algebra",
        answers=[TopicAnswerIn(questionId="q1")],
    )
    result = await learning.RecordTopicAnswersService(
        FlushSession(), "stu", body
    ).process()
    assert result == {"recorded": 1}
    assert recorder.calls == ["predict", "event"]
    assert [item.correct for _source, item in recorder.logged] == [None]
