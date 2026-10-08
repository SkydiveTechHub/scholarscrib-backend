import asyncio

import pytest

from app.services.provider.base import (
    DrawResult,
    NormalizedQuestion,
    PaperFetchMode,
    SearchPage,
)
from app.services.provider.cache import (
    PAPER_CACHE_TTL_SECONDS,
    decode_page_cursor,
    encode_page_cursor,
    fetch_complete_paper,
    fetch_provider_paper,
    get_or_fill_paper,
    paginate_paper,
    paper_cache_id,
)


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.values = {}
        self.ttls = {}

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        self.ttls[key] = ex
        return True

    async def exists(self, key):
        return int(key in self.values)

    async def eval(self, script, numkeys, key, *args):
        if script.lstrip().startswith("if redis.call"):
            if self.values.get(key) == args[0]:
                del self.values[key]
                self.ttls.pop(key, None)
                return 1
            return 0
        fields = dict(zip(args[:-1:2], args[1:-1:2], strict=True))
        self.hashes[key] = fields
        self.ttls[key] = int(args[-1])
        return 1

    def pipeline(self, *, transaction):
        raise AssertionError("Cache publication must use an atomic Lua script.")


class FakeProvider:
    name = "ALOC"
    configured = True
    supports_search = True
    paper_fetch_mode = PaperFetchMode.COMPLETE

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def search(self, *, subject_slug, exam_type, exam_year, limit, cursor=None):
        self.calls.append((subject_slug, exam_type, exam_year, limit, cursor))
        return self.pages[cursor]

    def normalize(self, item):
        return NormalizedQuestion(
            provider_id=str(item.get("id") or ""),
            text="",
            options={},
            answer="",
            explanation="",
            image_url=None,
            raw=item,
        )


def test_paper_cache_identity_is_normalized_and_cursor_independent():
    assert paper_cache_id("aloc", " Biology ", "jamb", 2012) == paper_cache_id(
        "ALOC", "biology", "JAMB", 2012
    )
    assert paper_cache_id("ALOC", "biology", "JAMB", 2012) != paper_cache_id(
        "ALOC", "biology", "WAEC", 2012
    )


async def test_fetch_complete_paper_walks_provider_pages_once():
    provider = FakeProvider(
        {
            None: SearchPage(
                items=[{"id": "q1"}],
                next_cursor="page-2",
                has_more=True,
                credits_remaining=90,
            ),
            "page-2": SearchPage(items=[{"id": "q2"}], credits_remaining=80),
        }
    )

    result = await fetch_complete_paper(
        provider,
        subject_slug="biology",
        exam_type="JAMB",
        exam_year=2012,
    )

    assert result.exhausted
    assert result.items == [{"id": "q1"}, {"id": "q2"}]
    assert result.credits_remaining == 80
    assert [call[-1] for call in provider.calls] == [None, "page-2"]


async def test_sampled_provider_batches_are_deduplicated_and_not_marked_complete():
    provider = FakeProvider(
        {
            None: SearchPage(
                items=[{"id": "q1"}, {"id": "q1"}, {"id": "q2"}],
            )
        }
    )
    provider.paper_fetch_mode = PaperFetchMode.SAMPLED

    result = await fetch_provider_paper(
        provider,
        subject_slug="biology",
        exam_type="JAMB",
        exam_year=2012,
    )

    assert [item["id"] for item in result.items] == ["q1", "q2"]
    assert result.complete is False
    assert result.cacheable is True
    assert result.last_batch_count == 3


async def test_cached_paper_is_shared_across_local_pages_and_expires_after_90_days():
    redis = FakeRedis()
    provider = FakeProvider(
        {None: SearchPage(items=[{"id": str(i)} for i in range(5)])}
    )
    cache_id = paper_cache_id(provider.name, "biology", "JAMB", 2012)

    async def fill():
        return await fetch_complete_paper(
            provider,
            subject_slug="biology",
            exam_type="JAMB",
            exam_year=2012,
        )

    paper = await get_or_fill_paper(redis, cache_id=cache_id, loader=fill)
    next_cursor = encode_page_cursor(cache_id, 2)
    second_page = paginate_paper(paper, cache_id=cache_id, limit=2, cursor=next_cursor)
    cached = await get_or_fill_paper(redis, cache_id=cache_id, loader=fill)

    assert second_page["questions"] == [{"id": "2"}, {"id": "3"}]
    assert second_page["pagination"]["hasMore"]
    assert decode_page_cursor(second_page["pagination"]["nextCursor"], cache_id) == 4
    assert cached.items == paper.items
    assert len(provider.calls) == 1
    assert PAPER_CACHE_TTL_SECONDS == 90 * 24 * 60 * 60
    assert set(redis.ttls.values()) == {PAPER_CACHE_TTL_SECONDS}


async def test_sampled_pool_is_locally_paginated_and_enriched_without_duplicate_ids():
    redis = FakeRedis()
    cache_id = paper_cache_id("SDASH", "biology", "JAMB", 2012)
    calls = 0

    async def first_batch():
        nonlocal calls
        calls += 1
        return DrawResult(
            items=[{"id": "1"}, {"id": "2"}],
            cacheable=True,
            last_batch_count=2,
        )

    async def second_batch():
        nonlocal calls
        calls += 1
        return DrawResult(
            items=[{"id": "2"}, {"id": "3"}],
            cacheable=True,
            last_batch_count=2,
        )

    first = await get_or_fill_paper(redis, cache_id=cache_id, loader=first_batch)
    page = paginate_paper(first, cache_id=cache_id, limit=1, cursor=None)
    enriched = await get_or_fill_paper(
        redis,
        cache_id=cache_id,
        loader=second_batch,
        enrich_sample=True,
        item_identity=lambda item: str(item["id"]),
    )
    reused = await get_or_fill_paper(redis, cache_id=cache_id, loader=second_batch)

    assert first.complete is False
    assert page["pagination"]["coverage"] == "sampled"
    assert page["pagination"]["hasMore"] is True
    assert [item["id"] for item in enriched.items] == ["1", "2", "3"]
    assert enriched.last_batch_count == 2
    assert reused.items == enriched.items
    assert reused.complete is False
    assert calls == 2


async def test_concurrent_cache_misses_share_one_provider_fill(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr("app.services.provider.cache.PAPER_FILL_POLL_SECONDS", 0.01)
    cache_id = paper_cache_id("ALOC", "biology", "JAMB", 2012)
    calls = 0

    async def fill():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.03)
        return DrawResult(items=[{"id": "q1"}], exhausted=True, complete=True)

    left, right = await asyncio.gather(
        get_or_fill_paper(redis, cache_id=cache_id, loader=fill),
        get_or_fill_paper(redis, cache_id=cache_id, loader=fill),
    )

    assert calls == 1
    assert left.items == right.items == [{"id": "q1"}]


async def test_incomplete_paper_is_not_cached():
    redis = FakeRedis()
    cache_id = paper_cache_id("ALOC", "biology", "JAMB", 2012)
    result = DrawResult(items=[{"id": "partial"}], exhausted=False)

    returned = await get_or_fill_paper(
        redis, cache_id=cache_id, loader=lambda: _completed(result)
    )

    assert returned is result
    assert redis.hashes == {}


async def _completed(result):
    return result


async def test_not_found_paper_is_negative_cached():
    redis = FakeRedis()
    cache_id = paper_cache_id("ALOC", "biology", "JAMB", 1990)
    calls = 0

    async def missing():
        nonlocal calls
        calls += 1
        return DrawResult(status_code=404, exhausted=True, complete=True)

    first = await get_or_fill_paper(redis, cache_id=cache_id, loader=missing)
    second = await get_or_fill_paper(redis, cache_id=cache_id, loader=missing)

    assert first.status_code == second.status_code == 404
    assert calls == 1


async def test_not_found_after_first_page_does_not_cache_a_partial_paper():
    redis = FakeRedis()
    provider = FakeProvider(
        {
            None: SearchPage(
                items=[{"id": "partial"}],
                next_cursor="page-2",
                has_more=True,
            ),
            "page-2": SearchPage(status_code=404),
        }
    )

    async def fill():
        return await fetch_complete_paper(
            provider,
            subject_slug="biology",
            exam_type="JAMB",
            exam_year=2012,
        )

    result = await get_or_fill_paper(
        redis,
        cache_id=paper_cache_id("ALOC", "biology", "JAMB", 2012),
        loader=fill,
    )

    assert result.status_code == 502
    assert result.complete is False
    assert redis.hashes == {}
    assert len(provider.calls) == 2


def test_cursor_is_bound_to_paper_and_rejects_invalid_tokens():
    one = paper_cache_id("ALOC", "biology", "JAMB", 2012)
    another = paper_cache_id("ALOC", "chemistry", "JAMB", 2012)
    token = encode_page_cursor(one, 15)

    assert decode_page_cursor(token, one) == 15
    with pytest.raises(ValueError, match="Invalid question pagination cursor"):
        decode_page_cursor(token, another)
    with pytest.raises(ValueError, match="Invalid question pagination cursor"):
        decode_page_cursor("not-a-cursor", one)
