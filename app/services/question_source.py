"""Where a student's practice questions come from.

Coverage questions (processed, formatted, topic-linked) come first; raw bank
questions only fill a shortfall. Past-paper practice and JAMB CBT do not use
this module: they read provider questions directly.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Question
from app.database.repositories.coverage import coverage_questions_repository
from app.database.repositories.question import questions_repository

logger = logging.getLogger(__name__)


async def pick_questions(
    session: AsyncSession,
    *,
    subject_id: str,
    count: int,
    topic_ids: list[str] | None = None,
    exam_type: str | None = None,
    exam_year: int | None = None,
    difficulty: str | None = None,
    exclude: set[str] | None = None,
    class_levels: list[tuple[str, str]] | None = None,
    flow: str = "unspecified",
) -> list:
    # Function-local: the assessments package imports this module's callers.
    from app.services.assessments.coverage_paper import as_paper_question

    taken = set(exclude or ())
    covered: list = []
    if not class_levels:
        rows = await coverage_questions_repository.pick_for_topics(
            session,
            subject_id=subject_id,
            count=count,
            topic_ids=topic_ids,
            exam_type=exam_type,
            exam_year=exam_year,
            difficulty=difficulty,
            exclude=set(taken) or None,
        )
        covered = [as_paper_question(row) for row in rows]
    taken.update(item.id for item in covered)
    shortfall = count - len(covered)
    filler: list = []
    if shortfall > 0:
        filler = await questions_repository.pick_objective(
            session,
            subject_id=subject_id,
            count=shortfall,
            topic_ids=topic_ids,
            exam_type=exam_type,
            exam_year=exam_year,
            difficulty=difficulty,
            exclude=taken or None,
            class_levels=class_levels,
        )
    logger.info(
        "question_source flow=%s requested=%s coverage=%s raw=%s",
        flow,
        count,
        len(covered),
        len(filler),
    )
    return [*covered, *filler]


async def questions_by_id(
    session: AsyncSession, ids: list[str], topic_id: str
) -> dict[str, object]:
    """Coverage and raw questions of one topic, keyed by id."""
    wanted = list(dict.fromkeys(ids))
    found: dict[str, object] = {
        row.id: row
        for row in await coverage_questions_repository.by_ids(session, wanted)
        if row.topic_id == topic_id
    }
    rest = [item for item in wanted if item not in found]
    if rest:
        for row in await questions_repository.many(
            session,
            select(Question).where(
                Question.id.in_(rest), Question.topic_id == topic_id
            ),
        ):
            found[row.id] = row
    return found
