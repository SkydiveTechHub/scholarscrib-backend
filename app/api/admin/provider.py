from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.api.responses import BackfillOut
from app.api.schemas import ProviderBackfillIn
from app.database.db import AnSession
from app.database.models import Admin
from app.services.admin import RecordAuditService
from app.services.provider import (
    ClearProviderBlockService,
    EnsureProviderQuestionsService,
    ResetFailedFetchService,
    SaturateProviderService,
)

router = APIRouter(prefix="/provider", tags=["Admin / Provider"])


@router.post("/backfill", response_model=BackfillOut)
async def backfill(
    body: ProviderBackfillIn,
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
):
    was_reset = False
    block_cleared = False
    slug, exam_type, exam_year = body.subjectSlug, body.examType, body.examYear
    if body.clearBlock:
        block_cleared = await ClearProviderBlockService(session).process()
    if body.reset:
        was_reset = await ResetFailedFetchService(
            session, slug, exam_type, exam_year
        ).process()
    await EnsureProviderQuestionsService(session, slug, exam_type, exam_year).process()
    ledger = await SaturateProviderService(
        session, slug, exam_type, exam_year
    ).process()
    await RecordAuditService(
        session,
        admin.id,
        "provider.backfill",
        "ProviderFetch",
        "Provider backfill",
        None,
    ).process()
    return {"ledger": ledger, "wasReset": was_reset, "blockCleared": block_cleared}
