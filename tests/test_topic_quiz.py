from types import SimpleNamespace

import httpx
import pytest

from app.services.provider import service as provider_service
from app.services.provider.aloc import AlocProvider
from app.services.provider.base import SearchPage

SUBJECT = SimpleNamespace(id="subj-1", slug="physics", name="Physics")
GAS_LAWS = SimpleNamespace(id="topic-gas", slug="gas-laws", subject_id="subj-1")
SOUND = SimpleNamespace(id="topic-sound", slug="sound-waves", subject_id="subj-1")
OPTICS = SimpleNamespace(
    id="topic-optics", slug="optical-instruments", subject_id="subj-1"
)


def _item(index: int, metadata: dict | None = None) -> dict:
    item = {
        "id": f"q-{index}",
        "text": f"Question {index}",
        "options": {"A": "1", "B": "2", "C": "3", "D": "4"},
        "correctAnswer": "b",
        "examType": "jamb",
        "year": 2010,
    }
    if metadata is not None:
        item["metadata"] = metadata
    return item


class FakeProvider:
    name = "ALOC"
    configured = True

    def __init__(self, pages: dict[str, list[dict]]):
        self.pages = pages
        self.calls: list[dict] = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        return SearchPage(items=self.pages.get(kwargs["exam_type"], []))

    def normalize(self, item):
        return AlocProvider(api_key="key").normalize(item)


@pytest.fixture
def repos(monkeypatch):
    async def by_id_or_slug(_session, key):
        return SUBJECT if key in {"physics", "subj-1"} else None

    async def topic_by_slug(_session, _subject_id, slug):
        topics = (GAS_LAWS, SOUND, OPTICS)
        return {topic.slug: topic for topic in topics}.get(slug)

    async def topic_by_id(_session, _id):
        return None

    async def for_subject(_session, _subject_id):
        return [GAS_LAWS, SOUND, OPTICS]

    stored: list = []

    async def pick_objective(_session, **_kwargs):
        return list(stored)

    monkeypatch.setattr(
        provider_service.subjects_repository, "by_id_or_slug", by_id_or_slug
    )
    monkeypatch.setattr(provider_service.topics_repository, "by_slug", topic_by_slug)
    monkeypatch.setattr(provider_service.topics_repository, "by_id", topic_by_id)
    monkeypatch.setattr(provider_service.topics_repository, "for_subject", for_subject)
    monkeypatch.setattr(
        provider_service.questions_repository, "pick_objective", pick_objective
    )
    return stored


def _service(provider, **overrides):
    options = dict(
        subject_key="physics",
        topic_key="gas-laws",
        exam_type="WAEC",
        limit=10,
        random=True,
    )
    options.update(overrides)
    return provider_service.TopicQuizQuestionsService(
        None, provider=provider, **options
    )


async def test_falls_back_to_jamb_when_waec_has_nothing(repos):
    provider = FakeProvider({"JAMB": [_item(1), _item(2)]})

    result = await _service(provider).process()

    assert result["requestedExamType"] == "WAEC"
    assert result["examType"] == "JAMB"
    assert [q["questionText"] for q in result["questions"]] == [
        "Question 1",
        "Question 2",
    ]
    assert all(q["correctAnswer"] == "B" for q in result["questions"])
    assert all(q["topicId"] == "topic-gas" for q in result["questions"])
    # Asked under the reviewed ALOC topic, randomly, for each exam in turn.
    first = provider.calls[0]
    assert first["exam_type"] == "WAEC"
    assert (first["topic"], first["subtopic"], first["random"]) == (
        "heat",
        "gas-laws-thermal",
        True,
    )
    assert {call["exam_type"] for call in provider.calls} == {"WAEC", "JAMB"}


async def test_drops_items_classified_under_another_topic(repos):
    sound = {"topic": "waves", "subtopic": "sound", "classificationConfidence": 0.9}
    gas = {
        "topic": "heat",
        "subtopic": "gas-laws-thermal",
        "classificationConfidence": 0.9,
    }
    provider = FakeProvider({"WAEC": [_item(1, sound), _item(2, gas), _item(2, gas)]})

    result = await _service(provider).process()

    assert result["examType"] == "WAEC"
    # The sound question is dropped, and the duplicate is kept once.
    assert [q["questionText"] for q in result["questions"]] == ["Question 2"]


async def test_bank_questions_come_first_and_cap_the_quiz(repos):
    repos.extend(
        SimpleNamespace(
            id=f"bank-{i}",
            subject_id="subj-1",
            topic_id="topic-gas",
            exam_type="WAEC",
            exam_year=2015,
            question_number=i,
            question_text=f"Bank {i}",
            question_image_url=None,
            question_type="OBJECTIVE",
            options={"A": "1", "B": "2"},
            correct_answer="A",
            explanation=None,
            explanation_image_url=None,
            difficulty="MEDIUM",
            marks=1,
            time_estimate_seconds=60,
        )
        for i in range(3)
    )
    provider = FakeProvider({"WAEC": [_item(1)]})

    result = await _service(provider, limit=3).process()

    assert [q["questionText"] for q in result["questions"]] == [
        "Bank 0",
        "Bank 1",
        "Bank 2",
    ]
    assert provider.calls == []


async def test_topic_without_a_provider_match_never_asks_the_provider(repos):
    provider = FakeProvider({"WAEC": [_item(1)], "JAMB": [_item(2)]})

    result = await _service(provider, topic_key="optical-instruments").process()

    assert result == {"questions": [], "examType": None, "requestedExamType": "WAEC"}
    assert provider.calls == []


async def test_aloc_search_sends_topic_and_random():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [], "pagination": {}})

    provider = AlocProvider(transport=httpx.MockTransport(handler), api_key="key")
    await provider.search(
        subject_slug="physics",
        exam_type="WAEC",
        exam_year=None,
        limit=10,
        topic="heat",
        subtopic="gas-laws-thermal",
        random=True,
    )

    params = seen[0].url.params
    assert params["examType"] == "waec"
    assert params["topic"] == "heat"
    assert params["subtopic"] == "gas-laws-thermal"
    assert params["random"] == "true"
    assert params["limit"] == "10"
