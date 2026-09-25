from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.api.responses import AuditOut
from app.database.db import AnSession
from app.database.models import Admin
from app.services.console import GetAuditLogService

router = APIRouter(prefix="/audit", tags=["Admin / Audit"])


@router.get("", response_model=AuditOut)
async def audit_log(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    page: int = 1,
    action: str | None = None,
):
    return await GetAuditLogService(session, max(page, 1), action).process()
