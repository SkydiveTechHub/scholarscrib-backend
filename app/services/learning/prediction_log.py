"""Log what the mastery engine believed just before a student answered.

Call `record_predictions` BEFORE the submission's own LearningEvent rows are
added, so an answer cannot leak into its own prediction. A failure here is
logged and swallowed: it must never fail the student's submission.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import cuid
from app.core.logger import logger
from app.database.models import PredictionLog
from app.database.repositories.prediction_log import prediction_log_repository
from app.services.learning.calibration import PredictionRow, calibration_report
from app.services.learning.evidence import SCORING_VERSION
from app.services.learning.mastery import TopicState
from app.services.learning.mastery_store import GetTopicMasteryService


@dataclass(frozen=True)
class AnsweredQuestion:
    topic_id: str | None
    subject_id: str | None
    question_id: str
    difficulty: str | None
    correct: bool | None
    occurred_at: datetime


def build_rows(
    student_id: str,
    source: str,
    answered: list[AnsweredQuestion],
    states: dict[str, TopicState],
) -> list[PredictionLog]:
    rows = []
    for item in answered:
        if not item.topic_id or item.correct is None:
            continue
        state = states.get(item.topic_id)
        if state is None:
            continue
        rows.append(
            PredictionLog(
                id=cuid(),
                student_id=student_id,
                subject_id=item.subject_id,
                topic_id=item.topic_id,
                question_id=item.question_id,
                source=source,
                predicted_p=state.score,
                confidence=state.confidence,
                acc_observations=state.acc_observations,
                difficulty=item.difficulty,
                correct=item.correct,
                scoring_version=SCORING_VERSION,
                occurred_at=item.occurred_at,
            )
        )
    return rows


async def record_predictions(
    session: AsyncSession,
    student_id: str,
    source: str,
    answered: list[AnsweredQuestion],
) -> None:
    topic_ids = sorted(
        {
            item.topic_id
            for item in answered
            if item.topic_id and item.correct is not None
        }
    )
    if not topic_ids:
        return
    try:
        async with session.begin_nested():
            states = await GetTopicMasteryService(
                session, student_id, topic_ids
            ).process()
            rows = build_rows(student_id, source, answered, states)
            await prediction_log_repository.add_many(session, rows)
    except Exception:
        logger.exception("Prediction log write failed for %s", student_id)


class GetCalibrationReportService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        scoring_version: int | None = None,
        subject_id: str | None = None,
        since: datetime | None = None,
        source: str | None = None,
    ) -> None:
        self.session = session
        self.scoring_version = scoring_version
        self.subject_id = subject_id
        self.since = since
        self.source = source

    async def process(self) -> dict:
        found = await prediction_log_repository.report_rows(
            self.session,
            scoring_version=self.scoring_version,
            subject_id=self.subject_id,
            since=self.since,
            source=self.source,
        )
        return calibration_report(
            PredictionRow(predicted, correct, confidence, difficulty)
            for predicted, correct, confidence, difficulty in found
        )
