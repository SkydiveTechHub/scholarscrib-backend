"""Repository for the append-only prediction log."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PredictionLog
from app.database.repositories.base import BaseRepository


class PredictionLogRepository(BaseRepository[PredictionLog]):
    model = PredictionLog

    async def add_many(
        self, session: AsyncSession, rows: list[PredictionLog]
    ) -> None:
        if not rows:
            return
        session.add_all(rows)
        await session.flush()

    async def report_rows(
        self,
        session: AsyncSession,
        *,
        scoring_version: int | None,
        subject_id: str | None,
        since: datetime | None,
        source: str | None,
    ) -> list[tuple[float, bool, float, str | None]]:
        statement = select(
            PredictionLog.predicted_p,
            PredictionLog.correct,
            PredictionLog.confidence,
            PredictionLog.difficulty,
        )
        if scoring_version is not None:
            statement = statement.where(PredictionLog.scoring_version == scoring_version)
        if subject_id is not None:
            statement = statement.where(PredictionLog.subject_id == subject_id)
        if since is not None:
            statement = statement.where(PredictionLog.occurred_at >= since)
        if source is not None:
            statement = statement.where(PredictionLog.source == source)
        result = await self.rows(session, statement)
        return [tuple(found) for found in result.all()]


prediction_log_repository = PredictionLogRepository()
