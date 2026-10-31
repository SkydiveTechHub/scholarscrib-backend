from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi_pagination import add_pagination
from redis_fastapi import FastAPIRedis
from sqlalchemy.exc import InterfaceError, OperationalError

from app.api.admin import router as admin_router
from app.api.cron import router as cron_router
from app.api.student import router as student_router
from app.api.system.views import router as system_router
from app.core.config import settings
from app.core.errors import ApiError, DatabaseWaking
from app.core.logger import logger
from app.core.observability import setup_observability


@asynccontextmanager
async def lifespan(api: FastAPI):
    from app.ai.core.checkpointer import open_checkpointer
    from app.ai.core.memory import open_memory_store
    from app.services.product_events import open_client, shutdown_client

    logger.info("Starting %s", settings.service_name)
    open_client()
    try:
        # Open for the duration of the server (LangGraph custom-checkpointer /
        # custom-store lifecycle), not per request.
        async with open_checkpointer() as checkpointer, open_memory_store() as store:
            api.state.checkpointer = checkpointer
            api.state.memory_store = store
            yield
    finally:
        shutdown_client()
    logger.warning("Shutting down %s", settings.service_name)


def get_app() -> FastAPI:
    api = FastAPI(
        title="ScholarsCrib API",
        description="Exam preparation API for WAEC, JAMB, and NECO.",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
    )
    api.include_router(system_router)
    api.include_router(student_router)
    api.include_router(admin_router)
    api.include_router(cron_router)
    add_pagination(api)
    FastAPIRedis(api).lifespan().caching()
    api.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # pdf.js reads the first three cross-origin to decide whether it can
        # range-load; the admin export download reads the filename from the last.
        expose_headers=[
            "Accept-Ranges",
            "Content-Range",
            "Content-Length",
            "Content-Disposition",
        ],
    )

    @api.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        headers = {}
        if exc.retry_after is not None:
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(exc.payload, status_code=exc.status_code, headers=headers)

    @api.exception_handler(OperationalError)
    @api.exception_handler(InterfaceError)
    @api.exception_handler(ConnectionError)
    @api.exception_handler(TimeoutError)
    async def db_unreachable_handler(request: Request, exc: Exception):
        # The connection dropped mid-request (e.g. the database suspended).
        logger.warning("Database unreachable: %s", type(exc).__name__)
        return await api_error_handler(request, DatabaseWaking())

    @api.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        details = exc.errors()
        if request.url.path.endswith("/complete-profile") and details:
            message = details[0].get("msg", "Validation failed")
            return JSONResponse({"error": message, "details": details}, status_code=400)
        return JSONResponse(
            {"error": "Validation failed", "details": details}, status_code=400
        )

    @api.exception_handler(Exception)
    async def internal_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error")
        return JSONResponse(
            {"error": "Something went wrong. Please try again."}, status_code=500
        )

    setup_observability(api)
    return api


app = get_app()
