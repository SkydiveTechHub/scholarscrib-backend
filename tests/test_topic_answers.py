from types import SimpleNamespace

import pytest

from app.api.schemas import TopicAnswerIn, TopicAnswersIn
from app.services.learning import service as learning_service

SUBJECT = SimpleNamespace(id="subj-1", slug="physics")
TOPIC = SimpleNamespace(id="topic-gas", slug="gas-laws", subject_id="subj-1")
BANK_Q = SimpleNamespace(
    id="bank-1", topic_id="topic-gas", correct_answer="B", difficulty="ADVANCED"
)
LESSON = SimpleNamespace(
    blocks=[
        {"id": "check-1", "type": "check", "answer": "C"},
        {"id": "check-2", "type": "check", "answer": "A"},
        {"id": "concept-1", "type": "concept", "text": "x"},
    ]
)


class _Events(list):
    already: set


class FakeSession:
    async def flush(self):
        pass


@pytest.fixture
def added(monkeypatch):
    events = _Events()
    already: set[str] = set()

    async def by_id_or_slug(_s, key):
        return SUBJECT if key in {"physics", "subj-1"} else None

    async def by_slug(_s, _subject_id, slug):
        return TOPIC if slug == "gas-laws" else None

    async def by_id(_s, _id):
        return None

    async def many(_s, _statement):
        return [BANK_Q]

    async def latest_for_topic(_s, _topic_id):
        return LESSON

    async def recorded_sources(_s, _student, _topic, _kind):
        return set(already)

    async def add(_s, event, **_kw):
        events.append(event)

    r = learning_service
    monkeypatch.setattr(r.subjects_repository, "by_id_or_slug", by_id_or_slug)
    monkeypatch.setattr(r.topics_repository, "by_slug", by_slug)
    monkeypatch.setattr(r.topics_repository, "by_id", by_id)
    monkeypatch.setattr(r.questions_repository, "many", many)
    monkeypatch.setattr(r.lessons_repository, "latest_for_topic", latest_for_topic)
    monkeypatch.setattr(r.learning_events_repository, "recorded_sources", recorded_sources)
    monkeypatch.setattr(r.learning_events_repository, "add", add)
    events.already = already
    return events


async def _run(*answers):
    body = TopicAnswersIn(
        subjectId="physics", topic="gas-laws", answers=list(answers)
    )
    return await learning_service.RecordTopicAnswersService(
        FakeSession(), "student-1", body
    ).process()


@pytest.mark.asyncio
async def test_bank_answer_is_graded_against_the_stored_answer(added):
    result = await _run(
        TopicAnswerIn(questionId="bank-1", selectedAnswer="A", firstTry=True)
    )
    assert result == {"recorded": 1}
    event = added[0]
    assert (event.kind, event.correct, event.difficulty) == (
        "QUESTION_ANSWERED",
        False,
        "ADVANCED",
    )


@pytest.mark.asyncio
async def test_practice_first_try_is_used_when_no_choice_is_sent(added):
    await _run(TopicAnswerIn(questionId="bank-1", firstTry=True))
    assert added[0].correct is True


@pytest.mark.asyncio
async def test_lesson_check_scores_one_or_zero(added):
    await _run(
        TopicAnswerIn(questionId="check-1", selectedAnswer="C"),
        TopicAnswerIn(questionId="check-2", selectedAnswer="D"),
    )
    assert [(e.kind, e.source_id, e.score) for e in added] == [
        ("LESSON_BLOCK_COMPLETED", "check-1", 1.0),
        ("LESSON_BLOCK_COMPLETED", "check-2", 0.0),
    ]


@pytest.mark.asyncio
async def test_a_lesson_check_already_recorded_is_not_counted_again(added):
    added.already.add("check-1")
    result = await _run(TopicAnswerIn(questionId="check-1", selectedAnswer="C"))
    assert result == {"recorded": 0}
    assert added == []


@pytest.mark.asyncio
async def test_unknown_ids_and_non_checks_are_ignored(added):
    result = await _run(
        TopicAnswerIn(questionId="nope", selectedAnswer="A"),
        TopicAnswerIn(questionId="concept-1", selectedAnswer="A"),
    )
    assert result == {"recorded": 0}
