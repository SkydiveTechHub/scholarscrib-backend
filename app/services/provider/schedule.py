"""Fire-and-forget scheduling for gradual question-bank population."""

from fastapi import BackgroundTasks

from app.core.config import settings
from app.core.errors import RateLimited
from app.core.logger import logger
from app.core.rate_limit import hit
from app.database.db import get_session
from app.services.provider.service import AdvanceProviderBankPageService


async def _provider_bank_page_job(
    subject_slug: str,
    exam_type: str,
    exam_year: int,
) -> None:
    async with get_session() as session:
        try:
            await AdvanceProviderBankPageService(
                session, subject_slug, exam_type, exam_year
            ).process()
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Provider bank page advance failed")


def schedule_provider_bank_page(
    background: BackgroundTasks,
    subject_slug: str | None,
    exam_type: str | None,
    exam_year: int | None,
) -> bool:
    """Schedule one bank page advance when the paper key is complete.

    Returns True when a background task was queued.
    """
    if not settings.provider_enabled:
        return False
    if not subject_slug or not exam_type or exam_year is None:
        return False
    try:
        hit("provider:outbound", 30, 60)
    except RateLimited:
        return False
    background.add_task(
        _provider_bank_page_job,
        subject_slug.strip().lower(),
        exam_type.strip().upper(),
        int(exam_year),
    )
    return True
