"""Map unmapped assessment questions to curriculum topics via Jev Choice."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from langchain_typesafe import Choice
from langchain_typesafe.types import Question
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.core.decisions import decide_batch
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

CHUNK_SIZE = 20
MIN_CLASS_TOPICS = 5
NONE_OPTION = "none"

# Maximum number of topics to present to Jev in one criteria set.
# A large criteria set spreads probability thinly; cap it so each option
# has enough mass for the attribution threshold to be meaningful.
MAX_TOPICS_PER_BATCH = 20


def _topic_instructions(
    question_text: str, options: dict | None
) -> dict[str, JsonValue]:
    """Per-question instructions object for the topic Choice.

    Each exam question's text and options live in that question's own Choice
    instructions — not in a shared state dict keyed by ID.  This avoids the
    Jev indirection failure mode: the model reads exactly the right content
    without needing to look up its own ID in a separate lookup table.
    """
    instr: dict[str, JsonValue] = {
        "task": (
            "Which curriculum topic does this exam question primarily test? "
            "Choose from the `criteria`. "
            "Pick `none` only when no listed topic is a reasonable fit."
        ),
        "question": question_text[:2000],
    }
    if options:
        instr["options"] = options
    return instr


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
        # Cap the criteria set so confidence does not spread too thin.
        # Topics are already deduplicated by title; if more remain than the
        # cap allows, keep the first MAX_TOPICS_PER_BATCH (curriculum order).
        capped = topics[:MAX_TOPICS_PER_BATCH]
        criteria: dict[str, JsonValue] = {topic.id: topic.title for topic in capped}
        criteria[NONE_OPTION] = "No topic in the curriculum list matches this question"

        # Shared state holds only context that is identical for every question
        # in this chunk — subject name and class level.  Putting the topic list
        # here alongside criteria would duplicate it and add irrelevant noise
        # (jaggedness: "large state full of irrelevant detail costs accuracy").
        state: dict[str, JsonValue] = {
            "subject": subject_name,
            "classLevel": class_level,
        }

        # Each exam question gets its own Choice entry in the batch.
        # Its text and options go in that question's `instructions` object —
        # not in a shared lookup dict keyed by ID (which would require
        # indirection, a documented Jev failure mode).
        # All questions share one API call: same state, parallel answers,
        # no extra cost (parallel_questions cookbook: 12x cheaper than N calls).
        choice_questions: dict[str, Question] = {
            question.id: Choice(
                instructions=_topic_instructions(
                    question.question_text or "", question.options
                ),
                criteria=criteria,
            )
            for question in questions
        }
        response = await decide_batch(state, choice_questions)
        if response is None:
            return {}

        known_ids = {question.id for question in questions}
        threshold = settings.jev_attribution_threshold
        accepted: dict[str, str] = {}
        for question_id, answer in (response.choices or {}).items():
            if question_id not in known_ids:
                continue
            choice = getattr(answer, "choice", None)
            confidence = float(getattr(answer, "confidence", 0) or 0)
            if not choice or choice == NONE_OPTION or confidence < threshold:
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
        result = await AttributeAttemptTopicsService(
            session, student_id, attempt_id
        ).process()
        if result.get("skipped"):
            logger.info(
                "Topic attribution skipped for attempt %s (student %s): %s",
                attempt_id,
                student_id,
                result["skipped"],
            )
        else:
            logger.info(
                "Topic attribution complete for attempt %s (student %s): "
                "%d attributed, topics=%s",
                attempt_id,
                student_id,
                result.get("attributed", 0),
                result.get("topicIds", []),
            )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Topic attribution failed for attempt %s (student %s)",
            attempt_id,
            student_id,
        )
