"""Build Assessment + questions for a tutor assignment (no student attempt yet)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.ids import cuid
from app.database.models import Assessment, AssessmentQuestion, Question, Subject
from app.database.repositories.assessment import (
    assessment_questions_repository,
    assessments_repository,
)
from app.database.repositories.coverage import coverage_questions_repository
from app.database.repositories.curriculum import subjects_repository
from app.database.repositories.question import questions_repository
from app.services.assessments.coverage_paper import as_paper_question, is_coverage
from app.services.assessments.service import _pick
from app.services.catalogue import time_limit_minutes


def _scope_range(from_point: dict, to_point: dict) -> list[tuple[str, str]]:
    order = ["SS1", "SS2", "SS3"]
    terms = ["FIRST", "SECOND", "THIRD"]
    start_key = (order.index(from_point["classLevel"]), terms.index(from_point["term"]))
    end_key = (order.index(to_point["classLevel"]), terms.index(to_point["term"]))
    if start_key > end_key:
        start_key, end_key = end_key, start_key
    scopes: list[tuple[str, str]] = []
    for level_index, level in enumerate(order):
        for term_index, term in enumerate(terms):
            key = (level_index, term_index)
            if start_key <= key <= end_key:
                scopes.append((level, term))
    return scopes


async def _load_subject(session: AsyncSession, filters: dict[str, Any]) -> Subject:
    subject_id = filters.get("subjectId")
    subject_slug = filters.get("subjectSlug")
    subject = None
    if subject_id:
        subject = await subjects_repository.by_id(session, subject_id)
    elif subject_slug:
        subject = await subjects_repository.by_slug(session, subject_slug)
    if subject is None:
        raise ApiError(404, "Subject not found")
    return subject


async def _pick_quiz_or_paper(
    session: AsyncSession, *, subject: Subject, filters: dict[str, Any]
) -> list[Question]:
    count = int(filters.get("count") or 10)
    topic_ids = list(filters.get("topicIds") or [])
    exam_type = filters.get("examType")
    exam_year = filters.get("examYear")
    difficulty = filters.get("difficulty")
    question_ids = list(filters.get("questionIds") or [])
    if question_ids:
        rows = await questions_repository.by_ids(session, question_ids)
        by_id = {row.id: row for row in rows}
        ordered = [by_id[qid] for qid in question_ids if qid in by_id]
        if not ordered:
            raise ApiError(404, "No questions found")
        return ordered
    picked = await _pick(
        session,
        subject_id=subject.id,
        count=count,
        topic_ids=topic_ids or None,
        exam_type=exam_type,
        exam_year=exam_year,
        difficulty=difficulty,
    )
    if not picked and topic_ids:
        picked = await _pick(
            session,
            subject_id=subject.id,
            count=count,
            exam_type=exam_type,
            exam_year=exam_year,
            difficulty=difficulty,
        )
    if not picked:
        raise ApiError(404, "No questions found")
    return picked


async def _pick_mock(
    session: AsyncSession, *, subject: Subject, filters: dict[str, Any]
) -> list:
    count = int(filters.get("count") or 40)
    from_point = filters.get("from") or filters.get("from_")
    to_point = filters.get("to")
    if not from_point or not to_point:
        raise ApiError(400, "Mock filters require from and to scope points")
    scopes = _scope_range(from_point, to_point)
    coverage_ids = list(filters.get("questionIds") or [])
    if coverage_ids:
        rows = await coverage_questions_repository.by_ids(session, coverage_ids)
        by_id = {row.id: row for row in rows}
        return [as_paper_question(by_id[qid]) for qid in coverage_ids if qid in by_id]
    picked = [
        as_paper_question(question)
        for question in await coverage_questions_repository.pick_objective(
            session,
            subject_id=subject.id,
            count=count,
            exam_type=filters.get("examType"),
            class_levels=scopes,
        )
    ]
    if not picked:
        raise ApiError(
            422, "No questions in that scope", reason="NO_QUESTIONS_IN_SCOPE"
        )
    return picked


async def build_assignment_assessment(
    session: AsyncSession,
    *,
    tutor_id: str,
    title: str,
    task_type: str,
    filters: dict[str, Any],
    time_limit_minutes_override: int | None = None,
    untimed: bool = False,
) -> tuple[Assessment, list]:
    """Create a shared Assessment bound to an assignment (no attempt)."""
    if task_type == "LESSON":
        raise ApiError(400, "Lesson assignments do not use the assessment engine")
    subject = await _load_subject(session, filters)
    if task_type == "MOCK_EXAM":
        questions = await _pick_mock(session, subject=subject, filters=filters)
        assessment_type = "MOCK_EXAM"
        exam_type = filters.get("examType")
        exam_year = None
    elif task_type == "PAST_PAPER":
        questions = await _pick_quiz_or_paper(session, subject=subject, filters=filters)
        assessment_type = "PAST_PAPER"
        exam_type = filters.get("examType")
        exam_year = filters.get("examYear")
    else:
        questions = await _pick_quiz_or_paper(session, subject=subject, filters=filters)
        exam_type = filters.get("examType")
        exam_year = filters.get("examYear")
        assessment_type = "PAST_PAPER" if exam_type else "TOPIC_QUIZ"

    if time_limit_minutes_override is not None:
        limit = time_limit_minutes_override
    elif untimed:
        limit = None
    else:
        limit = time_limit_minutes(len(questions), False, exam_type)

    assessment = Assessment(
        id=cuid(),
        title=title,
        subject_id=subject.id,
        assessment_type=assessment_type,
        exam_type=exam_type,
        exam_year=exam_year,
        total_marks=len(questions),
        time_limit_minutes=limit,
        pass_mark_percent=50,
        created_by=tutor_id,
    )
    await assessments_repository.add(session, assessment)
    for index, question in enumerate(questions):
        await assessment_questions_repository.add(
            session,
            AssessmentQuestion(
                id=cuid(),
                assessment_id=assessment.id,
                question_id=None if is_coverage(question) else question.id,
                coverage_question_id=question.id if is_coverage(question) else None,
                order_index=index,
            ),
        )
    await session.flush()
    return assessment, questions


async def clone_assessment_questions(
    session: AsyncSession,
    *,
    source_assessment_id: str,
    tutor_id: str,
    title: str,
) -> Assessment:
    source = await assessments_repository.by_id(session, source_assessment_id)
    if source is None:
        raise ApiError(404, "Source assessment not found")
    links = await assessment_questions_repository.list_where(
        session,
        AssessmentQuestion.assessment_id == source_assessment_id,
        order_by=[AssessmentQuestion.order_index],
    )
    assessment = Assessment(
        id=cuid(),
        title=title,
        subject_id=source.subject_id,
        assessment_type=source.assessment_type,
        exam_type=source.exam_type,
        exam_year=source.exam_year,
        total_marks=source.total_marks,
        time_limit_minutes=source.time_limit_minutes,
        pass_mark_percent=source.pass_mark_percent,
        created_by=tutor_id,
    )
    await assessments_repository.add(session, assessment)
    for link in links:
        await assessment_questions_repository.add(
            session,
            AssessmentQuestion(
                id=cuid(),
                assessment_id=assessment.id,
                question_id=link.question_id,
                coverage_question_id=link.coverage_question_id,
                order_index=link.order_index,
            ),
        )
    await session.flush()
    return assessment
