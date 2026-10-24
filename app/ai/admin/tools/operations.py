"""Announcements, dashboard, audit, provider, search index, and team tools."""

from __future__ import annotations

import secrets
from datetime import timedelta

from pydantic import BaseModel, Field

from app.ai.admin.tools.factory import (
    EmptyArgs,
    register_mutation,
    register_read,
)
from app.ai.rag.ingest import (
    index_lesson,
    index_material,
    index_question,
    reindex_all,
)
from app.ai.rag.vectorstore import rag_ready
from app.api.schemas import (
    AnnouncementCreateIn,
    AnnouncementTestIn,
    AudienceIn,
    ProviderBackfillIn,
)
from app.core.errors import ApiError
from app.core.logger import logger
from app.core.timeutil import utcnow
from app.database.repositories.admin import admins_repository
from app.database.repositories.chat import rag_index_jobs_repository
from app.services.admin import (
    AdminForgotPasswordService,
    CreateAdminService,
    RecordAuditService,
    SetAdminStatusService,
    admin_row,
)
from app.services.analytics import GetAdminAnalyticsService
from app.services.console import (
    CancelAnnouncementService,
    CreateAnnouncementService,
    GetAdminOverviewService,
    GetAdminStatsService,
    GetAuditActorsService,
    GetAuditLogService,
    ListAnnouncementsService,
    PreviewAnnouncementAudienceService,
    TestAnnouncementService,
)
from app.services.provider import (
    ClearProviderBlockService,
    ResetFailedFetchService,
    SaturateProviderService,
)


class PreviewAudienceArgs(AudienceIn):
    """Audience filters for a preview. Same shape as the console."""


class AnnouncementIdArgs(BaseModel):
    announcementId: str = Field(min_length=1, max_length=64)


class AuditLogArgs(BaseModel):
    page: int = Field(default=1, ge=1, le=20)
    action: str | None = None


class AnalyticsArgs(BaseModel):
    sinceDays: int = Field(default=30, ge=1, le=365)


class ReindexArgs(BaseModel):
    source: str = Field(
        default="full",
        description="lesson, question, material, or full",
    )
    entityId: str | None = None
    subjectId: str | None = None


class CreateAdminArgs(BaseModel):
    identifier: str = Field(min_length=3, max_length=120)


class SetAdminStatusArgs(BaseModel):
    adminId: str = Field(min_length=1, max_length=64)
    isActive: bool


def _windows(since_days: int) -> dict:
    now = utcnow()
    current_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_end = current_start
    previous_start = (current_start - timedelta(days=1)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return {
        "now": now,
        "since": now - timedelta(days=since_days),
        "current_start": current_start,
        "previous_start": previous_start,
        "previous_end": previous_end,
        "last_month": previous_start,
        "cohort_start": now - timedelta(days=max(since_days, 90)),
    }


async def _announcements(ctx, args: EmptyArgs) -> list:
    return await ListAnnouncementsService(ctx.session).process()


async def _preview(ctx, args: PreviewAudienceArgs) -> dict:
    audience = AudienceIn.model_validate(args.model_dump())
    return await PreviewAnnouncementAudienceService(ctx.session, audience).process()


async def _send(ctx, args: AnnouncementCreateIn) -> dict:
    return await CreateAnnouncementService(ctx.session, ctx.admin_id, args).process()


async def _test(ctx, args: AnnouncementTestIn) -> dict:
    return await TestAnnouncementService(ctx.session, ctx.admin_id, args).process()


async def _cancel(ctx, args: AnnouncementIdArgs) -> dict:
    return await CancelAnnouncementService(
        ctx.session, ctx.admin_id, args.announcementId
    ).process()


async def _stats(ctx, args: EmptyArgs) -> dict:
    return await GetAdminStatsService(ctx.session).process()


async def _overview(ctx, args: EmptyArgs) -> dict:
    return await GetAdminOverviewService(ctx.session).process()


async def _analytics(ctx, args: AnalyticsArgs) -> dict:
    windows = _windows(args.sinceDays)
    return await GetAdminAnalyticsService(ctx.session, **windows).process()


async def _audit(ctx, args: AuditLogArgs) -> dict:
    return await GetAuditLogService(ctx.session, args.page, args.action).process()


async def _actors(ctx, args: EmptyArgs) -> dict:
    return await GetAuditActorsService(ctx.session).process()


async def _backfill(ctx, args: ProviderBackfillIn) -> dict:
    block_cleared = False
    was_reset = False
    if args.clearBlock:
        block_cleared = await ClearProviderBlockService(ctx.session).process()
    if args.reset:
        was_reset = await ResetFailedFetchService(
            ctx.session, args.subjectSlug, args.examType, args.examYear
        ).process()
    ledger = await SaturateProviderService(
        ctx.session, args.subjectSlug, args.examType, args.examYear
    ).process()
    await RecordAuditService(
        ctx.session,
        ctx.admin_id,
        "provider.backfill",
        "ProviderFetch",
        "Provider backfill",
        None,
    ).process()
    return {"ledger": ledger, "wasReset": was_reset, "blockCleared": block_cleared}


def _job_row(job) -> dict:
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


async def _jobs(ctx, args: EmptyArgs) -> dict:
    rows = await rag_index_jobs_repository.recent(ctx.session, 20)
    return {"jobs": [_job_row(row) for row in rows]}


async def _reindex(ctx, args: ReindexArgs) -> dict:
    if not rag_ready():
        raise ApiError(503, "RAG is not configured")
    allowed = {"lesson", "question", "material", "full"}
    source = args.source if args.source in allowed else "full"
    if args.entityId and source in {"lesson", "question", "material"}:
        if source == "lesson":
            chunks = await index_lesson(ctx.session, args.entityId)
        elif source == "question":
            chunks = await index_question(ctx.session, args.entityId)
        else:
            chunks = await index_material(ctx.session, args.entityId)
        result = {"ok": True, "chunks": chunks, "entityId": args.entityId}
    else:
        result = await reindex_all(
            ctx.session,
            subject_id=args.subjectId,
            source=None if source == "full" else source,
        )
    await RecordAuditService(
        ctx.session,
        ctx.admin_id,
        "rag.reindex",
        "RagIndexJob",
        f"Reindex {source}",
        args.entityId,
    ).process()
    return result


async def _admins(ctx, args: EmptyArgs) -> dict:
    rows = await admins_repository.list_created(ctx.session)
    return {"admins": [admin_row(row) for row in rows]}


async def _create_admin(ctx, args: CreateAdminArgs) -> dict:
    if "@" not in args.identifier:
        raise ApiError(
            400,
            "Create the admin with an email so a reset link can be sent",
        )
    password = secrets.token_urlsafe(18)
    created = await CreateAdminService(
        ctx.session, ctx.admin_id, args.identifier, password
    ).process()
    emailed = False
    try:
        await AdminForgotPasswordService(
            ctx.session, args.identifier, "astra"
        ).process()
        emailed = True
    except Exception:
        logger.exception("admin astra reset email failed")
        emailed = False
    admin = created.get("admin") if isinstance(created, dict) else None
    return {"admin": admin, "resetEmailSent": emailed}


async def _admin_status(ctx, args: SetAdminStatusArgs) -> dict:
    return await SetAdminStatusService(
        ctx.session, ctx.admin_id, args.adminId, args.isActive
    ).process()


def register_operation_tools() -> None:
    register_read(
        "list_announcements",
        "announcements",
        "Recent announcements and their send status.",
        EmptyArgs,
        _announcements,
    )
    register_read(
        "preview_announcement_audience",
        "announcements",
        "How many students and devices match an audience. Does not send.",
        PreviewAudienceArgs,
        _preview,
    )
    register_mutation(
        "send_announcement",
        "announcements",
        "Propose sending an in-app announcement and push. Approve or reject only.",
        AnnouncementCreateIn,
        _send,
        destructive=True,
    )
    register_mutation(
        "test_announcement",
        "announcements",
        "Propose a push test to one student contact. Approve or reject only.",
        AnnouncementTestIn,
        _test,
        destructive=True,
    )
    register_mutation(
        "cancel_announcement",
        "announcements",
        "Propose cancelling a queued or sending announcement.",
        AnnouncementIdArgs,
        _cancel,
        destructive=True,
    )
    register_read(
        "admin_stats",
        "dashboard",
        "Counts of students, questions, and attempts.",
        EmptyArgs,
        _stats,
    )
    register_read(
        "admin_overview",
        "dashboard",
        "Overview figures: subjects, exams, and difficulty mix.",
        EmptyArgs,
        _overview,
    )
    register_read(
        "admin_analytics",
        "dashboard",
        "Windowed analytics. sinceDays defaults to 30. The other windows are "
        "derived from today.",
        AnalyticsArgs,
        _analytics,
    )
    register_read(
        "audit_log",
        "audit",
        "Audit log page. Optional action filter such as student.tier.",
        AuditLogArgs,
        _audit,
    )
    register_read(
        "audit_actors",
        "audit",
        "Admins who appear in the audit log, for filters.",
        EmptyArgs,
        _actors,
    )
    register_mutation(
        "provider_backfill",
        "provider",
        "Propose a provider backfill. clearBlock and reset are optional. "
        "Approve or reject only.",
        ProviderBackfillIn,
        _backfill,
        destructive=True,
    )
    register_read(
        "list_rag_jobs",
        "rag",
        "Recent search-index jobs.",
        EmptyArgs,
        _jobs,
    )
    register_mutation(
        "reindex_content",
        "rag",
        "Propose reindexing one lesson, question, or material, or a full rebuild. "
        "Approve or reject only.",
        ReindexArgs,
        _reindex,
        destructive=True,
    )
    register_read(
        "list_admins",
        "team",
        "Admin team accounts. Owner only. No password fields.",
        EmptyArgs,
        _admins,
        owner_only=True,
    )
    register_mutation(
        "create_admin",
        "team",
        "Propose creating an admin by email. A reset link is emailed. "
        "Do not invent or ask for a password. Owner only.",
        CreateAdminArgs,
        _create_admin,
        owner_only=True,
    )
    register_mutation(
        "set_admin_status",
        "team",
        "Propose activating or deactivating another admin. Owner only. "
        "The owner account cannot be deactivated.",
        SetAdminStatusArgs,
        _admin_status,
        owner_only=True,
        destructive=True,
    )
