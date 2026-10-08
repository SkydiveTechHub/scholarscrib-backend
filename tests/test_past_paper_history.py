from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.errors import ApiError
from app.services.assessments import past_paper
from app.services.assessments.past_paper import GetPastPaperHistoryService

SUBJECT = SimpleNamespace(id="subj-1")


def _attempt(attempt_id: str, day: int, percentage: float):
    return SimpleNamespace(
        id=attempt_id,
        completed_at=datetime(2026, 3, day, tzinfo=UTC),
        percentage=percentage,
        score=percentage,
        total_marks=100.0,
    )


def _service(exam: str = "jamb"):
    provider = SimpleNamespace(configured=True)
    return GetPastPaperHistoryService(
        None, "student-1", subject_key="english-language", exam=exam, provider=provider
    )


@pytest.fixture
def stub(monkeypatch):
    calls: list[dict] = []
    rows: list = []

    async def resolve(_session, _provider, _key):
        return SUBJECT

    async def completed(_session, student_id, *, subject_id, exam_type):
        calls.append(
            {"student": student_id, "subject": subject_id, "exam": exam_type}
        )
        return rows

    monkeypatch.setattr(past_paper, "resolve_subject", resolve)
    monkeypatch.setattr(
        past_paper.attempts_repository, "completed_past_papers", completed
    )
    return SimpleNamespace(calls=calls, rows=rows)


async def test_groups_attempts_by_year_newest_year_first(stub):
    stub.rows.extend(
        [
            (_attempt("a1", 1, 40.0), 2011),
            (_attempt("a2", 2, 55.0), 2011),
            (_attempt("a3", 3, 70.0), 2015),
        ]
    )

    result = await _service().process()

    assert [y["year"] for y in result["years"]] == [2015, 2011]
    twenty_eleven = result["years"][1]["attempts"]
    assert [a["attemptId"] for a in twenty_eleven] == ["a1", "a2"]
    assert twenty_eleven[1]["percentage"] == 55.0
    assert stub.calls == [{"student": "student-1", "subject": "subj-1", "exam": "JAMB"}]


async def test_no_attempts_gives_no_years(stub):
    assert await _service().process() == {"years": []}


async def test_unknown_subject_gives_no_years(monkeypatch):
    async def missing(_session, _provider, _key):
        raise ApiError(404, "nope")

    monkeypatch.setattr(past_paper, "resolve_subject", missing)

    assert await _service().process() == {"years": []}


async def test_unknown_exam_is_rejected(stub):
    with pytest.raises(ApiError) as error:
        await _service(exam="nonsense").process()
    assert error.value.status_code == 400
