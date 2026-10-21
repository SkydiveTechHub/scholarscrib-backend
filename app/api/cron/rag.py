"""Cron entry for full / partial RAG reindex."""

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.api.responses import RagReindexOut
from app.core.config import settings
from app.core.errors import ApiError
from app.database.db import AnSession
from app.services.auth.rules import cron_decision
from app.services.rag.ingest import reindex_all
from app.services.rag.vectorstore import rag_ready

router = APIRouter(prefix="/rag", tags=["Cron"])


def _guard(request: Request) -> int | None:
    header = request.headers.get("authorization") or ""
    bearer = header[7:] if header.lower().startswith("bearer ") else None
    return cron_decision(request.method, settings.cron_secret, bearer)


@router.post("/reindex", response_model=RagReindexOut)
async def cron_reindex(
    request: Request,
    session: AnSession,
    subjectId: str | None = None,
    source: str | None = None,
):
    decision = _guard(request)
    if decision == 405:
        raise ApiError(405, "Method not allowed")
    if decision == 401:
        raise ApiError(401, "Unauthorized")
    if decision == 204:
        return Response(status_code=204)
    if not rag_ready():
        return Response(status_code=204)
    return await reindex_all(session, subject_id=subjectId, source=source)
