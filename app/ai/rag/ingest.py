"""Index lessons, questions, and materials into Zilliz."""

from __future__ import annotations

import io
from datetime import UTC, datetime

import httpx
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.rag.loaders import (
    lesson_documents,
    material_documents_from_text,
    question_documents,
)
from app.ai.rag.vectorstore import delete_by_expr, rag_ready, upsert_documents
from app.core.config import settings
from app.core.ids import cuid
from app.core.logger import logger
from app.database.db import async_session
from app.database.models import Lesson, Question, SubjectResource, Subtopic, Topic
from app.database.models.chat import RagIndexJob
from app.database.repositories.chat import rag_index_jobs_repository
from app.database.repositories.curriculum import (
    lessons_repository,
    subject_resources_repository,
)
from app.database.repositories.question import questions_repository
from app.services.cloudinary_delivery import sign_delivery_url


async def _finish_job(
    session: AsyncSession,
    job: RagIndexJob,
    *,
    status: str,
    chunks: int = 0,
    error: str | None = None,
) -> None:
    job.status = status
    job.chunks = chunks
    job.error = error
    job.finished_at = datetime.now(UTC)
    await session.flush()


async def _new_job(
    session: AsyncSession, source: str, entity_id: str | None
) -> RagIndexJob:
    job = RagIndexJob(
        id=cuid(),
        source=source,
        entity_id=entity_id,
        status="RUNNING",
    )
    await rag_index_jobs_repository.add(session, job, flush=True)
    return job


async def index_lesson(session: AsyncSession, lesson_id: str) -> int:
    if not rag_ready():
        return 0
    job = await _new_job(session, "lesson", lesson_id)
    try:
        lesson = await lessons_repository.by_id(session, lesson_id)
        if lesson is None:
            await _finish_job(session, job, status="FAILED", error="Lesson not found")
            return 0
        row = (
            await session.execute(
                select(Topic.id, Topic.subject_id, Topic.title)
                .join(Subtopic, Subtopic.topic_id == Topic.id)
                .where(Subtopic.id == lesson.subtopic_id)
            )
        ).one_or_none()
        if row is None:
            await _finish_job(session, job, status="FAILED", error="Topic not found")
            return 0
        topic_id, subject_id, topic_title = row
        delete_by_expr(f'lesson_id == "{lesson_id}"')
        docs = lesson_documents(
            lesson,
            subject_id=subject_id,
            topic_id=topic_id,
            topic_title=topic_title,
        )
        count = upsert_documents(docs)
        await _finish_job(session, job, status="DONE", chunks=count)
        return count
    except Exception as exc:  # noqa: BLE001
        logger.exception("index_lesson failed %s", lesson_id)
        await _finish_job(session, job, status="FAILED", error=str(exc)[:2000])
        return 0


async def index_question(session: AsyncSession, question_id: str) -> int:
    if not rag_ready():
        return 0
    job = await _new_job(session, "question", question_id)
    try:
        question = await questions_repository.by_id(session, question_id)
        if question is None:
            await _finish_job(session, job, status="FAILED", error="Question not found")
            return 0
        delete_by_expr(f'question_id == "{question_id}"')
        docs = question_documents(question)
        count = upsert_documents(docs)
        await _finish_job(session, job, status="DONE", chunks=count)
        return count
    except Exception as exc:  # noqa: BLE001
        logger.exception("index_question failed %s", question_id)
        await _finish_job(session, job, status="FAILED", error=str(exc)[:2000])
        return 0


def _extract_pdf_bytes(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


async def index_material(session: AsyncSession, resource_id: str) -> int:
    if not rag_ready():
        return 0
    job = await _new_job(session, "material", resource_id)
    try:
        resource = await subject_resources_repository.by_id(session, resource_id)
        if resource is None:
            await _finish_job(session, job, status="FAILED", error="Material not found")
            return 0
        delete_by_expr(f'resource_id == "{resource_id}"')
        if resource.resource_type != "PDF":
            await _finish_job(
                session,
                job,
                status="DONE",
                chunks=0,
                error="Skipped non-PDF material",
            )
            return 0
        url = sign_delivery_url(resource.url)
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url)
            response.raise_for_status()
            text = _extract_pdf_bytes(response.content)
        docs = material_documents_from_text(resource, text)
        count = upsert_documents(docs)
        await _finish_job(session, job, status="DONE", chunks=count)
        return count
    except Exception as exc:  # noqa: BLE001
        logger.exception("index_material failed %s", resource_id)
        await _finish_job(session, job, status="FAILED", error=str(exc)[:2000])
        return 0


async def delete_material_vectors(resource_id: str) -> None:
    if not rag_ready():
        return
    delete_by_expr(f'resource_id == "{resource_id}"')


async def reindex_all(
    session: AsyncSession,
    *,
    subject_id: str | None = None,
    source: str | None = None,
) -> dict:
    if not rag_ready():
        return {"ok": False, "error": "RAG not configured", "chunks": 0}
    job = await _new_job(session, source or "full", subject_id)
    total = 0
    try:
        if source in (None, "lesson", "full"):
            lesson_q = (
                select(Lesson.id)
                .join(Subtopic, Subtopic.id == Lesson.subtopic_id)
                .join(Topic, Topic.id == Subtopic.topic_id)
            )
            if subject_id:
                lesson_q = lesson_q.where(Topic.subject_id == subject_id)
            for (lesson_id,) in (await session.execute(lesson_q)).all():
                total += await index_lesson(session, lesson_id)

        if source in (None, "question", "full"):
            question_q = select(Question.id)
            if subject_id:
                question_q = question_q.where(Question.subject_id == subject_id)
            for (question_id,) in (await session.execute(question_q)).all():
                total += await index_question(session, question_id)

        if source in (None, "material", "full"):
            material_q = select(SubjectResource.id)
            if subject_id:
                material_q = material_q.where(SubjectResource.subject_id == subject_id)
            for (resource_id,) in (await session.execute(material_q)).all():
                total += await index_material(session, resource_id)

        await _finish_job(session, job, status="DONE", chunks=total)
        return {"ok": True, "chunks": total, "jobId": job.id}
    except Exception as exc:  # noqa: BLE001
        logger.exception("reindex_all failed")
        await _finish_job(session, job, status="FAILED", error=str(exc)[:2000])
        return {"ok": False, "error": str(exc), "chunks": total, "jobId": job.id}


async def run_index_job(kind: str, entity_id: str) -> None:
    """BackgroundTasks entrypoint — opens its own session."""
    if not settings.rag_enabled:
        return
    async with async_session() as session:
        try:
            if kind == "lesson":
                await index_lesson(session, entity_id)
            elif kind == "question":
                await index_question(session, entity_id)
            elif kind == "material":
                await index_material(session, entity_id)
            elif kind == "material_delete":
                await delete_material_vectors(entity_id)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("run_index_job failed %s %s", kind, entity_id)


def schedule_index(background, kind: str, entity_id: str) -> None:
    if not settings.rag_enabled or background is None:
        return
    background.add_task(run_index_job, kind, entity_id)
