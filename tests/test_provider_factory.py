import json

import httpx
import pytest

from app.core.config import Settings, settings
from app.core.errors import ApiError
from app.database.models import ProviderFetch
from app.services.provider import (
    DiscoveryResource,
    ProviderCoverageService,
    ProviderDiscoveryService,
    ProviderFactory,
)
from app.services.provider import service as provider_service
from app.services.provider.aloc import (
    ALOC_MAX_PAGES,
    ALOC_PAGE_LIMIT,
    AlocProvider,
    flatten_explanation,
)
from app.services.provider.base import DrawResult, ProviderNotFound
from app.services.provider.sdash import SdashProvider


def _aloc_question(index: int) -> dict:
    return {
        "id": f"q-{index}",
        "text": f"Question {index}",
        "options": {"A": "1", "B": "2", "C": "3", "D": "4"},
        "correctAnswer": "B",
        "imageUrl": None,
    }


def _aloc_page(start: int, count: int, next_cursor: str | None, has_more: bool) -> dict:
    return {
        "data": [_aloc_question(start + offset) for offset in range(count)],
        "pagination": {
            "nextCursor": next_cursor,
            "prevCursor": None,
            "hasMore": has_more,
        },
        "meta": {"creditsUsed": 1, "creditsRemaining": 900 - start, "tier": "free"},
    }


def test_factory_defaults_to_aloc():
    assert Settings.model_fields["question_provider"].default == "ALOC"


def test_factory_follows_configured_provider(monkeypatch):
    monkeypatch.setattr(settings, "question_provider", "ALOC")
    assert isinstance(ProviderFactory.create(), AlocProvider)
    monkeypatch.setattr(settings, "question_provider", "SDASH")
    assert isinstance(ProviderFactory.create(), SdashProvider)


def test_factory_resolves_both_names():
    assert isinstance(ProviderFactory.create("SDASH"), SdashProvider)
    assert isinstance(ProviderFactory.create("aloc"), AlocProvider)


def test_factory_rejects_unknown_names():
    with pytest.raises(ValueError):
        ProviderFactory.create("nope")


async def test_aloc_walks_cursor_until_has_more_is_false():
    requests: list[httpx.Request] = []
    pages = {
        None: _aloc_page(0, 15, "c1", True),
        "c1": _aloc_page(15, 15, "c2", True),
        "c2": _aloc_page(30, 5, None, False),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=pages[request.url.params.get("cursor")])

    provider = AlocProvider(
        transport=httpx.MockTransport(handler), api_key="key", fetch_explanations=False
    )
    result = await provider.draw("mathematics", "JAMB", 2015, 50)

    assert result is not None
    assert len(result.items) == 35
    assert result.exhausted is True
    assert result.complete is True
    assert result.credits_remaining == 870
    assert len(requests) == 3
    first = requests[0]
    assert first.headers["X-API-Key"] == "key"
    assert first.url.params["limit"] == str(ALOC_PAGE_LIMIT)
    assert first.url.params["examType"] == "jamb"
    assert all("/explain" not in str(request.url) for request in requests)


async def test_aloc_respects_page_cap_when_has_more_never_ends():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_aloc_page(calls * 15, 15, f"c{calls}", True))

    provider = AlocProvider(transport=httpx.MockTransport(handler), api_key="key")
    result = await provider.draw("physics", "WAEC", 2020, 50)

    assert result is not None
    assert calls == ALOC_MAX_PAGES
    assert result.exhausted is True
    assert result.complete is False


async def test_aloc_error_is_reported_with_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text='{"error":"Insufficient credit"}')

    provider = AlocProvider(transport=httpx.MockTransport(handler), api_key="key")
    result = await provider.draw("physics", "WAEC", 2020, 50)

    assert result is not None
    assert result.failed
    assert result.status_code == 403
    assert "credit" in result.body.lower()
    assert result.exhausted is False


async def test_aloc_explain_posts_and_returns_payload():
    seen: list[httpx.Request] = []
    payload = {"data": {"explanation": "Because.", "steps": [], "commonMistakes": []}}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    provider = AlocProvider(transport=httpx.MockTransport(handler), api_key="key")
    result = await provider.explain("q-1")

    assert result == payload
    assert seen[0].method == "POST"
    assert seen[0].url.path.endswith("/questions/q-1/explain")
    assert json.loads(seen[0].content) == {"depth": "step_by_step"}


def test_aloc_saturates_only_when_exhausted():
    provider = AlocProvider(api_key="key")
    row = ProviderFetch(draw_count=1)
    assert provider.should_saturate(row, DrawResult(exhausted=True), 0) is True
    assert provider.should_saturate(row, DrawResult(exhausted=False), 15) is False


def test_aloc_normalize():
    normalized = AlocProvider(api_key="key").normalize(_aloc_question(3))
    assert normalized.provider_id == "q-3"
    assert normalized.text == "Question 3"
    assert normalized.options == {"A": "1", "B": "2", "C": "3", "D": "4"}
    assert normalized.answer == "B"
    assert normalized.explanation == ""


def test_sdash_normalize_keeps_field_fallbacks():
    provider = SdashProvider(access_token="token")
    normalized = provider.normalize(
        {
            "questionId": 42,
            "questionText": "What?",
            "options": ["w", "x", "y", "z"],
            "correctAnswer": "c",
            "solution": "Because y.",
        }
    )
    assert normalized.provider_id == "42"
    assert normalized.text == "What?"
    assert normalized.options == {"A": "w", "B": "x", "C": "y", "D": "z"}
    assert normalized.answer == "c"
    assert normalized.explanation == "Because y."


async def test_sdash_draw_reads_data_envelope():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": 200,
                "data": [
                    {
                        "id": 1,
                        "question": "What?",
                        "option": {"a": "One", "b": "Two", "c": "Three", "d": "Four"},
                        "answer": "b",
                        "solution": "Because.",
                    }
                ],
            },
        )

    provider = SdashProvider(
        transport=httpx.MockTransport(handler),
        base_url="https://www.sdashapi.com/api",
        access_token="token",
    )
    result = await provider.draw("mathematics", "JAMB", 2015, 50)

    assert result is not None
    assert result.items[0]["id"] == 1
    assert seen[0].url.path == "/api/v1/q"
    assert seen[0].headers["AccessToken"] == "token"
    assert seen[0].url.params["subject"] == "mathematics"
    assert seen[0].url.params["type"] == "utme"
    assert seen[0].url.params["year"] == "2015"
    assert seen[0].url.params["limit"] == "50"
    normalized = provider.normalize(result.items[0])
    assert normalized.options == {
        "A": "One",
        "B": "Two",
        "C": "Three",
        "D": "Four",
    }
    assert normalized.answer == "b"
    assert normalized.explanation == "Because."
    assert provider.paper_fetch_mode.value == "sampled"


async def test_sdash_draw_accepts_documented_single_question_object():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["limit"] == "1"
        return httpx.Response(
            200,
            json={"status": 200, "data": {"id": 4, "question": "One question"}},
        )

    provider = SdashProvider(
        transport=httpx.MockTransport(handler), access_token="token"
    )
    result = await provider.draw("biology", "WAEC", 2020, 1)

    assert result is not None
    assert result.items == [{"id": 4, "question": "One question"}]


async def test_sdash_maps_api_level_not_found_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": 404, "message": "No questions matched your filters."},
        )

    provider = SdashProvider(
        transport=httpx.MockTransport(handler), access_token="token"
    )
    page = await provider.search(
        subject_slug="biology",
        exam_type="JAMB",
        exam_year=1988,
        limit=50,
    )

    assert page is not None
    assert page.status_code == 404
    assert "No questions" in page.body


def test_flatten_explanation_to_markdown():
    markdown = flatten_explanation(
        {
            "data": {
                "explanation": "Add the numbers.",
                "steps": ["Take 1", "Add 1"],
                "commonMistakes": [
                    {"mistake": "Picking A", "whyWrong": "That subtracts."},
                    {"mistake": "Picking C"},
                ],
            }
        }
    )
    assert markdown == (
        "Add the numbers.\n\n"
        "### Steps\n1. Take 1\n2. Add 1\n\n"
        "### Common mistakes\n- **Picking A**: That subtracts.\n- **Picking C**"
    )


def test_flatten_explanation_handles_provider_envelope():
    markdown = flatten_explanation(
        {
            "data": {
                "questionId": "f47ac10b",
                "explanation": "\n".join(["Step 1: 2x + 5 = 15", "Step 2: 2x = 10"]),
                "simplifiedExplanation": "Undo the operations in reverse.",
                "commonMistakes": [
                    {"mistake": "Dividing first", "whyWrong": "Subtract first."}
                ],
            },
            "meta": {"creditsUsed": 10, "tier": "growth", "requestId": "req_1"},
        }
    )
    assert markdown == (
        "Step 1: 2x + 5 = 15\n\n"
        "Step 2: 2x = 10\n\n"
        "### Common mistakes\n- **Dividing first**: Subtract first."
    )


_COVERAGE = {
    "data": {
        "summary": {"totalQuestions": 12645, "minYear": 1988, "maxYear": 2025},
        "examBodies": [
            {"slug": "jamb", "name": "JAMB / UTME", "subjects": [{"slug": "physics"}]},
            {"slug": "post_utme", "name": "Post-UTME", "subjects": []},
        ],
    }
}


async def test_aloc_coverage_maps_exam_types():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_COVERAGE)

    provider = AlocProvider(transport=httpx.MockTransport(handler), api_key="key")
    coverage = await provider.discover(DiscoveryResource.COVERAGE)

    assert isinstance(coverage, dict)
    assert seen[0].url.path.endswith("/coverage")
    assert coverage["summary"]["totalQuestions"] == 12645
    assert [body["examType"] for body in coverage["examBodies"]] == ["JAMB", None]
    assert coverage["examBodies"][0]["subjects"] == [{"slug": "physics"}]


@pytest.mark.parametrize(
    ("resource", "key", "path"),
    [
        (DiscoveryResource.SUBJECTS, None, "/subjects"),
        (DiscoveryResource.SUBJECT, "Mathematics", "/subjects/mathematics"),
        (DiscoveryResource.SUBJECT, "english", "/subjects/english-language"),
        (DiscoveryResource.SUBJECT_TOPICS, "physics", "/subjects/physics/topics"),
        (DiscoveryResource.SUBJECT_YEARS, "MTH", "/subjects/mth/years"),
        (DiscoveryResource.YEAR_SUBJECTS, 2019, "/subjects/years/2019"),
    ],
)
async def test_aloc_discovery_paths(resource, key, path):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"name": "x"}], "meta": {}})

    provider = AlocProvider(transport=httpx.MockTransport(handler), api_key="key")
    assert await provider.discover(resource, key) == [{"name": "x"}]
    assert seen[0].url.path == f"/api/v1{path}"
    assert seen[0].headers["X-API-Key"] == "key"


async def test_aloc_discovery_unknown_subject_raises_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not_found"})

    provider = AlocProvider(transport=httpx.MockTransport(handler), api_key="key")
    with pytest.raises(ProviderNotFound):
        await provider.discover(DiscoveryResource.SUBJECT, "nope")


async def test_coverage_service_caches_and_serves_stale_on_failure():
    provider_service._DISCOVERY_CACHE.clear()
    responses = [httpx.Response(200, json=_COVERAGE), httpx.Response(500)]
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return responses[min(calls - 1, 1)]

    provider = AlocProvider(transport=httpx.MockTransport(handler), api_key="key")
    first = await ProviderCoverageService(provider).process()
    assert first["provider"] == "ALOC"
    assert "examBodies" in first
    assert await ProviderCoverageService(provider).process() == first
    assert calls == 1

    for cache_id, (_, value) in list(provider_service._DISCOVERY_CACHE.items()):
        provider_service._DISCOVERY_CACHE[cache_id] = (0.0, value)
    assert await ProviderCoverageService(provider).process() == first
    assert calls == 2
    provider_service._DISCOVERY_CACHE.clear()


async def test_discovery_service_wraps_data_and_maps_not_found():
    provider_service._DISCOVERY_CACHE.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/nope"):
            return httpx.Response(404, json={"error": "not_found"})
        return httpx.Response(200, json={"data": {"name": "mathematics"}})

    provider = AlocProvider(transport=httpx.MockTransport(handler), api_key="key")
    found = await ProviderDiscoveryService(
        DiscoveryResource.SUBJECT, "mathematics", provider
    ).process()
    assert found == {"provider": "ALOC", "data": {"name": "mathematics"}}

    with pytest.raises(ApiError) as caught:
        await ProviderDiscoveryService(
            DiscoveryResource.SUBJECT, "nope", provider
        ).process()
    assert caught.value.status_code == 404
    provider_service._DISCOVERY_CACHE.clear()


async def test_coverage_service_raises_when_unsupported():
    provider_service._DISCOVERY_CACHE.clear()
    with pytest.raises(ApiError) as caught:
        await ProviderCoverageService(SdashProvider(access_token="t")).process()
    assert caught.value.status_code == 503
