from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

import httpx

from app.database.models import ProviderFetch


class DiscoveryResource(StrEnum):
    COVERAGE = "coverage"
    SUBJECTS = "subjects"
    SUBJECT = "subject"
    SUBJECT_TOPICS = "subject_topics"
    SUBJECT_YEARS = "subject_years"
    YEAR_SUBJECTS = "year_subjects"


class ProviderNotFound(Exception):
    pass


@dataclass
class NormalizedQuestion:
    provider_id: str
    text: str
    options: dict[str, str]
    answer: str
    explanation: str
    image_url: str | None
    raw: dict


@dataclass
class DrawResult:
    items: list[dict] = field(default_factory=list)
    status_code: int = 200
    body: str = ""
    credits_remaining: int | None = None
    exhausted: bool = False

    @property
    def failed(self) -> bool:
        return self.status_code >= 400


@dataclass
class SearchPage:
    items: list[dict] = field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    status_code: int = 200
    body: str = ""

    @property
    def failed(self) -> bool:
        return self.status_code >= 400


class QuestionProvider(ABC):
    name: str

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    def client(self, timeout: float = 20) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.transport)

    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @property
    def fetch_explanations(self) -> bool:
        return False

    @abstractmethod
    async def draw(
        self, subject_slug: str, exam_type: str, exam_year: int, limit: int
    ) -> DrawResult | None:
        """Return ``None`` when the provider could not be reached at all."""

    @abstractmethod
    def normalize(self, item: dict) -> NormalizedQuestion: ...

    @abstractmethod
    def should_saturate(
        self, row: ProviderFetch, result: DrawResult, new_count: int
    ) -> bool: ...

    async def search(
        self,
        *,
        subject_slug: str | None,
        exam_type: str | None,
        exam_year: int | None,
        limit: int,
        cursor: str | None = None,
    ) -> SearchPage | None:
        """Fetch one page of questions. ``None`` when unsupported or unreachable."""
        return None

    async def explain(
        self, provider_question_id: str, depth: str = "step_by_step"
    ) -> dict | None:
        return None

    def flatten_explanation(self, payload: dict) -> str:
        return ""

    async def discover(
        self, resource: DiscoveryResource, key: str | int | None = None
    ) -> dict | list | None:
        """Return ``None`` when unsupported or unreachable.

        Raises ``ProviderNotFound`` when the provider says ``key`` does not exist.
        """
        return None
