"""The provider-neutral catalogue: adapters, services and ALOC/SDash parity.

Both providers run against ``httpx.MockTransport`` with equivalent data, so
every neutral endpoint can be held to the same keys and types under either.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.api.responses import CatalogueSubjectsOut, CatalogueYearsOut
from app.core.config import settings
from app.core.errors import ApiError
from app.services.provider import catalogue as catalogue_module
from app.services.provider.aloc import AlocProvider
from app.services.provider.base import PaperFetchMode
from app.services.provider.cache import fetch_provider_paper
from app.services.provider.catalogue import (
    CatalogueSubjectsService,
    CatalogueYearsService,
)
from app.services.provider.sdash import (
    PROBE_CONCURRENCY,
    SAMPLE_DRAWS,
    SdashProvider,
    clear_reference_cache,
)

SDASH_URL = "https://sdash.test/api"
ALOC_URL = "https://aloc.test/v2"

PHYSICS = SimpleNamespace(
    id="subj-physics",
    slug="physics",
    name="Physics",
    track_category="SCIENCE",
    is_jamb=True,
    is_waec=True,
    is_neco=False,
)
CHEMISTRY = SimpleNamespace(
    id="subj-chem",
    slug="chemistry",
    name="Chemistry",
    track_category="SCIENCE",
    is_jamb=True,
    is_waec=False,
    is_neco=False,
)
LITERATURE = SimpleNamespace(
    id="subj-lit",
    slug="literature-in-english",
    name="Literature in English",
    track_category="ARTS",
    is_jamb=True,
    is_waec=True,
    is_neco=True,
)


@pytest.fixture(autouse=True)
def _fresh_reference_cache(monkeypatch):
    # A developer's own token must not make a "no token" provider configured.
    monkeypatch.setattr(settings, "sdash_access_token", "")
    clear_reference_cache()
    yield
    clear_reference_cache()


# ----- fakes ---------------------------------------------------------


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def exists(self, key):
        return int(key in self.values)

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        self.ttls[key] = ex
        return True

    async def eval(self, script, numkeys, key, *args):
        if self.values.get(key) == args[0]:
            del self.values[key]
            self.ttls.pop(key, None)
            return 1
        return 0


class FakeSession:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class Store:
    """What the three repositories hold, plus what the service wrote."""

    def __init__(self, bank=None, catalogued=()):
        self.bank = bank or []
        self.rows = [SimpleNamespace(exam_year=year) for year in catalogued]
        self.upserts: list[set[int]] = []


@pytest.fixture
def store(monkeypatch):
    holder = Store()

    async def by_id_or_slug(_session, key):
        return {s.slug: s for s in (PHYSICS, CHEMISTRY, LITERATURE)}.get(key)

    async def past_paper_groups(_session, exam_type, subject_id):
        return [(exam_type, year, subject_id, count) for year, count in holder.bank]

    async def matching(_session, exam_type, subject_id, provider=None):
        return list(holder.rows)

    async def upsert_years(_session, *, provider, subject_id, exam_type, years):
        fresh = set(years) - {row.exam_year for row in holder.rows}
        holder.upserts.append(set(years))
        holder.rows.extend(SimpleNamespace(exam_year=year) for year in sorted(fresh))
        return len(fresh)

    async def active_ordered(_session):
        return [PHYSICS, CHEMISTRY, LITERATURE]

    monkeypatch.setattr(
        catalogue_module.subjects_repository, "by_id_or_slug", by_id_or_slug
    )
    monkeypatch.setattr(
        catalogue_module.subjects_repository, "active_ordered", active_ordered
    )
    monkeypatch.setattr(
        catalogue_module.questions_repository, "past_paper_groups", past_paper_groups
    )
    monkeypatch.setattr(catalogue_module.catalogues_repository, "matching", matching)
    monkeypatch.setattr(
        catalogue_module.catalogues_repository, "upsert_years", upsert_years
    )
    return holder


# ----- provider mocks ------------------------------------------------


class Calls:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def probes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/v1/q")]


def sdash(papers, *, years=None, calls=None, token="token"):
    """An SDash provider holding ``papers``: {(subject, type, year)}."""
    calls = calls if calls is not None else Calls()
    reported = years or sorted({year for _s, _t, year in papers}, reverse=True)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.requests.append(request)
        path = request.url.path
        if path.endswith("/v1/years"):
            return httpx.Response(200, json={"status": 200, "data": reported})
        if path.endswith("/v1/subjects"):
            return httpx.Response(
                200,
                json={
                    "status": 200,
                    "data": [
                        {"slug": "physics", "name": "Physics"},
                        {"slug": "englishlit", "name": "Literature in English"},
                    ],
                },
            )
        if path.endswith("/v1/q"):
            params = request.url.params
            key = (params["subject"], params["type"], int(params["year"]))
            if key in papers:
                return httpx.Response(
                    200, json={"status": 200, "data": {"id": 1, "question": "?"}}
                )
            return httpx.Response(
                404, json={"status": 404, "message": "No questions matched."}
            )
        return httpx.Response(500)

    provider = SdashProvider(
        transport=httpx.MockTransport(handler),
        base_url=SDASH_URL,
        access_token=token,
    )
    return provider, calls


def aloc(years_by_subject, *, subjects=None, calls=None):
    """An ALOC provider. ``years_by_subject``: {slug: {year: {exam: count}}}."""
    calls = calls if calls is not None else Calls()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.requests.append(request)
        path = request.url.path
        if path.endswith("/subjects"):
            return httpx.Response(
                200, json={"data": subjects if subjects is not None else []}
            )
        if path.endswith("/years"):
            slug = path.rsplit("/", 2)[-2]
            if slug not in years_by_subject:
                return httpx.Response(404, json={"message": "unknown"})
            return httpx.Response(
                200,
                json={
                    "data": {
                        "years": [
                            {"year": year, "breakdown": breakdown}
                            for year, breakdown in years_by_subject[slug].items()
                        ]
                    }
                },
            )
        return httpx.Response(500)

    provider = AlocProvider(
        transport=httpx.MockTransport(handler), base_url=ALOC_URL, api_key="key"
    )
    return provider, calls


def years_service(provider, store, redis=None, **kwargs):
    return CatalogueYearsService(
        FakeSession(), redis or FakeRedis(), "physics", "jamb", provider, **kwargs
    )


# ----- SDash adapter -------------------------------------------------


async def test_sdash_years_probes_each_reported_year_and_keeps_the_hits():
    provider, calls = sdash(
        {("physics", "utme", 2015), ("physics", "utme", 2012)},
        years=[2016, 2015, 2014, 2013, 2012],
    )

    found = await provider.catalogue_years("physics", "jamb")

    assert found == {2015: None, 2012: None}
    probes = calls.probes()
    # One /v1/years, then a one-question probe per reported year.
    assert calls.paths().count(f"{'/api'}/v1/years") == 1
    assert len(probes) == 5
    assert {p.url.params["limit"] for p in probes} == {"1"}
    assert {p.url.params["type"] for p in probes} == {"utme"}
    assert all(p.headers["AccessToken"] == "token" for p in probes)


async def test_sdash_years_use_the_providers_own_subject_and_exam_keys():
    provider, calls = sdash({("englishlit", "wassce", 2020)}, years=[2020])

    found = await provider.catalogue_years("literature-in-english", "waec")

    assert found == {2020: None}
    assert calls.probes()[0].url.params["subject"] == "englishlit"
    assert calls.probes()[0].url.params["type"] == "wassce"


async def test_sdash_years_none_found_is_an_answer_not_a_failure():
    provider, _calls = sdash(set(), years=[2015, 2014])

    assert await provider.catalogue_years("physics", "neco") == {}


async def test_sdash_years_untrustworthy_when_many_probes_fail():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/years"):
            return httpx.Response(
                200, json={"status": 200, "data": list(range(2010, 2020))}
            )
        year = int(request.url.params["year"])
        # 3 of 10 probes fail (30%), over the 20% limit.
        if year in {2010, 2011, 2012}:
            return httpx.Response(500, text="boom")
        return httpx.Response(
            200, json={"status": 200, "data": {"id": year, "question": "?"}}
        )

    provider = SdashProvider(
        transport=httpx.MockTransport(handler), base_url=SDASH_URL, access_token="t"
    )

    assert await provider.catalogue_years("physics", "jamb") is None


async def test_sdash_years_tolerates_a_few_failed_probes():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/years"):
            return httpx.Response(
                200, json={"status": 200, "data": list(range(2010, 2020))}
            )
        year = int(request.url.params["year"])
        if year == 2010:  # 1 of 10 (10%) is within the limit
            return httpx.Response(500, text="boom")
        return httpx.Response(
            200, json={"status": 200, "data": {"id": year, "question": "?"}}
        )

    provider = SdashProvider(
        transport=httpx.MockTransport(handler), base_url=SDASH_URL, access_token="t"
    )

    found = await provider.catalogue_years("physics", "jamb")

    assert found is not None
    assert set(found) == set(range(2011, 2020))


async def test_sdash_year_probes_never_exceed_the_concurrency_cap():
    active = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        if request.url.path.endswith("/v1/years"):
            return httpx.Response(
                200, json={"status": 200, "data": list(range(1990, 2020))}
            )
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.005)
        active -= 1
        return httpx.Response(
            200, json={"status": 404, "message": "No questions matched."}
        )

    provider = SdashProvider(
        transport=httpx.MockTransport(handler), base_url=SDASH_URL, access_token="t"
    )

    assert await provider.catalogue_years("physics", "jamb") == {}
    assert 1 < peak <= PROBE_CONCURRENCY


async def test_sdash_years_do_not_reprobe_confirmed_years():
    provider, calls = sdash(
        {("physics", "utme", 2015), ("physics", "utme", 2014)},
        years=[2015, 2014, 2013],
    )

    found = await provider.catalogue_years("physics", "jamb", known={2015})

    assert found == {2015: None, 2014: None}
    assert sorted(int(p.url.params["year"]) for p in calls.probes()) == [2013, 2014]


async def test_sdash_years_stop_when_the_quota_is_exhausted():
    calls = Calls()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.requests.append(request)
        if request.url.path.endswith("/v1/years"):
            return httpx.Response(
                200, json={"status": 200, "data": list(range(1980, 2020))}
            )
        return httpx.Response(429, json={"status": 429, "message": "Quota"})

    provider = SdashProvider(
        transport=httpx.MockTransport(handler), base_url=SDASH_URL, access_token="t"
    )

    assert await provider.catalogue_years("physics", "jamb") is None
    # Probing stops after the first rejections instead of burning all 40 calls.
    assert len(calls.probes()) <= PROBE_CONCURRENCY


async def test_sdash_years_keep_progress_when_the_caller_gives_up():
    provider, _calls = sdash({("physics", "utme", 2015)}, years=[2015, 2014])
    progress: dict[int, int | None] = {}

    await provider.catalogue_years("physics", "jamb", progress=progress)

    assert progress == {2015: None}


async def test_sdash_without_a_token_knows_nothing():
    provider, calls = sdash(set(), token="")

    assert await provider.catalogue_years("physics", "jamb") is None
    assert await provider.catalogue_subjects("jamb") is None
    assert calls.requests == []


async def test_sdash_subjects_cover_our_slugs_and_are_listed_once_a_day():
    provider, calls = sdash(set())

    names = await provider.catalogue_subjects("jamb")
    again = await provider.catalogue_subjects("waec")

    assert names is not None
    assert {"physics", "englishlit", "literature-in-english"} <= names
    assert again == names
    assert calls.paths().count("/api/v1/subjects") == 1


# ----- ALOC adapter (behaviour unchanged) ----------------------------


async def test_aloc_years_carry_counts_for_the_exam_only():
    provider, _calls = aloc(
        {
            "physics": {
                2015: {"jamb": 40, "waec": 50},
                2014: {"waec": 50},
                2013: {"jamb": 0},
            }
        }
    )

    assert await provider.catalogue_years("physics", "jamb") == {2015: 40}
    assert await provider.catalogue_years("physics", "waec") == {2015: 50, 2014: 50}


async def test_aloc_unknown_subject_has_no_years():
    provider, _calls = aloc({})

    assert await provider.catalogue_years("physics", "jamb") == {}


async def test_aloc_subjects_are_narrowed_by_exam():
    provider, _calls = aloc(
        {},
        subjects=[
            {"slug": "physics", "name": "Physics", "examTypes": ["jamb", "waec"]},
            {"slug": "chemistry", "name": "Chemistry", "examTypes": ["jamb"]},
        ],
    )

    jamb = await provider.catalogue_subjects("jamb")
    waec = await provider.catalogue_subjects("waec")

    assert jamb is not None and {"physics", "chemistry"} <= jamb
    assert waec is not None and "physics" in waec and "chemistry" not in waec


# ----- neutral years endpoint ----------------------------------------


async def test_years_merge_bank_catalogue_and_provider_newest_first(store):
    store.bank = [(2010, 40)]
    store.rows = [SimpleNamespace(exam_year=2012)]
    provider, _calls = sdash(
        {("physics", "utme", 2015), ("physics", "utme", 2012)},
        years=[2015, 2012],
    )

    result = await years_service(provider, store).process()

    assert result["provider"] == "SDASH"
    assert result["complete"] is True
    assert result["data"] == [
        {"year": 2015, "questionCount": None, "cached": False},
        {"year": 2012, "questionCount": None, "cached": False},
        {"year": 2010, "questionCount": 40, "cached": True},
    ]
    CatalogueYearsOut.model_validate(result)


async def test_a_second_open_is_answered_from_the_marker_without_http(store):
    redis = FakeRedis()
    provider, calls = sdash({("physics", "utme", 2015)}, years=[2015, 2014])

    first = await years_service(provider, store, redis).process()
    made = len(calls.requests)
    second = await years_service(provider, store, redis).process()

    assert made == 3  # /v1/years plus one probe per year
    assert len(calls.requests) == made
    assert second["data"] == first["data"]
    assert second["complete"] is True
    marker = next(key for key in redis.values if key.endswith(":done"))
    assert redis.ttls[marker] == 30 * 24 * 3600
    # The lock is released, never left to expire.
    assert not any(key.endswith(":lock") for key in redis.values)


async def test_discovered_years_are_stored_and_the_upsert_is_idempotent(store):
    provider, _calls = sdash(
        {("physics", "utme", 2015), ("physics", "utme", 2014)}, years=[2015, 2014]
    )

    await years_service(provider, store).process()
    assert sorted(row.exam_year for row in store.rows) == [2014, 2015]
    assert store.upserts == [{2015, 2014}]

    # A second discovery (marker lost) finds the same years: no duplicates,
    # and nothing already confirmed is paid for or written again.
    provider, calls = sdash(
        {("physics", "utme", 2015), ("physics", "utme", 2014)}, years=[2015, 2014]
    )
    clear_reference_cache()
    await years_service(provider, store).process()

    assert sorted(row.exam_year for row in store.rows) == [2014, 2015]
    assert calls.probes() == []
    assert len(store.upserts) == 1


async def test_concurrent_opens_share_one_discovery(store):
    redis = FakeRedis()
    provider, calls = sdash({("physics", "utme", 2015)}, years=[2015, 2014, 2013])

    first, second = await asyncio.gather(
        years_service(provider, store, redis).process(),
        years_service(provider, store, redis).process(),
    )

    assert len(calls.probes()) == 3  # one discovery, not two
    assert first["data"] == second["data"]
    assert second["complete"] is True


async def test_an_untrustworthy_discovery_is_not_remembered(store):
    redis = FakeRedis()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="down")

    provider = SdashProvider(
        transport=httpx.MockTransport(handler), base_url=SDASH_URL, access_token="t"
    )

    result = await years_service(provider, store, redis).process()

    assert result["complete"] is False
    assert result["data"] == []
    assert not any(key.endswith(":done") for key in redis.values)


class SlowProvider:
    name = "SDASH"
    configured = True
    native_discovery = False

    async def catalogue_years(self, subject_slug, exam, *, known=(), progress=None):
        progress[2015] = None
        await asyncio.sleep(5)
        return {2015: None, 2014: None}


async def test_a_slow_discovery_returns_what_is_known_as_incomplete(store):
    redis = FakeRedis()

    result = await years_service(
        SlowProvider(), store, redis, budget_seconds=0.05
    ).process()

    assert result["complete"] is False
    assert [paper["year"] for paper in result["data"]] == [2015]
    assert not any(key.endswith(":done") for key in redis.values)
    # What was found is kept for the next student.
    assert [row.exam_year for row in store.rows] == [2015]


async def test_an_unconfigured_provider_still_lists_the_bank(store):
    store.bank = [(2011, 30)]
    provider, calls = sdash(set(), token="")

    result = await years_service(provider, store).process()

    assert result["complete"] is False
    assert result["data"] == [{"year": 2011, "questionCount": 30, "cached": True}]
    assert calls.requests == []


async def test_an_unknown_subject_is_a_404(store):
    provider, _calls = sdash(set())

    with pytest.raises(ApiError) as caught:
        await CatalogueYearsService(
            FakeSession(), FakeRedis(), "no-such-subject", "jamb", provider
        ).process()

    assert caught.value.status_code == 404


async def test_a_board_with_no_past_papers_is_a_400(store):
    provider, _calls = sdash(set())

    with pytest.raises(ApiError) as caught:
        CatalogueYearsService(FakeSession(), FakeRedis(), "physics", "gce", provider)

    assert caught.value.status_code == 400


# ----- neutral subjects endpoint -------------------------------------


async def test_subjects_are_ours_flagged_for_the_exam_and_held_by_the_provider(store):
    provider, _calls = sdash(set())

    result = await CatalogueSubjectsService(FakeSession(), "jamb", provider).process()

    # SDash lists physics and englishlit: chemistry is not held, so it drops.
    assert [s["slug"] for s in result["data"]] == ["physics", "literature-in-english"]
    assert result["data"][0] == {
        "slug": "physics",
        "name": "Physics",
        "displayName": "Physics",
        "category": "SCIENCE",
        "examTypes": ["jamb", "waec"],
    }
    CatalogueSubjectsOut.model_validate(result)


async def test_subjects_only_include_those_flagged_for_the_exam(store):
    provider, _calls = sdash(set())

    result = await CatalogueSubjectsService(FakeSession(), "neco", provider).process()

    assert [s["slug"] for s in result["data"]] == ["literature-in-english"]


async def test_a_provider_outage_never_empties_the_subject_picker(store):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="down")

    provider = SdashProvider(
        transport=httpx.MockTransport(handler), base_url=SDASH_URL, access_token="t"
    )

    result = await CatalogueSubjectsService(FakeSession(), "jamb", provider).process()

    assert [s["slug"] for s in result["data"]] == [
        "physics",
        "chemistry",
        "literature-in-english",
    ]


# ----- ALOC / SDash parity -------------------------------------------


def _both_providers():
    """Equivalent data behind each provider: physics papers in 2015 and 2012."""
    sd, _ = sdash(
        {("physics", "utme", 2015), ("physics", "utme", 2012)},
        years=[2016, 2015, 2014, 2012],
    )
    al, _ = aloc(
        {
            "physics": {
                2015: {"jamb": 40},
                2012: {"jamb": 40},
                2011: {"waec": 40},
            }
        },
        subjects=[
            {"slug": "physics", "name": "Physics", "examTypes": ["jamb", "waec"]},
            {
                "slug": "literature-in-english",
                "name": "Literature in English",
                "examTypes": ["jamb"],
            },
        ],
    )
    return {"SDASH": sd, "ALOC": al}


@pytest.mark.parametrize("name", ["SDASH", "ALOC"])
async def test_parity_years_endpoint_has_one_shape_under_either_provider(store, name):
    provider = _both_providers()[name]

    result = await years_service(provider, store).process()

    validated = CatalogueYearsOut.model_validate(result)
    assert validated.provider == name
    assert validated.exam == "jamb"
    assert validated.subject.slug == "physics"
    assert validated.complete is True
    assert [paper.year for paper in validated.data] == [2015, 2012]
    for paper in validated.data:
        assert paper.questionCount is None or isinstance(paper.questionCount, int)
        assert paper.cached is False
    assert set(result) == {"provider", "subject", "exam", "complete", "data"}


@pytest.mark.parametrize("name", ["SDASH", "ALOC"])
async def test_parity_subjects_endpoint_has_one_shape_per_provider(store, name):
    provider = _both_providers()[name]

    result = await CatalogueSubjectsService(FakeSession(), "jamb", provider).process()

    validated = CatalogueSubjectsOut.model_validate(result)
    assert validated.provider == name
    assert [s.slug for s in validated.data] == ["physics", "literature-in-english"]


@pytest.mark.parametrize("name", ["SDASH", "ALOC"])
async def test_parity_a_bank_paper_is_listed_whatever_the_provider(store, name):
    store.bank = [(2015, 40)]
    provider = _both_providers()[name]

    result = await years_service(provider, store).process()

    top = result["data"][0]
    assert (top["year"], top["cached"], top["questionCount"]) == (2015, True, 40)


def test_parity_circuit_transitions():
    sd, al = _both_providers()["SDASH"], _both_providers()["ALOC"]

    # A bad token blocks either provider.
    assert sd.next_circuit(401, "", "CLOSED") == "BLOCKED"
    assert al.next_circuit(401, "", "CLOSED") == "BLOCKED"
    # A missing paper leaves the circuit alone.
    assert sd.next_circuit(404, "", "CLOSED") == "CLOSED"
    assert al.next_circuit(404, "", "CLOSED") == "CLOSED"
    # Each provider's own quota signal exhausts it.
    assert sd.next_circuit(429, "", "CLOSED") == "EXHAUSTED"
    assert al.next_circuit(402, "", "CLOSED") == "EXHAUSTED"
    # A plan lacking a v2+ feature must not shut off the v1 calls.
    assert sd.next_circuit(403, "Not available in your plan", "CLOSED") == "CLOSED"
    assert sd.next_circuit(403, "No active plan", "CLOSED") == "BLOCKED"


def test_parity_capability_flags():
    providers = _both_providers()

    assert providers["ALOC"].supports_topic_filter is True
    assert providers["SDASH"].supports_topic_filter is False
    assert providers["ALOC"].native_discovery is True
    assert providers["SDASH"].native_discovery is False
    assert providers["ALOC"].paper_fetch_mode == PaperFetchMode.COMPLETE
    assert providers["SDASH"].paper_fetch_mode == PaperFetchMode.SAMPLED


def test_parity_normalized_fields_agree_on_equivalent_items():
    sd = SdashProvider(access_token="t")
    al = AlocProvider(api_key="k")
    passage = ("A long reading passage. " * 20).strip()

    sdash_item = sd.normalize(
        {
            "id": 7,
            "question": "What?",
            "option": {"a": "1", "b": "2", "c": "3", "d": "4"},
            "answer": "b",
            "examtype": "UTME",
            "examyear": "2015",
            "section": passage,
        }
    )
    aloc_item = al.normalize(
        {
            "id": "7",
            "text": "What?",
            "options": {"A": "1", "B": "2", "C": "3", "D": "4"},
            "correctAnswer": "b",
            "examType": "jamb",
            "year": 2015,
            "questionNumber": 3,
            "hasPassage": True,
            "section": passage,
        }
    )

    for item in (sdash_item, aloc_item):
        assert item.provider_id == "7"
        assert item.text == "What?"
        assert item.exam_year == 2015
        assert item.passage == passage
        assert item.instruction is None
        assert item.options == {"A": "1", "B": "2", "C": "3", "D": "4"}


def test_sdash_short_section_is_an_instruction_not_a_passage():
    item = SdashProvider(access_token="t").normalize(
        {"id": 1, "question": "Q", "section": "Choose the word nearest in meaning."}
    )

    assert item.passage is None
    assert item.instruction == "Choose the word nearest in meaning."
    assert item.exam_type is None and item.exam_year is None


# ----- explanations ---------------------------------------------------


async def test_sdash_explanation_comes_from_the_v1_solution():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"status": 200, "data": {"id": 9, "solution": "Because 2 + 2 = 4."}},
        )

    provider = SdashProvider(
        transport=httpx.MockTransport(handler), base_url=SDASH_URL, access_token="t"
    )

    payload = await provider.explain("9")

    assert payload is not None
    assert provider.flatten_explanation(payload) == "Because 2 + 2 = 4."
    assert [r.url.path for r in seen] == ["/api/v1/q"]
    assert seen[0].url.params["id"] == "9"


async def test_sdash_explanation_falls_back_to_v3_and_matches_alocs_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/q"):
            return httpx.Response(200, json={"status": 200, "data": {"id": 9}})
        return httpx.Response(
            200,
            json={
                "status": 200,
                "data": {
                    "id": 9,
                    "explanation": {
                        "detailed": "Add the numbers.",
                        "steps": ["2 + 2", "= 4"],
                        "why_others_wrong": {"a": "Too small."},
                    },
                },
            },
        )

    provider = SdashProvider(
        transport=httpx.MockTransport(handler), base_url=SDASH_URL, access_token="t"
    )

    payload = await provider.explain("9")

    assert payload is not None
    text = provider.flatten_explanation(payload)
    assert "Add the numbers." in text
    assert "### Steps" in text
    assert "- **Option A**: Too small." in text


async def test_sdash_explanation_is_none_when_nothing_is_available():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"status": 403, "message": "Not available in your plan"}
        )

    provider = SdashProvider(
        transport=httpx.MockTransport(handler), base_url=SDASH_URL, access_token="t"
    )

    assert await provider.explain("9") is None


# ----- sampled paper snapshot -----------------------------------------


async def test_sdash_paper_snapshot_merges_parallel_draws_sorted_and_deduplicated():
    draws = [
        [{"id": 30, "question": "c"}, {"id": 2, "question": "b"}],
        [{"id": 2, "question": "b"}, {"id": 11, "question": "d"}],
        [{"id": 5, "question": "e"}],
    ]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": 200, "data": draws[len(seen) - 1]})

    provider = SdashProvider(
        transport=httpx.MockTransport(handler), base_url=SDASH_URL, access_token="t"
    )

    result = await fetch_provider_paper(
        provider, subject_slug="physics", exam_type="JAMB", exam_year=2015
    )

    assert len(seen) == SAMPLE_DRAWS
    assert {r.url.params["limit"] for r in seen} == {"50"}
    assert [item["id"] for item in result.items] == [2, 5, 11, 30]
    # A random sample is never a complete paper.
    assert result.complete is False
    assert result.cacheable is True


async def test_sdash_paper_snapshot_treats_a_404_as_a_paper_that_does_not_exist():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"status": 404, "message": "No questions matched."}
        )

    provider = SdashProvider(
        transport=httpx.MockTransport(handler), base_url=SDASH_URL, access_token="t"
    )

    result = await fetch_provider_paper(
        provider, subject_slug="physics", exam_type="JAMB", exam_year=1980
    )

    assert result.items == []
    assert result.status_code == 404
    assert result.complete is True
