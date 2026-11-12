"""Tutor realm: recommendations CRUD (C-1..C-3, C-7)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import TutorPrincipal, require_tutor
from app.database.db import AnSession
from app.services.centers.recommendations import (
    CreateRecommendationsService,
    GetCenterRecommendationService,
    ListCenterRecommendationsService,
)

router = APIRouter(tags=["Center / Recommendations"])


class RecommendationAttachmentsIn(BaseModel):
    topicIds: list[str] | None = Field(default=None, max_length=20)
    lessonId: str | None = Field(default=None, max_length=64)
    practiceSetId: str | None = Field(default=None, max_length=64)
    assignmentId: str | None = Field(default=None, max_length=64)


class RecommendationCreateIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    studentId: str | None = None
    groupId: str | None = None
    toCenter: bool = False
    assignmentId: str | None = None
    attachments: RecommendationAttachmentsIn | None = None


@router.post("/centers/{center_id}/recommendations", status_code=201)
async def create_recommendations(
    center_id: str,
    body: RecommendationCreateIn,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await CreateRecommendationsService(
        session, tutor.id, center_id, body
    ).process()


@router.get("/centers/{center_id}/recommendations")
async def list_recommendations(
    center_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await ListCenterRecommendationsService(
        session, tutor.id, center_id
    ).process()


@router.get("/centers/{center_id}/recommendations/{recommendation_id}")
async def get_recommendation(
    center_id: str,
    recommendation_id: str,
    session: AnSession,
    tutor: Annotated[TutorPrincipal, Depends(require_tutor)],
):
    return await GetCenterRecommendationService(
        session, tutor.id, center_id, recommendation_id
    ).process()
