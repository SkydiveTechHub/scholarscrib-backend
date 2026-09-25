"""Fold TopicMastery forward from the learning-event ledger.

A failed write must not fail the page that triggered the read. The next read
folds the same events again.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.core.timeutil import utcnow
from app.database.models import LearningEvent, TopicMastery
from app.database.repositories.learning import (
    learning_events_repository,
    topic_mastery_repository,
)
from app.services.learning.evidence import SCORING_VERSION
from app.services.learning.mastery import (
    ChannelAggregate,
    LearningEventView,
    TopicAggregate,
    TopicState,
    fold_events,
    state_from_aggregate,
)


class GetTopicMasteryService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        topic_ids: list[str] | None = None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.topic_ids = topic_ids

    async def process(self) -> dict[str, TopicState]:
        now = utcnow()
        if self.topic_ids is not None and not self.topic_ids:
            return {}
        rows = await topic_mastery_repository.for_student(
            self.session, self.student_id, self.topic_ids
        )
        events = await learning_events_repository.for_student(
            self.session, self.student_id, self.topic_ids
        )
        stored: dict[str, TopicAggregate] = {}
        for row in rows:
            stored[row.topic_id] = _aggregate(self.student_id, row)
        grouped: dict[str, list[LearningEventView]] = {}
        for event in events:
            if not event.topic_id:
                continue
            grouped.setdefault(event.topic_id, []).append(_view(event))
            stored.setdefault(
                event.topic_id,
                TopicAggregate(student_id=self.student_id, topic_id=event.topic_id),
            )
        states: dict[str, TopicState] = {}
        for topic_id, aggregate in stored.items():
            fold_events(aggregate, grouped.get(topic_id, []), now)
            states[topic_id] = state_from_aggregate(aggregate, now)
        if self.topic_ids is not None:
            for topic_id in self.topic_ids:
                if topic_id not in states:
                    states[topic_id] = state_from_aggregate(
                        TopicAggregate(student_id=self.student_id, topic_id=topic_id),
                        now,
                    )
        await self._persist(stored)
        return states

    async def _persist(self, aggregates: dict[str, TopicAggregate]) -> None:
        try:
            async with self.session.begin_nested():
                for topic_id, aggregate in aggregates.items():
                    row = await topic_mastery_repository.by_id(
                        self.session, (self.student_id, topic_id)
                    )
                    if row is None:
                        row = TopicMastery(
                            student_id=self.student_id, topic_id=topic_id
                        )
                        await topic_mastery_repository.add(self.session, row)
                    row.acc_outcome = aggregate.acc.outcome
                    row.acc_mass = aggregate.acc.mass
                    row.acc_observations = aggregate.acc.observations
                    row.lesson_outcome = aggregate.lesson.outcome
                    row.lesson_mass = aggregate.lesson.mass
                    row.lesson_observations = aggregate.lesson.observations
                    row.srs_outcome = aggregate.srs.outcome
                    row.srs_mass = aggregate.srs.mass
                    row.srs_observations = aggregate.srs.observations
                    row.abandon_count = aggregate.abandon_count
                    row.decay_anchor = aggregate.decay_anchor
                    row.last_effort_at = aggregate.last_effort_at
                    row.cursor_seq = aggregate.cursor_seq
                    row.scoring_version = aggregate.scoring_version or SCORING_VERSION
                await self.session.flush()
        except Exception:
            logger.exception("Topic mastery persist failed for %s", self.student_id)


def _aggregate(student_id: str, row: TopicMastery) -> TopicAggregate:
    return TopicAggregate(
        student_id=student_id,
        topic_id=row.topic_id,
        acc=ChannelAggregate(row.acc_outcome, row.acc_mass, row.acc_observations),
        lesson=ChannelAggregate(
            row.lesson_outcome, row.lesson_mass, row.lesson_observations
        ),
        srs=ChannelAggregate(row.srs_outcome, row.srs_mass, row.srs_observations),
        abandon_count=row.abandon_count,
        decay_anchor=row.decay_anchor,
        last_effort_at=row.last_effort_at,
        cursor_seq=row.cursor_seq,
        scoring_version=row.scoring_version,
    )


def _view(event: LearningEvent) -> LearningEventView:
    return LearningEventView(
        seq=event.seq,
        topic_id=event.topic_id,
        kind=event.kind,
        correct=event.correct,
        score=event.score,
        difficulty=event.difficulty,
        seconds=event.seconds,
        occurred_at=event.occurred_at,
    )
