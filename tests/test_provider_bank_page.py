"""Gradual question-bank population: checkpoint, lease, schedule, advance."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.core.errors import RateLimited
from app.core.timeutil import utcnow
from app.database.models import ProviderFetch
from app.services.provider import schedule as schedule_mod
from app.services.provider import service as provider_service
from app.services.provider.aloc import AlocProvider
from app.services.provider.base import SearchPage
from app.services.provider.ingest import ProviderPaperIngest

SUBJECT = SimpleNamespace(id="subj-1", slug="mathematics", name="Mathematics")


class FakeSession:
    async def flush(self):
        return None

    async def refresh(self, _obj):
        return None


def _item(index: int) -> dict:
    return {
        "id": f"q-{index}",
        "text": f"Question {index}",
        "options": {"A": "1", "B": "2", "C": "3", "D": "4"},
        "correctAnswer": "B",
        "examType": "jamb",
        "year": 2015,
        "questionNumber": index,
    }


class FakeProvider:
    name = "ALOC"
    configured = True

    def __init__(self, pages: dict[str | None, SearchPage]):
        self.pages = pages
        self.calls: list[dict] = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        cursor = kwargs.get("cursor")
        return self.pages.get(cursor, SearchPage(items=[], has_more=False))

    def normalize(self, item):
        return AlocProvider(api_key="key").normalize(item)


def test_checkpoint_advances_only_for_expected_cursor():
    ingest = ProviderPaperIngest(
        None,
        FakeProvider({}),
        provider_subject="mathematics",
        subject=SUBJECT,
        exam_type="JAMB",
        exam_year=2015,
    )
    fetch = ProviderFetch(
        id="f1",
        provider="ALOC",
        cache_key="mathematics|JAMB|2015",
        status="PENDING",
        next_cursor=None,
    )
    ingest._advance_checkpoint(fetch, None, "c1", has_more=True)
    assert fetch.next_cursor == "c1"
    assert fetch.status == "PENDING"
    assert fetch.draw_count == 1
    assert fetch.checkpoint_at is not None

    # A student page ahead of the checkpoint must not skip the watermark.
    ingest._advance_checkpoint(fetch, "c9", "c10", has_more=True)
    assert fetch.next_cursor == "c1"

    ingest._advance_checkpoint(fetch, "c1", "c2", has_more=True)
    assert fetch.next_cursor == "c2"

    ingest._advance_checkpoint(fetch, "c2", None, has_more=False)
    assert fetch.next_cursor is None
    assert fetch.status == "SATURATED"


def test_schedule_skips_when_disabled_or_incomplete(monkeypatch):
    added: list = []

    class Background:
        def add_task(self, fn, *args):
            added.append((fn, args))

    monkeypatch.setattr(settings, "question_provider_enabled", "false")
    assert (
        schedule_mod.schedule_provider_bank_page(Background(), "maths", "JAMB", 2015)
        is False
    )
    assert added == []

    monkeypatch.setattr(settings, "question_provider_enabled", "true")
    assert (
        schedule_mod.schedule_provider_bank_page(Background(), "maths", "JAMB", None)
        is False
    )
    assert (
        schedule_mod.schedule_provider_bank_page(Background(), None, "JAMB", 2015)
        is False
    )


def test_schedule_queues_job_and_honours_rate_limit(monkeypatch):
    added: list = []

    class Background:
        def add_task(self, fn, *args):
            added.append((fn, args))

    monkeypatch.setattr(settings, "question_provider_enabled", "true")
    monkeypatch.setattr(schedule_mod, "hit", lambda *a, **k: None)
    assert (
        schedule_mod.schedule_provider_bank_page(Background(), " Maths ", "jamb", 2015)
        is True
    )
    assert len(added) == 1
    assert added[0][1] == ("maths", "JAMB", 2015)

    added.clear()

    def limited(*_a, **_k):
        raise RateLimited(30)

    monkeypatch.setattr(schedule_mod, "hit", limited)
    assert (
        schedule_mod.schedule_provider_bank_page(Background(), "maths", "JAMB", 2015)
        is False
    )
    assert added == []


@pytest.fixture
def advance_repos(monkeypatch):
    fetch_store: dict[str, ProviderFetch] = {}

    async def by_slug(_session, slug):
        return SUBJECT if slug == "mathematics" else None

    async def by_cache_key(_session, provider, key):
        return fetch_store.get(f"{provider}:{key}")

    async def add(_session, row, flush=False):
        fetch_store[f"{row.provider}:{row.cache_key}"] = row

    async def load_state(_session, _name=None):
        return SimpleNamespace(
            circuit="OK",
            cooldown_until=None,
            credits_remaining=None,
        )

    monkeypatch.setattr(provider_service.subjects_repository, "by_slug", by_slug)
    monkeypatch.setattr(
        provider_service.fetches_repository, "by_cache_key", by_cache_key
    )
    monkeypatch.setattr(provider_service.fetches_repository, "add", add)
    monkeypatch.setattr(provider_service, "load_provider_state", load_state)
    return fetch_store


async def test_advance_first_page_sets_cursor(advance_repos, monkeypatch):
    provider = FakeProvider({})
    stored_pairs = [(SimpleNamespace(id="q1"), _item(1))]

    class FakeIngest:
        def __init__(self, *a, **k):
            pass

        async def page_result(self, cursor=None):
            assert cursor is None
            row = advance_repos["ALOC:mathematics|JAMB|2015"]
            row.next_cursor = "c1"
            row.status = "PENDING"
            row.checkpoint_at = utcnow()
            row.draw_count = 1
            row.promoted_count = 2
            return stored_pairs, "c1", 99

    monkeypatch.setattr(provider_service, "ProviderPaperIngest", FakeIngest)

    result = await provider_service.AdvanceProviderBankPageService(
        FakeSession(), "mathematics", "JAMB", 2015, provider=provider
    ).process()

    assert result["status"] == "PENDING"
    assert result["nextCursor"] == "c1"
    assert result["stored"] == 1
    row = advance_repos["ALOC:mathematics|JAMB|2015"]
    assert row.started_at is None
    assert row.next_cursor == "c1"


async def test_advance_noops_when_saturated(advance_repos):
    key = "mathematics|JAMB|2015"
    advance_repos[f"ALOC:{key}"] = ProviderFetch(
        id="f1",
        provider="ALOC",
        cache_key=key,
        status="SATURATED",
        subject_id=SUBJECT.id,
        exam_type="JAMB",
        exam_year=2015,
    )
    provider = FakeProvider({})
    result = await provider_service.AdvanceProviderBankPageService(
        FakeSession(), "mathematics", "JAMB", 2015, provider=provider
    ).process()
    assert result["status"] == "SATURATED"
    assert provider.calls == []


async def test_advance_respects_active_lease(advance_repos):
    key = "mathematics|JAMB|2015"
    advance_repos[f"ALOC:{key}"] = ProviderFetch(
        id="f1",
        provider="ALOC",
        cache_key=key,
        status="PENDING",
        started_at=utcnow(),
        subject_id=SUBJECT.id,
        exam_type="JAMB",
        exam_year=2015,
        next_cursor="c1",
    )
    provider = FakeProvider({})
    result = await provider_service.AdvanceProviderBankPageService(
        FakeSession(), "mathematics", "JAMB", 2015, provider=provider
    ).process()
    assert result.get("leased") is True
    assert provider.calls == []


async def test_advance_skips_open_circuit(advance_repos, monkeypatch):
    key = "mathematics|JAMB|2015"
    advance_repos[f"ALOC:{key}"] = ProviderFetch(
        id="f1",
        provider="ALOC",
        cache_key=key,
        status="PENDING",
        subject_id=SUBJECT.id,
        exam_type="JAMB",
        exam_year=2015,
    )

    async def blocked(_session, _name=None):
        return SimpleNamespace(
            circuit="BLOCKED",
            cooldown_until=None,
            credits_remaining=0,
        )

    monkeypatch.setattr(provider_service, "load_provider_state", blocked)
    provider = FakeProvider({None: SearchPage(items=[_item(1)], has_more=False)})
    result = await provider_service.AdvanceProviderBankPageService(
        FakeSession(), "mathematics", "JAMB", 2015, provider=provider
    ).process()
    assert result.get("skipped") == "circuit"
    assert provider.calls == []


async def test_advance_final_page_saturates(advance_repos, monkeypatch):
    key = "mathematics|JAMB|2015"
    advance_repos[f"ALOC:{key}"] = ProviderFetch(
        id="f1",
        provider="ALOC",
        cache_key=key,
        status="PENDING",
        next_cursor="c2",
        subject_id=SUBJECT.id,
        exam_type="JAMB",
        exam_year=2015,
    )

    class FakeIngest:
        def __init__(self, *a, **k):
            pass

        async def page_result(self, cursor=None):
            assert cursor == "c2"
            row = advance_repos[f"ALOC:{key}"]
            row.next_cursor = None
            row.status = "SATURATED"
            row.checkpoint_at = utcnow()
            row.promoted_count = 5
            return [], None, 50

    monkeypatch.setattr(provider_service, "ProviderPaperIngest", FakeIngest)
    result = await provider_service.AdvanceProviderBankPageService(
        FakeSession(), "mathematics", "JAMB", 2015, provider=FakeProvider({})
    ).process()
    assert result["status"] == "SATURATED"
    assert result["nextCursor"] is None


async def test_saturate_loops_until_terminal(advance_repos, monkeypatch):
    calls = {"n": 0}

    async def advance(self):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"status": "PENDING", "nextCursor": f"c{calls['n']}"}
        return {"status": "SATURATED", "nextCursor": None}

    monkeypatch.setattr(
        provider_service.AdvanceProviderBankPageService, "process", advance
    )
    last = await provider_service.SaturateProviderService(
        FakeSession(), "mathematics", "JAMB", 2015, provider=FakeProvider({})
    ).process()
    assert calls["n"] == 3
    assert last["status"] == "SATURATED"


async def test_expired_lease_allows_advance(advance_repos, monkeypatch):
    key = "mathematics|JAMB|2015"
    advance_repos[f"ALOC:{key}"] = ProviderFetch(
        id="f1",
        provider="ALOC",
        cache_key=key,
        status="PENDING",
        started_at=utcnow() - timedelta(minutes=5),
        subject_id=SUBJECT.id,
        exam_type="JAMB",
        exam_year=2015,
        next_cursor=None,
    )

    class FakeIngest:
        def __init__(self, *a, **k):
            pass

        async def page_result(self, cursor=None):
            row = advance_repos[f"ALOC:{key}"]
            row.status = "SATURATED"
            row.next_cursor = None
            return [], None, None

    monkeypatch.setattr(provider_service, "ProviderPaperIngest", FakeIngest)
    result = await provider_service.AdvanceProviderBankPageService(
        FakeSession(), "mathematics", "JAMB", 2015, provider=FakeProvider({})
    ).process()
    assert result["status"] == "SATURATED"
