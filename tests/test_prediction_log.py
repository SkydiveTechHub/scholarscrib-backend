from datetime import UTC, datetime

import pytest

from app.database.models import PredictionLog
from app.services.learning import prediction_log
from app.services.learning.evidence import SCORING_VERSION
from app.services.learning.mastery import TopicState
from app.services.learning.prediction_log import (
    AnsweredQuestion,
    build_rows,
    record_predictions,
)


def test_prediction_log_columns_use_the_camel_case_names():
    columns = {column.name for column in PredictionLog.__table__.columns}
    assert columns == {
        "id",
        "studentId",
        "subjectId",
        "topicId",
        "questionId",
        "source",
        "predictedP",
        "confidence",
        "accObservations",
        "difficulty",
        "correct",
        "scoringVersion",
        "occurredAt",
    }
    indexes = {index.name for index in PredictionLog.__table__.indexes}
    assert indexes == {
        "PredictionLog_version_occurred_idx",
        "PredictionLog_topic_idx",
    }


NOW = datetime(2026, 10, 27, tzinfo=UTC)


def answered(topic_id="t1", question_id="q1", correct=True, difficulty="BASIC"):
    return AnsweredQuestion(
        topic_id=topic_id,
        subject_id="s1",
        question_id=question_id,
        difficulty=difficulty,
        correct=correct,
        occurred_at=NOW,
    )


STATES = {
    "t1": TopicState(topic_id="t1", score=0.6, confidence=0.5, acc_observations=3),
}


def test_build_rows_copies_the_prediction_and_the_outcome():
    (found,) = build_rows("stu", "ASSESSMENT", [answered()], STATES)
    assert found.student_id == "stu"
    assert found.topic_id == "t1"
    assert found.subject_id == "s1"
    assert found.question_id == "q1"
    assert found.source == "ASSESSMENT"
    assert found.predicted_p == pytest.approx(0.6)
    assert found.confidence == pytest.approx(0.5)
    assert found.acc_observations == 3
    assert found.difficulty == "BASIC"
    assert found.correct is True
    assert found.scoring_version == SCORING_VERSION
    assert found.occurred_at == NOW


def test_build_rows_skips_unknown_topic_unknown_outcome_and_missing_state():
    rows = build_rows(
        "stu",
        "ASSESSMENT",
        [
            answered(topic_id=None),
            answered(question_id="q2", correct=None),
            answered(topic_id="gone", question_id="q3"),
        ],
        STATES,
    )
    assert rows == []


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def begin_nested(self):
        return _Nested()


class FakeRepository:
    def __init__(self):
        self.added = []

    async def add_many(self, session, rows):
        self.added.extend(rows)


@pytest.fixture
def repository(monkeypatch):
    fake = FakeRepository()
    monkeypatch.setattr(prediction_log, "prediction_log_repository", fake)
    return fake


def fake_mastery(monkeypatch, states=None, error=None, calls=None):
    class FakeMastery:
        def __init__(self, session, student_id, topic_ids=None):
            if calls is not None:
                calls.append(sorted(topic_ids))

        async def process(self):
            if error is not None:
                raise error
            return states

    monkeypatch.setattr(prediction_log, "GetTopicMasteryService", FakeMastery)


async def test_record_predictions_reads_each_topic_once_and_writes_rows(
    monkeypatch, repository
):
    calls = []
    fake_mastery(monkeypatch, states=STATES, calls=calls)
    await record_predictions(
        FakeSession(),
        "stu",
        "ASSESSMENT",
        [answered(question_id="q1"), answered(question_id="q2", correct=False)],
    )
    assert calls == [["t1"]]
    assert [row.question_id for row in repository.added] == ["q1", "q2"]


async def test_record_predictions_with_no_topics_does_nothing(monkeypatch, repository):
    calls = []
    fake_mastery(monkeypatch, states=STATES, calls=calls)
    await record_predictions(
        FakeSession(), "stu", "ASSESSMENT", [answered(topic_id=None)]
    )
    assert calls == []
    assert repository.added == []


async def test_record_predictions_swallows_a_failure(monkeypatch, repository):
    fake_mastery(monkeypatch, error=RuntimeError("db down"))
    await record_predictions(FakeSession(), "stu", "ASSESSMENT", [answered()])
    assert repository.added == []


async def test_report_service_passes_filters_and_scores_rows(monkeypatch):
    seen = {}

    class ReportRepository:
        async def report_rows(self, session, **filters):
            seen.update(filters)
            return [(0.8, True, 0.5, "BASIC"), (0.8, False, 0.5, None)]

    monkeypatch.setattr(prediction_log, "prediction_log_repository", ReportRepository())
    report = await prediction_log.GetCalibrationReportService(
        FakeSession(),
        scoring_version=3,
        subject_id="s1",
        since=NOW,
        source="ASSESSMENT",
    ).process()
    assert seen == {
        "scoring_version": 3,
        "subject_id": "s1",
        "since": NOW,
        "source": "ASSESSMENT",
    }
    assert report["overall"]["n"] == 2
    assert set(report["byDifficulty"]) == {"BASIC", "UNKNOWN"}
