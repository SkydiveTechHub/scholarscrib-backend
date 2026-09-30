import json

import httpx
import pytest

from app.core.config import Settings, settings
from app.database.models import ProviderFetch
from app.services.provider import ProviderFactory
from app.services.provider.aloc import (
    ALOC_MAX_PAGES,
    ALOC_PAGE_LIMIT,
    AlocProvider,
    flatten_explanation,
)
from app.services.provider.base import DrawResult
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
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token"
        assert request.url.params["exam"] == "jamb"
        return httpx.Response(200, json={"data": [{"id": 1}, {"id": 2}]})

    provider = SdashProvider(
        transport=httpx.MockTransport(handler), access_token="token"
    )
    result = await provider.draw("mathematics", "JAMB", 2015, 50)

    assert result is not None
    assert [item["id"] for item in result.items] == [1, 2]


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
