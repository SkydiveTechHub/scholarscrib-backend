from sqlalchemy.dialects import postgresql

from app.database.repositories.assessment import (
    assessment_questions_repository,
    responses_repository,
)
from app.database.repositories.question import questions_repository


class Result:
    def all(self):
        return []

    def one(self):
        return (0, 0, 0)


class RecordingSession:
    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return Result()

    async def scalar(self, statement):
        self.statements.append(statement)
        return 0


def _sql(session):
    return str(session.statements[-1].compile(dialect=postgresql.dialect())).replace(
        '"', ""
    )


async def test_totals_by_subject_reads_coverage_questions_too():
    session = RecordingSession()
    await responses_repository.totals_by_subject(session, "u1")
    sql = _sql(session)
    assert "LEFT OUTER JOIN CoverageQuestion" in sql
    assert "coalesce(Question.subjectId, CoverageQuestion.subjectId)" in sql


async def test_answer_totals_and_timing_read_coverage_questions_too():
    session = RecordingSession()
    await responses_repository.answer_totals(session, "u1")
    assert "LEFT OUTER JOIN CoverageQuestion" in _sql(session)
    await responses_repository.timing_for_subject(session, "u1", "s1")
    assert "LEFT OUTER JOIN CoverageQuestion" in _sql(session)


async def test_subject_sets_and_topic_attempts_read_coverage_questions_too():
    session = RecordingSession()
    await assessment_questions_repository.subject_sets(session, ["a1"])
    assert "LEFT OUTER JOIN CoverageQuestion" in _sql(session)
    await questions_repository.count_attempted_for_topic(session, "u1", "t1")
    assert "LEFT OUTER JOIN CoverageQuestion" in _sql(session)
