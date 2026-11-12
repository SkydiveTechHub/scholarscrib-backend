"""Tutor realm: assignment-scoped results + item analysis (P-1..P-4)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import TutorPrincipal, require_tutor
from app.database.db import AnSession
from app.services.centers.results import (
    AssignmentItemAnalysisService,
    AssignmentOverviewService,
    AssignmentStudentRowsService,
    StudentScopedDetailService,
)

router = APIRouter(tags=["Center / Results"])


@router.get("/centers/{center_id}/assignments/{assignment_id}/overview")
async def assignment_overview(
    center_id: str,
    assignment_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    """P-1: counts, score distribution, averages."""
    return await AssignmentOverviewService(
        session, tutor.id, center_id, assignment_id
    ).process()


@router.get("/centers/{center_id}/assignments/{assignment_id}/students")
async def assignment_students(
    center_id: str,
    assignment_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    """P-2: per-student rows + not-started / overdue lists."""
    return await AssignmentStudentRowsService(
        session, tutor.id, center_id, assignment_id
    ).process()


@router.get("/centers/{center_id}/assignments/{assignment_id}/items")
async def assignment_item_analysis(
    center_id: str,
    assignment_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    """P-3: per-question item analysis."""
    return await AssignmentItemAnalysisService(
        session, tutor.id, center_id, assignment_id
    ).process()


@router.get("/centers/{center_id}/students/{student_id}/results")
async def student_scoped_results(
    center_id: str,
    student_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    """P-4 / P-6: assignment-scoped student detail. Never TopicMastery."""
    return await StudentScopedDetailService(
        session, tutor.id, center_id, student_id
    ).process()
