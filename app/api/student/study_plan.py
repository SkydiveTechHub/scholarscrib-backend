from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import StudentPrincipal, require_feature, require_student
from app.api.responses import (
    ItemStatusOut,
    OkOut,
    PlanCreatedOut,
    PlanPageOut,
    PlanPreviewOut,
)
from app.api.schemas import ItemStatusIn, PositionsIn, StudyPlanIn
from app.database.db import AnSession
from app.services.planner import (
    CreateStudyPlanService,
    DeleteStudyPlanService,
    GetStudyPlanService,
    PreviewStudyPlanService,
    SetStudyPlanItemStatusService,
    SetStudyPlanPositionsService,
    UpdateStudyPlanSettingsService,
)

router = APIRouter(prefix="/study-plan", tags=["Student / Study plan"])


@router.get(
    "",
    dependencies=[Depends(require_feature("studyPlanner"))],
    response_model=PlanPageOut,
)
async def get_plan(
    session: AnSession, student: Annotated[StudentPrincipal, Depends(require_student)]
):
    return await GetStudyPlanService(
        session, student.id, student.class_level, student.track
    ).process()


@router.post(
    "",
    status_code=201,
    dependencies=[Depends(require_feature("studyPlanner"))],
    response_model=PlanCreatedOut,
)
async def create_plan(
    body: StudyPlanIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await CreateStudyPlanService(
        session, student.id, student.class_level, body, student.track
    ).process()


@router.post(
    "/preview",
    dependencies=[Depends(require_feature("studyPlanner"))],
    response_model=PlanPreviewOut,
)
async def preview_plan(
    body: StudyPlanIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await PreviewStudyPlanService(
        session, student.id, student.class_level, body, student.track
    ).process()


@router.patch(
    "/{plan_id}",
    dependencies=[Depends(require_feature("studyPlanner"))],
    response_model=OkOut,
)
async def patch_plan(
    plan_id: str,
    body: StudyPlanIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await UpdateStudyPlanSettingsService(
        session, student.id, student.class_level, plan_id, body, student.track
    ).process()


@router.delete(
    "/{plan_id}",
    dependencies=[Depends(require_feature("studyPlanner"))],
    response_model=OkOut,
)
async def delete_plan(
    session: AnSession,
    plan_id: str,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await DeleteStudyPlanService(session, student.id, plan_id).process()


@router.put(
    "/positions",
    dependencies=[Depends(require_feature("studyPlanner"))],
    response_model=OkOut,
)
async def plan_positions(
    body: PositionsIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await SetStudyPlanPositionsService(
        session, student.id, student.class_level, body.positions
    ).process()


@router.patch(
    "/items/{item_id}",
    dependencies=[Depends(require_feature("studyPlanner"))],
    response_model=ItemStatusOut,
)
async def plan_item(
    item_id: str,
    body: ItemStatusIn,
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(require_student)],
):
    return await SetStudyPlanItemStatusService(
        session, student.id, item_id, body.status
    ).process()
