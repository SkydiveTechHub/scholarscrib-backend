"""Admin RAG reindex and job status."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends

from app.api.deps import require_admin
from app.api.responses import OkOut, RagJobsOut, RagReindexOut
from app.api.schemas import RagReindexIn
from app.core.errors import ApiError
from app.database.db import AnSession
from app.database.models import Admin
from app.database.repositories.chat import rag_index_jobs_repository
from app.services.rag.ingest import (
    index_lesson,
    index_material,
    index_question,
    reindex_all,
    schedule_index,
)
from app.services.rag.vectorstore import rag_ready

router = APIRouter(prefix="/rag", tags=["Admin / RAG"])


def _job_out(job) -> dict:
    return {
        "id": job.id,
        "source": job.source,
        "entityId": job.entity_id,
        "status": job.status,
        "chunks": job.chunks,
        "error": job.error,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
        "finishedAt": job.finished_at.isoformat() if job.finished_at else None,
    }


@router.get("/jobs", response_model=RagJobsOut)
async def list_jobs(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    rows = await rag_index_jobs_repository.recent(session, 50)
    return {"jobs": [_job_out(row) for row in rows]}


@router.post("/reindex", response_model=RagReindexOut)
async def reindex(
    body: RagReindexIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    background: BackgroundTasks,
):
    if not rag_ready():
        raise ApiError(503, "RAG is not configured")
    if body.entityId and body.source in ("lesson", "question", "material"):
        schedule_index(background, body.source, body.entityId)
        return {"ok": True, "chunks": 0, "jobId": None}
    result = await reindex_all(session, subject_id=body.subjectId, source=body.source)
    return result


@router.post("/reindex/{source}/{entity_id}", response_model=OkOut)
async def reindex_entity(
    source: str,
    entity_id: str,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    if not rag_ready():
        raise ApiError(503, "RAG is not configured")
    if source == "lesson":
        await index_lesson(session, entity_id)
    elif source == "question":
        await index_question(session, entity_id)
    elif source == "material":
        await index_material(session, entity_id)
    else:
        raise ApiError(400, "source must be lesson, question, or material")
    return {"ok": True}
