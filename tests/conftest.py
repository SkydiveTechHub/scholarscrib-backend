"""Keep the suite off the shared Upstash rate-limit counters."""

from __future__ import annotations

import pytest

from app.core import rate_limit


@pytest.fixture(autouse=True)
def _memory_rate_limit(monkeypatch):
    monkeypatch.setattr(rate_limit, "_redis_hit", lambda *_args, **_kwargs: None)
    rate_limit.reset_memory()
    yield
    rate_limit.reset_memory()
