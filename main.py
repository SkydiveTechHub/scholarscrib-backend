from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.admin import router as admin_router
from app.api.cron import router as cron_router
from app.api.student import router as student_router
from app.api.system.views import router as system_router
from app.core.config import settings
from app.core.errors import ApiError
from app.core.logger import logger


@asynccontextmanager
async def lifespan(api: FastAPI):
    logger.info("Starting %s", settings.service_name)
    yield
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
    api.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @api.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        headers = {}
        if exc.retry_after is not None:
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(exc.payload, status_code=exc.status_code, headers=headers)

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

    return api


app = get_app()
