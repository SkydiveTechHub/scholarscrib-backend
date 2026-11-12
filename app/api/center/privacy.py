"""X-1 privacy boundary stubs.

These endpoints exist so the privacy harness can assert that a tutor JWT cannot
read unscoped student attempts, mastery, or Astra history. Real scoped reads
land in later milestones and must keep the same fail-closed behaviour for
anything outside assignment_id scope.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import TutorPrincipal, require_tutor
from app.core.errors import ApiError

router = APIRouter(prefix="/privacy", tags=["Center / Privacy"])


def _forbidden_unscoped(resource: str) -> None:
    raise ApiError(
        403,
        "Tutors can only access assignment-scoped student data",
        resource=resource,
        code="CENTER_PRIVACY_X1",
    )


@router.get("/students/{student_id}/attempts")
async def unscoped_attempts(
    student_id: str,
    _tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    _forbidden_unscoped("attempts")


@router.get("/students/{student_id}/mastery")
async def unscoped_mastery(
    student_id: str,
    _tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    _forbidden_unscoped("mastery")


@router.get("/students/{student_id}/astra")
async def unscoped_astra(
    student_id: str,
    _tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    _forbidden_unscoped("astra")
