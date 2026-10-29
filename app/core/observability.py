"""Logfire request tracing for the FastAPI app.

Only HTTP request spans are recorded. Headers and bodies are not captured, so
bearer JWTs, the cron secret and payment payloads never leave the process.
"""

import logfire
from fastapi import FastAPI

from app.core.config import settings


def setup_observability(app: FastAPI) -> None:
    # configure() must run before any instrument_*() call.
    logfire.configure(
        service_name=settings.service_name,
        service_version=settings.service_version,
        environment=settings.environment,
        # Export only when enabled and a token or .logfire/ credential exists.
        # An explicit value here overrides LOGFIRE_SEND_TO_LOGFIRE, hence the setting.
        send_to_logfire="if-token-present" if settings.logfire_enabled else False,
        console=False,
    )
    logfire.instrument_fastapi(
        app,
        capture_headers=False,
        # Probe endpoint; would dominate the trace list.
        excluded_urls="/health",
    )
