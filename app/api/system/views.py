from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from scalar_fastapi import get_scalar_api_reference

from app.api.system.schema import StatusCheck

router = APIRouter(tags=["System"])


@router.get("/", include_in_schema=False)
async def redirect_to_docs():
    return RedirectResponse(url="/docs")


@router.get("/health")
async def health_status_check() -> StatusCheck:
    return StatusCheck(status=True, detail="ScholarsCrib API is up")


@router.get("/docs", include_in_schema=False)
async def scalar_html():
    return get_scalar_api_reference(
        openapi_url="/openapi.json",
        title="ScholarsCrib API",
    )
