"""Async Lago REST client (httpx). Lago is not on the request-path hot path."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.config import settings
from app.core.logger import logger

_TIMEOUT = 20.0
_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


class LagoError(Exception):
    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


def _http() -> httpx.AsyncClient:
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client = httpx.AsyncClient(timeout=_TIMEOUT)
        _client_loop = loop
    return _client


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.lago_api_key}",
        "Content-Type": "application/json",
    }


def _url(path: str) -> str:
    base = settings.lago_api_url.rstrip("/")
    return f"{base}/{path.lstrip('/')}"


async def _request(
    method: str, path: str, *, json: dict | None = None, params: dict | None = None
) -> dict | None:
    if not settings.lago_enabled:
        raise LagoError("Lago is not configured")
    response = await _http().request(
        method, _url(path), headers=_headers(), json=json, params=params
    )
    if response.status_code >= 400:
        logger.error(
            "Lago %s %s -> %s %s", method, path, response.status_code, response.text
        )
        raise LagoError(
            f"Lago request failed ({response.status_code})",
            status=response.status_code,
            body=response.text,
        )
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


async def upsert_customer(
    *,
    external_id: str,
    email: str | None,
    name: str | None = None,
    currency: str = "NGN",
) -> dict:
    payload = {
        "customer": {
            "external_id": external_id,
            "email": email,
            "name": name or email or external_id,
            "currency": currency,
        }
    }
    try:
        return await _request("POST", "/customers", json=payload) or {}
    except LagoError as exc:
        if exc.status == 422:
            return (
                await _request("PUT", f"/customers/{external_id}", json=payload) or {}
            )
        raise


async def create_subscription(
    *,
    external_customer_id: str,
    plan_code: str,
    external_id: str,
    ending_at: str | None = None,
    amount_cents: int | None = None,
) -> dict:
    subscription: dict[str, Any] = {
        "external_customer_id": external_customer_id,
        "plan_code": plan_code,
        "external_id": external_id,
        "billing_time": "anniversary",
    }
    if ending_at:
        subscription["ending_at"] = ending_at
    if amount_cents is not None:
        subscription["plan_overrides"] = {"amount_cents": amount_cents}
    return (
        await _request("POST", "/subscriptions", json={"subscription": subscription})
        or {}
    )


async def terminate_subscription(external_id: str) -> dict | None:
    try:
        return await _request(
            "DELETE", f"/subscriptions/{external_id}", params={"status": "active"}
        )
    except LagoError as exc:
        if exc.status == 404:
            return None
        raise


async def update_invoice_payment_status(
    lago_id: str, *, payment_status: str
) -> dict | None:
    return await _request(
        "PUT",
        f"/invoices/{lago_id}",
        json={"invoice": {"payment_status": payment_status}},
    )


async def create_plan(
    *,
    code: str,
    name: str,
    amount_cents: int,
    currency: str = "NGN",
    interval: str = "monthly",
    description: str | None = None,
) -> dict:
    payload = {
        "plan": {
            "name": name,
            "code": code,
            "interval": interval,
            "amount_cents": amount_cents,
            "amount_currency": currency,
            "pay_in_advance": True,
            "description": description or name,
        }
    }
    try:
        return await _request("POST", "/plans", json=payload) or {}
    except LagoError as exc:
        if exc.status in {422, 409}:
            return await _request("PUT", f"/plans/{code}", json=payload) or {}
        raise


async def create_feature(
    *,
    code: str,
    name: str,
    description: str | None = None,
    privileges: list[dict] | None = None,
) -> dict:
    feature: dict[str, Any] = {
        "code": code,
        "name": name,
        "description": description or name,
    }
    if privileges:
        feature["privileges"] = privileges
    payload = {"feature": feature}
    try:
        return await _request("POST", "/features", json=payload) or {}
    except LagoError as exc:
        if exc.status in {422, 409}:
            return {}
        raise


async def set_plan_entitlements(plan_code: str, entitlements: dict) -> dict | None:
    return await _request(
        "POST",
        f"/plans/{plan_code}/entitlements",
        json={"entitlements": entitlements},
    )


def verify_hmac_signature(
    body: bytes, signature: str | None, secret: str | None
) -> bool:
    if not signature or not secret:
        return False
    import base64
    import hashlib
    import hmac

    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)
