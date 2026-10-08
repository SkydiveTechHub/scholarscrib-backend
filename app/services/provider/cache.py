import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster
from redis.exceptions import RedisError
from redis_fastapi import get_settings

from app.services.provider.base import (
    PROVIDER_PAPER_MAX_PAGES,
    PROVIDER_PAPER_PAGE_LIMIT,
    PROVIDER_SAMPLE_BATCH_LIMIT,
    DrawResult,
    PaperFetchMode,
    QuestionProvider,
)

logger = logging.getLogger(__name__)

PAPER_CACHE_TTL_SECONDS = 90 * 24 * 60 * 60
PAPER_FILL_LOCK_TTL_SECONDS = 10 * 60
PAPER_FILL_WAIT_SECONDS = 30
PAPER_FILL_POLL_SECONDS = 0.25

AsyncRedisClient = Redis | RedisCluster
PaperLoader = Callable[[], Awaitable[DrawResult]]

_RELEASE_LOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
"""
_PUBLISH_PAPER_SCRIPT = """
redis.call("DEL", KEYS[1])
for i = 1, #ARGV - 1, 2 do
    redis.call("HSET", KEYS[1], ARGV[i], ARGV[i + 1])
end
return redis.call("EXPIRE", KEYS[1], tonumber(ARGV[#ARGV]))
"""


class PaperCacheError(Exception):
    """Redis could not safely serve or populate a provider paper."""


def paper_cache_id(
    provider: str,
    subject_slug: str | None,
    exam_type: str | None,
    exam_year: int | None,
) -> str:
    filters = {
        "provider": provider.strip().upper(),
        "subject": (subject_slug or "").strip().lower(),
        "exam": (exam_type or "").strip().upper(),
        "year": exam_year,
    }
    canonical = json.dumps(filters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _paper_keys(cache_id: str) -> tuple[str, str]:
    prefix = get_settings().pattern_prefix("provider-paper")
    hash_tag = f"{{{cache_id}}}"
    return f"{prefix}:{hash_tag}:data", f"{prefix}:{hash_tag}:fill"


def encode_page_cursor(cache_id: str, offset: int) -> str:
    payload = json.dumps(
        {"cache": cache_id, "offset": offset, "version": 1},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode()


def decode_page_cursor(cursor: str, cache_id: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        offset = payload["offset"]
        if (
            payload.get("version") != 1
            or payload.get("cache") != cache_id
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset <= 0
        ):
            raise ValueError
    except (
        ValueError,
        TypeError,
        KeyError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("Invalid question pagination cursor") from exc
    return offset


def paginate_paper(
    result: DrawResult,
    *,
    cache_id: str,
    limit: int,
    cursor: str | None,
) -> dict:
    offset = decode_page_cursor(cursor, cache_id) if cursor else 0
    if offset > len(result.items):
        raise ValueError("Invalid question pagination cursor")
    end = min(offset + limit, len(result.items))
    has_more = end < len(result.items)
    return {
        "questions": result.items[offset:end],
        "pagination": {
            "limit": limit,
            "cursor": cursor,
            "nextCursor": encode_page_cursor(cache_id, end) if has_more else None,
            "hasMore": has_more,
            "coverage": (
                PaperFetchMode.COMPLETE.value
                if result.complete
                else PaperFetchMode.SAMPLED.value
            ),
        },
    }


def _decode(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def _read_paper(
    redis: AsyncRedisClient, data_key: str, cache_id: str
) -> DrawResult | None:
    try:
        stored = await redis.hgetall(data_key)
    except (RedisError, OSError) as exc:
        logger.error("Unable to read provider paper cache %s", cache_id, exc_info=True)
        raise PaperCacheError("Question cache is temporarily unavailable.") from exc
    if not stored:
        return None

    fields = {_decode(key): _decode(value) for key, value in stored.items()}
    if fields.get("state") != "complete":
        logger.error("Incomplete provider paper cache entry %s", cache_id)
        raise PaperCacheError("Question cache contains an incomplete paper.")
    try:
        count = int(fields["count"])
        status_code = int(fields["status_code"])
        coverage = fields.get("coverage", PaperFetchMode.COMPLETE.value)
        items = [json.loads(fields[f"item:{index}"]) for index in range(count)]
        credits_raw = fields.get("credits_remaining", "")
        credits_remaining = int(credits_raw) if credits_raw else None
        if (
            count < 0
            or status_code not in {200, 404}
            or coverage not in {mode.value for mode in PaperFetchMode}
            or (status_code == 404 and coverage != PaperFetchMode.COMPLETE.value)
            or not all(isinstance(item, dict) for item in items)
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.error("Invalid provider paper cache entry %s", cache_id, exc_info=True)
        raise PaperCacheError("Question cache contains an invalid paper.") from exc
    return DrawResult(
        items=items,
        status_code=status_code,
        credits_remaining=credits_remaining,
        exhausted=coverage == PaperFetchMode.COMPLETE.value,
        complete=coverage == PaperFetchMode.COMPLETE.value,
        cacheable=True,
    )


async def _write_paper(
    redis: AsyncRedisClient,
    data_key: str,
    cache_id: str,
    result: DrawResult,
) -> None:
    fields = {
        "state": "complete",
        "count": str(len(result.items)),
        "status_code": str(result.status_code),
        "coverage": (
            PaperFetchMode.COMPLETE.value
            if result.complete
            else PaperFetchMode.SAMPLED.value
        ),
        "credits_remaining": (
            str(result.credits_remaining)
            if result.credits_remaining is not None
            else ""
        ),
    }
    fields.update(
        {
            f"item:{index}": json.dumps(item, separators=(",", ":"), ensure_ascii=False)
            for index, item in enumerate(result.items)
        }
    )
    args: list[str | int] = []
    for field, value in fields.items():
        args.extend((field, value))
    args.append(PAPER_CACHE_TTL_SECONDS)
    try:
        await redis.eval(_PUBLISH_PAPER_SCRIPT, 1, data_key, *args)
    except (RedisError, OSError) as exc:
        logger.error(
            "Unable to publish provider paper cache %s", cache_id, exc_info=True
        )
        raise PaperCacheError("Question cache is temporarily unavailable.") from exc


async def _release_lock(
    redis: AsyncRedisClient, lock_key: str, token: str, cache_id: str
) -> None:
    try:
        await redis.eval(_RELEASE_LOCK_SCRIPT, 1, lock_key, token)
    except (RedisError, OSError):
        logger.warning(
            "Unable to release provider paper lock %s", cache_id, exc_info=True
        )


async def get_or_fill_paper(
    redis: AsyncRedisClient,
    *,
    cache_id: str,
    loader: PaperLoader,
    enrich_sample: bool = False,
    item_identity: Callable[[dict], str] | None = None,
) -> DrawResult:
    data_key, lock_key = _paper_keys(cache_id)
    cached = await _read_paper(redis, data_key, cache_id)
    if cached is not None and (not enrich_sample or cached.complete):
        return cached

    token = secrets.token_urlsafe(24)
    try:
        acquired = await redis.set(
            lock_key, token, nx=True, ex=PAPER_FILL_LOCK_TTL_SECONDS
        )
    except (RedisError, OSError) as exc:
        logger.error(
            "Unable to acquire provider paper lock %s", cache_id, exc_info=True
        )
        raise PaperCacheError("Question cache is temporarily unavailable.") from exc

    if acquired:
        try:
            cached = await _read_paper(redis, data_key, cache_id)
            if cached is not None and (not enrich_sample or cached.complete):
                return cached
            result = await loader()
            if (
                cached is not None
                and not cached.complete
                and enrich_sample
                and not result.failed
            ):
                if item_identity is None:
                    raise ValueError("Sample enrichment requires an item identity.")
                known = {item_identity(item) for item in cached.items}
                additions = []
                for item in result.items:
                    identity = item_identity(item)
                    if identity not in known:
                        known.add(identity)
                        additions.append(item)
                result = DrawResult(
                    items=[*cached.items, *additions],
                    credits_remaining=(
                        result.credits_remaining
                        if result.credits_remaining is not None
                        else cached.credits_remaining
                    ),
                    cacheable=True,
                    last_batch_count=len(result.items),
                )
            if (result.complete or result.cacheable) and (
                not result.failed or result.status_code == 404
            ):
                await _write_paper(redis, data_key, cache_id, result)
                result.cacheable = True
            return result
        finally:
            await _release_lock(redis, lock_key, token, cache_id)

    deadline = time.monotonic() + PAPER_FILL_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(PAPER_FILL_POLL_SECONDS)
        cached = await _read_paper(redis, data_key, cache_id)
        if cached is not None:
            return cached
        try:
            still_filling = await redis.exists(lock_key)
        except (RedisError, OSError) as exc:
            logger.error(
                "Unable to check provider paper lock %s", cache_id, exc_info=True
            )
            raise PaperCacheError("Question cache is temporarily unavailable.") from exc
        if not still_filling:
            raise PaperCacheError("The question paper could not be fully cached.")

    raise PaperCacheError(
        "The question paper is still being prepared; try again shortly."
    )


async def fetch_complete_paper(
    provider: QuestionProvider,
    *,
    subject_slug: str | None,
    exam_type: str | None,
    exam_year: int | None,
) -> DrawResult:
    items: list[dict] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    credits_remaining: int | None = None

    for page_number in range(PROVIDER_PAPER_MAX_PAGES):
        page = await provider.search(
            subject_slug=subject_slug,
            exam_type=exam_type,
            exam_year=exam_year,
            limit=PROVIDER_PAPER_PAGE_LIMIT,
            cursor=cursor,
        )
        if page is None:
            return DrawResult(status_code=503, body="Provider search unavailable.")
        if page.status_code == 404:
            if page_number == 0:
                return DrawResult(
                    status_code=404,
                    exhausted=True,
                    complete=True,
                    body=page.body,
                    last_batch_count=0,
                )
            return DrawResult(
                status_code=502,
                body="Provider rejected a cursor during the paper page walk.",
                credits_remaining=credits_remaining,
            )
        if page.failed:
            return DrawResult(
                status_code=page.status_code,
                body=page.body,
                credits_remaining=page.credits_remaining,
            )
        if page.credits_remaining is not None:
            credits_remaining = page.credits_remaining
        items.extend(item for item in page.items if isinstance(item, dict))
        if not page.has_more:
            return DrawResult(
                items=items,
                credits_remaining=credits_remaining,
                exhausted=True,
                complete=True,
                last_batch_count=len(items),
            )
        next_cursor = page.next_cursor
        if not next_cursor or next_cursor in seen_cursors:
            return DrawResult(
                status_code=502,
                body="Provider returned an invalid pagination sequence.",
                credits_remaining=credits_remaining,
            )
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return DrawResult(
        status_code=502,
        body="Provider paper exceeds the supported page-walk limit.",
        credits_remaining=credits_remaining,
    )


async def fetch_provider_paper(
    provider: QuestionProvider,
    *,
    subject_slug: str | None,
    exam_type: str | None,
    exam_year: int | None,
) -> DrawResult:
    if provider.paper_fetch_mode == PaperFetchMode.COMPLETE:
        return await fetch_complete_paper(
            provider,
            subject_slug=subject_slug,
            exam_type=exam_type,
            exam_year=exam_year,
        )

    if provider.supports_search:
        page = await provider.search(
            subject_slug=subject_slug,
            exam_type=exam_type,
            exam_year=exam_year,
            limit=PROVIDER_SAMPLE_BATCH_LIMIT,
        )
    elif subject_slug and exam_type and exam_year is not None:
        result = await provider.draw(
            subject_slug,
            exam_type,
            exam_year,
            PROVIDER_SAMPLE_BATCH_LIMIT,
        )
        if result is None:
            return DrawResult(status_code=503, body="Provider sample unavailable.")
        if result.status_code == 404:
            result.complete = True
            result.exhausted = True
            result.cacheable = True
            return result
        if not result.failed and result.items:
            batch_count = len(result.items)
            result.items = _deduplicate_sample(provider, result.items)
            result.cacheable = True
            result.last_batch_count = batch_count
        return result
    else:
        return DrawResult(
            status_code=503,
            body="Provider does not support sampled searches for these filters.",
        )
    if page is None:
        return DrawResult(status_code=503, body="Provider sample unavailable.")
    if page.status_code == 404:
        return DrawResult(
            status_code=404,
            body=page.body,
            exhausted=True,
            complete=True,
            cacheable=True,
        )
    if page.failed:
        return DrawResult(
            status_code=page.status_code,
            body=page.body,
            credits_remaining=page.credits_remaining,
        )
    batch_count = len(page.items)
    items = _deduplicate_sample(provider, page.items)
    if not items:
        return DrawResult(
            status_code=502,
            body="Provider returned an empty sample without confirming no results.",
        )
    return DrawResult(
        items=items,
        credits_remaining=page.credits_remaining,
        cacheable=True,
        last_batch_count=batch_count,
    )


def sample_item_identity(provider: QuestionProvider, item: dict) -> str:
    normalized = provider.normalize(item)
    if normalized.provider_id.strip():
        return f"id:{normalized.provider_id.strip()}"
    canonical = json.dumps(item, sort_keys=True, separators=(",", ":"), default=str)
    return f"payload:{hashlib.sha256(canonical.encode()).hexdigest()}"


def _deduplicate_sample(provider: QuestionProvider, items: list[dict]) -> list[dict]:
    deduplicated = []
    known: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        identity = sample_item_identity(provider, item)
        if identity not in known:
            known.add(identity)
            deduplicated.append(item)
    return deduplicated
