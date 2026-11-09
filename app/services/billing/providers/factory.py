"""Select the active billing provider from settings."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.services.billing.providers.base import BillingProvider
from app.services.billing.providers.lago_provider import LagoBillingProvider
from app.services.billing.providers.paystack_provider import (
    PaystackSubscriptionProvider,
)


@lru_cache(maxsize=1)
def get_billing_provider() -> BillingProvider:
    if settings.billing_provider == "lago":
        return LagoBillingProvider()
    return PaystackSubscriptionProvider()


def clear_billing_provider_cache() -> None:
    """Test helper — reset after monkeypatching settings.billing_provider."""
    get_billing_provider.cache_clear()
