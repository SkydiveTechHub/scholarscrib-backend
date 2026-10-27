from types import SimpleNamespace

import pytest

from app.services import question_source


def cov(question_id, topic_id="t1"):
    return SimpleNamespace(
        id=question_id,
        subject_id="s1",
        topic_id=topic_id,
        exam_type="WAEC",
        exam_year=None,
        question_number=None,
        question_text="Q",
        question_image=None,
        question_type="OBJECTIVE",
        options={"A": "1"},
        correct_answer="A",
        explanation=None,
        solution_image=None,
        difficulty="BASIC",
        marks=1,
    )


def raw(question_id):
    return SimpleNamespace(id=question_id, topic_id="t1", difficulty="BASIC")


class Calls:
    def __init__(self, coverage, raw_rows):
        self.coverage = coverage
        self.raw_rows = raw_rows
        self.coverage_args = None
        self.raw_args = None


@pytest.fixture
def calls(monkeypatch):
    holder = Calls([], [])

    async def fake_coverage(session, **kwargs):
        holder.coverage_args = kwargs
        return holder.coverage

    async def fake_raw(session, **kwargs):
        holder.raw_args = kwargs
        return holder.raw_rows

    monkeypatch.setattr(
        question_source.coverage_questions_repository, "pick_for_topics", fake_coverage
    )
    monkeypatch.setattr(
        question_source.questions_repository, "pick_objective", fake_raw
    )
    return holder


async def test_coverage_alone_fills_the_request_and_raw_is_not_asked(calls):
    calls.coverage = [cov("c1"), cov("c2")]
    picked = await question_source.pick_questions(
        object(), subject_id="s1", count=2, topic_ids=["t1"]
    )
    assert [item.id for item in picked] == ["c1", "c2"]
    assert all(item.is_coverage for item in picked)
    assert calls.raw_args is None


async def test_raw_tops_up_the_shortfall_and_excludes_coverage_ids(calls):
    calls.coverage = [cov("c1"), cov("c2")]
    calls.raw_rows = [raw("r1"), raw("r2"), raw("r3")]
    picked = await question_source.pick_questions(
        object(), subject_id="s1", count=5, topic_ids=["t1"], exclude={"seen"}
    )
    assert [item.id for item in picked] == ["c1", "c2", "r1", "r2", "r3"]
    assert calls.raw_args["count"] == 3
    assert calls.raw_args["exclude"] == {"seen", "c1", "c2"}
    assert calls.coverage_args["exclude"] == {"seen"}


async def test_no_coverage_behaves_like_raw_only(calls):
    calls.raw_rows = [raw("r1")]
    picked = await question_source.pick_questions(
        object(), subject_id="s1", count=1, topic_ids=["t1"]
    )
    assert [item.id for item in picked] == ["r1"]
    assert calls.raw_args["count"] == 1


async def test_class_levels_skip_coverage(calls):
    calls.coverage = [cov("c1")]
    calls.raw_rows = [raw("r1")]
    picked = await question_source.pick_questions(
        object(), subject_id="s1", count=1, class_levels=[("SS1", "FIRST")]
    )
    assert [item.id for item in picked] == ["r1"]
    assert calls.coverage_args is None
    assert calls.raw_args["class_levels"] == [("SS1", "FIRST")]


async def test_questions_by_id_keeps_only_the_topics_rows_from_both_tables(
    monkeypatch,
):
    async def fake_by_ids(session, ids):
        return [cov("c1", "t1"), cov("c2", "other")]

    async def fake_many(session, statement):
        return [raw("r1")]

    monkeypatch.setattr(
        question_source.coverage_questions_repository, "by_ids", fake_by_ids
    )
    monkeypatch.setattr(question_source.questions_repository, "many", fake_many)
    found = await question_source.questions_by_id(
        object(), ["c1", "c2", "r1", "missing"], "t1"
    )
    assert set(found) == {"c1", "r1"}


def test_seen_ids_query_covers_both_question_columns():
    from datetime import UTC, datetime

    from app.database.repositories.assessment import seen_question_ids_statement

    sql = str(seen_question_ids_statement("student", datetime(2026, 1, 1, tzinfo=UTC)))
    assert "coverageQuestionId" in sql and "questionId" in sql
