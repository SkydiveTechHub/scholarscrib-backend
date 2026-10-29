"""Keep the suite off shared Upstash counters and the live PostHog project."""

from __future__ import annotations

import pytest

from app.core import rate_limit
from app.services import product_events


@pytest.fixture(autouse=True)
def _disable_product_events():
    """A developer's PostHog token must not receive events from the suite."""
    product_events.disable()
    yield
    product_events.disable()


@pytest.fixture(autouse=True)
def _memory_rate_limit(monkeypatch):
    monkeypatch.setattr(rate_limit, "_redis_hit", lambda *_args, **_kwargs: None)
    rate_limit.reset_memory()
    yield
    rate_limit.reset_memory()
