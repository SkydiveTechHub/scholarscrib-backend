from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.api.responses import CalibrationOut
from app.core.timeutil import as_utc
from app.database.db import AnSession
from app.database.models import Admin
from app.services.learning.prediction_log import GetCalibrationReportService

router = APIRouter(prefix="/calibration", tags=["Admin / Calibration"])


@router.get("", response_model=CalibrationOut)
async def admin_calibration(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    scoringVersion: int | None = None,
    subjectId: str | None = None,
    since: datetime | None = None,
    source: str | None = None,
):
    return await GetCalibrationReportService(
        session,
        scoring_version=scoringVersion,
        subject_id=subjectId,
        since=as_utc(since) if since else None,
        source=source,
    ).process()
