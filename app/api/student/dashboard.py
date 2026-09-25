from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import StudentPrincipal, require_student
from app.api.responses import DashboardOut
from app.database.db import AnSession
from app.services.pages import GetDashboardService

router = APIRouter(prefix="/dashboard", tags=["Student / Dashboard"])


@router.get("", response_model=DashboardOut)
async def dashboard(
    session: AnSession, student: Annotated[StudentPrincipal, Depends(require_student)]
):
    return await GetDashboardService(
        session, student.id, student.first_name, student.tier, student.class_level
    ).process()
