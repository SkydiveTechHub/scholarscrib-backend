from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_admin, require_super_admin
from app.api.responses import (
    AdminCurriculumOut,
    AdminCurriculumsOut,
    AdminTopicOut,
    AdminTopicsOut,
    OkOut,
    SubjectOut,
    SubjectsOut,
)
from app.api.schemas import (
    CurriculumCreateIn,
    CurriculumPatchIn,
    SubjectCreateIn,
    SubjectPatchIn,
    TopicCreateIn,
    TopicPatchIn,
)
from app.database.db import AnSession
from app.database.models import Admin
from app.services.console import (
    CreateCurriculumService,
    CreateSubjectService,
    CreateTopicService,
    DeleteCurriculumService,
    DeleteSubjectService,
    DeleteTopicService,
    ListAdminCurriculumsService,
    ListAdminSubjectsService,
    ListAdminTopicsService,
    UpdateCurriculumService,
    UpdateSubjectService,
    UpdateTopicService,
)

subjects_router = APIRouter(prefix="/subjects", tags=["Admin / Catalogue"])
curriculums_router = APIRouter(prefix="/curriculums", tags=["Admin / Catalogue"])
topics_router = APIRouter(prefix="/topics", tags=["Admin / Catalogue"])


@subjects_router.get("", response_model=SubjectsOut)
async def list_subjects(
    session: AnSession, admin: Annotated[Admin, Depends(require_admin)]
):
    return {"subjects": await ListAdminSubjectsService(session).process()}


@subjects_router.post("", status_code=201, response_model=SubjectOut)
async def create_subject(
    body: SubjectCreateIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_super_admin)],
):
    return await CreateSubjectService(session, admin.id, body).process()


@subjects_router.patch("/{subject_id}", response_model=SubjectOut)
async def update_subject(
    subject_id: str,
    body: SubjectPatchIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_super_admin)],
):
    return await UpdateSubjectService(session, admin.id, subject_id, body).process()


@subjects_router.delete("/{subject_id}", response_model=OkOut)
async def delete_subject(
    subject_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_super_admin)],
):
    return await DeleteSubjectService(session, admin.id, subject_id).process()


@curriculums_router.get("", response_model=AdminCurriculumsOut)
async def list_curriculums(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    subjectId: str | None = None,
):
    rows = await ListAdminCurriculumsService(session, subjectId).process()
    return {"curriculums": rows}


@curriculums_router.post("", status_code=201, response_model=AdminCurriculumOut)
async def create_curriculum(
    body: CurriculumCreateIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_super_admin)],
):
    return await CreateCurriculumService(session, admin.id, body).process()


@curriculums_router.patch("/{curriculum_id}", response_model=AdminCurriculumOut)
async def update_curriculum(
    curriculum_id: str,
    body: CurriculumPatchIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_super_admin)],
):
    return await UpdateCurriculumService(
        session, admin.id, curriculum_id, body
    ).process()


@curriculums_router.delete("/{curriculum_id}", response_model=OkOut)
async def delete_curriculum(
    curriculum_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_super_admin)],
):
    return await DeleteCurriculumService(session, admin.id, curriculum_id).process()


@curriculums_router.get("/{curriculum_id}/topics", response_model=AdminTopicsOut)
async def list_curriculum_topics(
    curriculum_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    rows = await ListAdminTopicsService(session, curriculum_id).process()
    return {"topics": rows}


@curriculums_router.post(
    "/{curriculum_id}/topics", status_code=201, response_model=AdminTopicOut
)
async def create_curriculum_topic(
    curriculum_id: str,
    body: TopicCreateIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_super_admin)],
):
    return await CreateTopicService(session, admin.id, curriculum_id, body).process()


@topics_router.patch("/{topic_id}", response_model=AdminTopicOut)
async def update_topic(
    topic_id: str,
    body: TopicPatchIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_super_admin)],
):
    return await UpdateTopicService(session, admin.id, topic_id, body).process()


@topics_router.delete("/{topic_id}", response_model=OkOut)
async def delete_topic(
    topic_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_super_admin)],
):
    return await DeleteTopicService(session, admin.id, topic_id).process()
