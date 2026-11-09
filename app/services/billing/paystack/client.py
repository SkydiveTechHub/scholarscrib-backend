"""Thin Paystack HTTP client (Teeket-aligned + Subscriptions/Plans)."""

from __future__ import annotations

import asyncio

import httpx

from app.core.config import settings
from app.core.logger import logger
from app.services.billing.paystack.models import (
    PaystackChargeResponse,
    PaystackManageLinkResponse,
    PaystackMessage,
    PaystackPlanListResponse,
    PaystackPlanResponse,
    PaystackSubscriptionResponse,
    PaystackTransaction,
    PaystackTransactionVerify,
)

BASE_URL = "https://api.paystack.co"
_TIMEOUT = 15.0

CHANNELS = ["card", "bank", "ussd", "qr", "mobile_money", "bank_transfer"]

# Reused across requests so TLS/keep-alive are not rebuilt every call.
_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def _http() -> httpx.AsyncClient:
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client = httpx.AsyncClient(timeout=_TIMEOUT, base_url=BASE_URL)
        _client_loop = loop
    return _client


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.paystack_secret_key}"}


async def initialize(
    *,
    email: str,
    amount_kobo: int,
    reference: str,
    callback_url: str,
    metadata: dict | None = None,
    currency: str = "NGN",
    plan: str | None = None,
) -> PaystackTransaction:
    payload: dict = {
        "email": email,
        "amount": amount_kobo,
        "reference": reference,
        "currency": currency,
        "channels": CHANNELS,
        "callback_url": callback_url,
        "metadata": metadata or {},
    }
    # When set, Paystack charges the plan amount and creates a Subscription.
    if plan:
        payload["plan"] = plan
    response = await _http().post(
        "/transaction/initialize",
        headers=_headers(),
        json=payload,
    )
    if response.status_code >= 400:
        logger.error("Paystack initialize failed: %s", response.text)
        response.raise_for_status()
    return PaystackTransaction(**response.json())


async def verify(reference: str) -> PaystackTransactionVerify:
    response = await _http().get(
        f"/transaction/verify/{reference}",
        headers=_headers(),
    )
    if response.status_code >= 400:
        logger.error("Paystack verify failed: %s", response.text)
        response.raise_for_status()
    return PaystackTransactionVerify(**response.json())


async def charge_authorization(
    *,
    authorization_code: str,
    email: str,
    amount_kobo: int,
    reference: str,
    currency: str = "NGN",
    metadata: dict | None = None,
) -> PaystackChargeResponse:
    payload = {
        "authorization_code": authorization_code,
        "email": email,
        "amount": amount_kobo,
        "reference": reference,
        "currency": currency,
        "metadata": metadata or {},
    }
    response = await _http().post(
        "/transaction/charge_authorization",
        headers=_headers(),
        json=payload,
    )
    if response.status_code >= 400:
        logger.error("Paystack charge_authorization failed: %s", response.text)
        response.raise_for_status()
    return PaystackChargeResponse(**response.json())


async def create_plan(
    *,
    name: str,
    amount_kobo: int,
    interval: str = "monthly",
    currency: str = "NGN",
) -> PaystackPlanResponse:
    payload = {
        "name": name,
        "amount": amount_kobo,
        "interval": interval,
        "currency": currency,
    }
    response = await _http().post("/plan", headers=_headers(), json=payload)
    if response.status_code >= 400:
        logger.error("Paystack create_plan failed: %s", response.text)
        response.raise_for_status()
    return PaystackPlanResponse(**response.json())


async def list_plans(*, per_page: int = 100, page: int = 1) -> PaystackPlanListResponse:
    response = await _http().get(
        "/plan",
        headers=_headers(),
        params={"perPage": per_page, "page": page},
    )
    if response.status_code >= 400:
        logger.error("Paystack list_plans failed: %s", response.text)
        response.raise_for_status()
    return PaystackPlanListResponse(**response.json())


async def fetch_plan(id_or_code: str) -> PaystackPlanResponse:
    response = await _http().get(f"/plan/{id_or_code}", headers=_headers())
    if response.status_code >= 400:
        logger.error("Paystack fetch_plan failed: %s", response.text)
        response.raise_for_status()
    return PaystackPlanResponse(**response.json())


async def create_subscription(
    *,
    customer: str,
    plan: str,
    authorization: str | None = None,
    start_date: str | None = None,
) -> PaystackSubscriptionResponse:
    payload: dict = {"customer": customer, "plan": plan}
    if authorization:
        payload["authorization"] = authorization
    if start_date:
        payload["start_date"] = start_date
    response = await _http().post("/subscription", headers=_headers(), json=payload)
    if response.status_code >= 400:
        logger.error("Paystack create_subscription failed: %s", response.text)
        response.raise_for_status()
    return PaystackSubscriptionResponse(**response.json())


async def fetch_subscription(id_or_code: str) -> PaystackSubscriptionResponse:
    response = await _http().get(
        f"/subscription/{id_or_code}",
        headers=_headers(),
    )
    if response.status_code >= 400:
        logger.error("Paystack fetch_subscription failed: %s", response.text)
        response.raise_for_status()
    return PaystackSubscriptionResponse(**response.json())


async def disable_subscription(*, code: str, token: str) -> PaystackMessage:
    response = await _http().post(
        "/subscription/disable",
        headers=_headers(),
        json={"code": code, "token": token},
    )
    if response.status_code >= 400:
        logger.error("Paystack disable_subscription failed: %s", response.text)
        response.raise_for_status()
    return PaystackMessage(**response.json())


async def enable_subscription(*, code: str, token: str) -> PaystackMessage:
    response = await _http().post(
        "/subscription/enable",
        headers=_headers(),
        json={"code": code, "token": token},
    )
    if response.status_code >= 400:
        logger.error("Paystack enable_subscription failed: %s", response.text)
        response.raise_for_status()
    return PaystackMessage(**response.json())


async def manage_link(code: str) -> PaystackManageLinkResponse:
    response = await _http().get(
        f"/subscription/{code}/manage/link",
        headers=_headers(),
    )
    if response.status_code >= 400:
        logger.error("Paystack manage_link failed: %s", response.text)
        response.raise_for_status()
    return PaystackManageLinkResponse(**response.json())
