"""Student topic standings over TopicMastery with status band filters."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.curriculum import subjects_repository, topics_repository
from app.services.learning.evidence import (
    CONFIDENCE_FLOOR,
    STRONG_MASTERY,
    WEAK_MASTERY,
)
from app.services.learning.mastery import TopicState
from app.services.learning.mastery_store import GetTopicMasteryService

STANDING_ALL = "all"
STANDING_STRONG = "strong"
STANDING_WEAK = "weak"
STANDING_NORMAL = "normal"
STANDING_EMERGING = "emerging"

VALID_STATUS = {
    STANDING_ALL,
    STANDING_STRONG,
    STANDING_WEAK,
    STANDING_NORMAL,
}


def standing_band(state: TopicState) -> str | None:
    """Return strong/weak/normal/emerging, or None when there is no evidence."""
    if state.observations <= 0:
        return None
    if state.confidence < CONFIDENCE_FLOOR:
        return STANDING_EMERGING
    if state.mastery >= STRONG_MASTERY:
        return STANDING_STRONG
    if state.mastery < WEAK_MASTERY:
        return STANDING_WEAK
    return STANDING_NORMAL


class GetTopicStandingsService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        *,
        status: str = STANDING_ALL,
        subject_ids: list[str] | None = None,
        limit: int = 50,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.status = status if status in VALID_STATUS else STANDING_ALL
        self.subject_ids = subject_ids or []
        self.limit = max(1, min(limit, 100))

    async def process(self) -> dict:
        subjects = await subjects_repository.active_ordered(self.session)
        subject_by_id = {subject.id: subject for subject in subjects}
        if self.subject_ids:
            wanted = set(self.subject_ids)
            subjects = [subject for subject in subjects if subject.id in wanted]
            subject_by_id = {subject.id: subject for subject in subjects}

        topic_rows = await topics_repository.all_ordered(self.session)
        if self.subject_ids:
            wanted = set(self.subject_ids)
            topic_rows = [topic for topic in topic_rows if topic.subject_id in wanted]

        states = await GetTopicMasteryService(
            self.session,
            self.student_id,
            [topic.id for topic in topic_rows],
        ).process()

        rows: list[dict] = []
        for topic in topic_rows:
            state = states.get(topic.id)
            if state is None:
                continue
            band = standing_band(state)
            if band is None:
                continue
            if self.status != STANDING_ALL and band != self.status:
                continue
            subject = subject_by_id.get(topic.subject_id)
            if subject is None:
                continue
            rows.append(
                {
                    "topicId": topic.id,
                    "title": topic.title,
                    "slug": topic.slug,
                    "subjectId": subject.id,
                    "subjectName": subject.name,
                    "subjectSlug": subject.slug,
                    "mastery": state.mastery,
                    "level": state.level,
                    "status": band,
                    "confidence": round(state.confidence, 3),
                    "observations": state.observations,
                }
            )

        reverse = self.status == STANDING_STRONG
        rows.sort(key=lambda row: row["mastery"], reverse=reverse)
        return {"topics": rows[: self.limit], "status": self.status}
