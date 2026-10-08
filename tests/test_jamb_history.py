from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.errors import ApiError
from app.services.assessments import jamb
from app.services.assessments.jamb import GetJambHistoryService

ENGLISH = SimpleNamespace(id="eng")


def _sitting(attempt_id: str, assessment_id: str, year: int, day: int, score: float):
    attempt = SimpleNamespace(
        id=attempt_id,
        completed_at=datetime(2026, 3, day, tzinfo=UTC),
        percentage=score / 4,
        score=score,
        total_marks=400.0,
    )
    return attempt, SimpleNamespace(id=assessment_id, exam_year=year)


@pytest.fixture
def stub(monkeypatch):
    sittings: list = []
    sets: dict[str, set[str]] = {}

    async def english(_session):
        return ENGLISH

    async def completed(_session, _student_id, **_kwargs):
        return sittings

    async def subject_sets(_session, _ids):
        return sets

    monkeypatch.setattr(jamb, "_english", english)
    monkeypatch.setattr(jamb.attempts_repository, "completed_cbt_sittings", completed)
    monkeypatch.setattr(
        jamb.assessment_questions_repository, "subject_sets", subject_sets
    )
    return SimpleNamespace(sittings=sittings, sets=sets)


def _service(ids=("phy", "chem", "bio")):
    return GetJambHistoryService(None, "student-1", list(ids))


async def test_only_sittings_with_the_same_four_subjects_count(stub):
    stub.sittings.extend(
        [
            _sitting("a1", "s1", 2020, 1, 200),
            _sitting("a2", "s2", 2020, 2, 260),
            _sitting("a3", "s3", 2020, 3, 300),  # different subjects
        ]
    )
    stub.sets.update(
        {
            "s1": {"eng", "phy", "chem", "bio"},
            "s2": {"eng", "phy", "chem", "bio"},
            "s3": {"eng", "phy", "chem", "econ"},
        }
    )

    result = await _service().process()

    assert [y["year"] for y in result["years"]] == [2020]
    attempts = result["years"][0]["attempts"]
    assert [a["attemptId"] for a in attempts] == ["a1", "a2"]
    assert attempts[1]["score"] == 260
    assert attempts[1]["totalMarks"] == 400.0


async def test_no_matching_sittings_gives_no_years(stub):
    stub.sittings.append(_sitting("a1", "s1", 2020, 1, 200))
    stub.sets["s1"] = {"eng", "phy", "chem", "econ"}

    assert await _service().process() == {"years": []}


async def test_needs_exactly_three_distinct_subjects(stub):
    with pytest.raises(ApiError) as error:
        await _service(("phy", "chem")).process()
    assert error.value.status_code == 400
