"""Map unmapped assessment questions to curriculum topics via Jev Choice."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, cast

from langchain_typesafe import Choice
from langchain_typesafe.types import Question
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import logger
from app.database.models import CoverageQuestion, LearningEvent, Topic
from app.database.models import Question as BankQuestion
from app.database.repositories.assessment import attempts_repository
from app.database.repositories.coverage import coverage_questions_repository
from app.database.repositories.curriculum import subjects_repository, topics_repository
from app.database.repositories.identity import users_repository
from app.database.repositories.learning import learning_events_repository
from app.database.repositories.question import questions_repository
from app.services.assessments.coverage_paper import is_coverage
from app.services.assessments.service import _paper_questions
from app.services.learning.mastery_store import GetTopicMasteryService
from app.services.rag.decisions import decide_batch

CHUNK_SIZE = 20
MIN_CLASS_TOPICS = 5
NONE_OPTION = "none"
TOPIC_INSTRUCTIONS = (
    "Which curriculum topic does this exam question primarily test? "
    "Use `topics` as the closed set of allowed topics. "
    "Match the question in `questions` under the same id as this Choice. "
    "Pick `none` only when no listed topic is a reasonable fit."
)


class AttributeAttemptTopicsService:
    """Background attribution for questions that still lack a bank topic_id."""

    def __init__(self, session: AsyncSession, student_id: str, attempt_id: str) -> None:
        self.session = session
        self.student_id = student_id
        self.attempt_id = attempt_id

    async def process(self) -> dict[str, Any]:
        if not settings.jev_ready:
            return {"attributed": 0, "skipped": "jev_off"}

        attempt = await attempts_repository.by_id(self.session, self.attempt_id)
        if attempt is None or attempt.student_id != self.student_id:
            return {"attributed": 0, "skipped": "not_found"}

        paper = await _paper_questions(self.session, attempt.assessment_id)
        unmapped = [question for question in paper if not question.topic_id]
        if not unmapped:
            return {"attributed": 0, "skipped": "all_mapped"}

        user = await users_repository.by_id(self.session, self.student_id)
        class_level = user.class_level if user else None

        by_subject: dict[str, list] = defaultdict(list)
        for question in unmapped:
            by_subject[question.subject_id].append(question)

        attributed_topic_ids: set[str] = set()
        attributed = 0
        for subject_id, questions in by_subject.items():
            subject = await subjects_repository.by_id(self.session, subject_id)
            if subject is None:
                continue
            topics = await self._topics_for_subject(subject_id, class_level)
            if not topics:
                continue
            topic_by_id = {topic.id: topic for topic in topics}
            by_question_id = {question.id: question for question in questions}
            for chunk in _chunks(questions, CHUNK_SIZE):
                mapped = await self._map_chunk(
                    subject_name=subject.name,
                    class_level=class_level,
                    topics=topics,
                    questions=chunk,
                )
                for question_id, topic_id in mapped.items():
                    if topic_id not in topic_by_id:
                        continue
                    question = by_question_id.get(question_id)
                    if question is None:
                        continue
                    await self._persist(question, topic_id)
                    attributed_topic_ids.add(topic_id)
                    attributed += 1

        if attributed_topic_ids:
            await GetTopicMasteryService(
                self.session, self.student_id, list(attributed_topic_ids)
            ).process()

        return {"attributed": attributed, "topicIds": sorted(attributed_topic_ids)}

    async def _topics_for_subject(
        self, subject_id: str, class_level: str | None
    ) -> list[Topic]:
        if class_level:
            scoped = await topics_repository.with_levels(
                self.session, subject_id=subject_id, class_level=class_level
            )
            topics = [topic for topic, _level, _term in scoped]
            if len(topics) >= MIN_CLASS_TOPICS:
                return topics
        return await topics_repository.for_subject(self.session, subject_id)

    async def _map_chunk(
        self,
        *,
        subject_name: str,
        class_level: str | None,
        topics: list[Topic],
        questions: list,
    ) -> dict[str, str]:
        criteria: dict[str, JsonValue] = {topic.id: topic.title for topic in topics}
        criteria[NONE_OPTION] = "No topic in the curriculum list matches this question"

        known_ids = {question.id for question in questions}
        state = {
            "subject": subject_name,
            "classLevel": class_level,
            "topics": [{"id": topic.id, "title": topic.title} for topic in topics],
            "questions": {
                question.id: {
                    "text": (question.question_text or "")[:2000],
                    "options": question.options,
                }
                for question in questions
            },
        }
        choice_questions: dict[str, Question] = {
            question.id: Choice(
                instructions=TOPIC_INSTRUCTIONS,
                criteria=cast(dict[str, JsonValue], criteria),
            )
            for question in questions
        }
        response = await decide_batch(state, choice_questions)
        if response is None:
            return {}

        threshold = settings.jev_confidence_threshold
        accepted: dict[str, str] = {}
        for question_id, answer in (response.choices or {}).items():
            choice = getattr(answer, "choice", None)
            confidence = float(getattr(answer, "confidence", 0) or 0)
            if (
                not choice
                or choice == NONE_OPTION
                or confidence < threshold
                or question_id not in known_ids
            ):
                continue
            accepted[question_id] = choice
        return accepted

    async def _persist(self, question, topic_id: str) -> None:
        await learning_events_repository.update_where(
            self.session,
            {"topic_id": topic_id},
            LearningEvent.student_id == self.student_id,
            LearningEvent.source_id == question.id,
            LearningEvent.kind == "QUESTION_ANSWERED",
            LearningEvent.topic_id.is_(None),
        )
        if is_coverage(question):
            await coverage_questions_repository.update_where(
                self.session,
                {"topic_id": topic_id},
                CoverageQuestion.id == question.id,
                CoverageQuestion.topic_id.is_(None),
            )
        else:
            await questions_repository.update_where(
                self.session,
                {"topic_id": topic_id},
                BankQuestion.id == question.id,
                BankQuestion.topic_id.is_(None),
            )


def _chunks(items: list, size: int) -> list[list]:
    return [items[index : index + size] for index in range(0, len(items), size)]


async def attribute_attempt_topics_safe(
    session: AsyncSession, student_id: str, attempt_id: str
) -> None:
    """Fail-open wrapper for BackgroundTasks."""
    try:
        await AttributeAttemptTopicsService(session, student_id, attempt_id).process()
    except Exception:  # noqa: BLE001
        logger.exception(
            "Topic attribution failed for attempt %s (student %s)",
            attempt_id,
            student_id,
        )
