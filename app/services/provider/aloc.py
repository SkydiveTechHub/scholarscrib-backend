from collections.abc import Collection
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.database.models import ProviderFetch
from app.services.provider.base import (
    PROVIDER_PAPER_MAX_PAGES,
    PROVIDER_PAPER_PAGE_LIMIT,
    DiscoveryResource,
    DrawResult,
    NormalizedQuestion,
    ProviderNotFound,
    QuestionProvider,
    SearchPage,
)

ALOC_PAGE_LIMIT = PROVIDER_PAPER_PAGE_LIMIT
ALOC_MAX_PAGES = PROVIDER_PAPER_MAX_PAGES
ALOC_SEARCH_LIMIT = 50

EXAM_ALIASES = {"JAMB": "jamb", "WAEC": "waec", "NECO": "neco"}
SUBJECT_ALIASES = {
    "english": "english-language",
    "financial-accounting": "accounting",
    "crs": "christian-religious-studies",
    "literature": "literature-in-english",
}


def flatten_explanation(payload: dict) -> str:
    nested = payload.get("data")
    data: dict = nested if isinstance(nested, dict) else payload
    parts: list[str] = []
    # The provider separates "Step 1: ..." lines with a single newline, which
    # markdown folds into one run-on paragraph; one paragraph per line instead.
    prose = "\n\n".join(
        line.strip()
        for line in str(data.get("explanation") or "").splitlines()
        if line.strip()
    )
    if prose:
        parts.append(prose)
    steps = [str(step).strip() for step in data.get("steps") or [] if str(step).strip()]
    if steps:
        parts.append(
            "### Steps\n"
            + "\n".join(f"{index}. {step}" for index, step in enumerate(steps, 1))
        )
    mistakes = []
    for entry in data.get("commonMistakes") or []:
        if not isinstance(entry, dict):
            continue
        mistake = str(entry.get("mistake") or "").strip()
        why = str(entry.get("whyWrong") or "").strip()
        if mistake:
            mistakes.append(f"- **{mistake}**" + (f": {why}" if why else ""))
    if mistakes:
        parts.append("### Common mistakes\n" + "\n".join(mistakes))
    return "\n\n".join(parts)


class AlocProvider(QuestionProvider):
    name = "ALOC"

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        fetch_explanations: bool | None = None,
    ) -> None:
        super().__init__(transport)
        self.base_url = (base_url or settings.aloc_base_url).rstrip("/")
        self.api_key = api_key or settings.aloc_api_key
        self._fetch_explanations = (
            settings.aloc_fetch_explanations
            if fetch_explanations is None
            else fetch_explanations
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def fetch_explanations(self) -> bool:
        return self._fetch_explanations

    @property
    def supports_topic_filter(self) -> bool:
        return True

    @property
    def native_discovery(self) -> bool:
        return True

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key or ""}

    async def catalogue_subjects(self, exam: str | None = None) -> set[str] | None:
        """Every name ALOC knows a subject by, for subjects held for ``exam``."""
        from app.services.provider.mapping import slugify

        rows = await self.discover(DiscoveryResource.SUBJECTS)
        if not isinstance(rows, list):
            return None
        names: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            exam_types = row.get("examTypes")
            if (
                exam
                and isinstance(exam_types, list)
                and exam not in {str(value).lower() for value in exam_types}
            ):
                continue
            keys = [row.get("slug"), row.get("name"), row.get("displayName")]
            keys.extend(
                alias for alias in row.get("aliases") or [] if isinstance(alias, str)
            )
            for key in keys:
                if isinstance(key, str) and (slug := slugify(key)):
                    names.add(slug)
        return names

    async def catalogue_years(
        self,
        subject_slug: str,
        exam: str,
        *,
        known: Collection[int] = (),
        progress: dict[int, int | None] | None = None,
    ) -> dict[int, int | None] | None:
        """ALOC's per-subject year breakdown: one cached call, counts included."""
        try:
            rows = await self.discover(DiscoveryResource.SUBJECT_YEARS, subject_slug)
        except ProviderNotFound:
            return {}
        data = rows.get("years") if isinstance(rows, dict) else rows
        if not isinstance(data, list):
            return None
        years: dict[int, int | None] = {}
        for row in data:
            if not isinstance(row, dict):
                continue
            year = row.get("year")
            if not isinstance(year, int) or isinstance(year, bool):
                continue
            breakdown = row.get("breakdown")
            if isinstance(breakdown, dict):
                count = breakdown.get(exam)
                if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                    years[year] = count
            elif exam in {str(value).lower() for value in row.get("examTypes") or []}:
                years[year] = None
        return years

    async def draw(
        self, subject_slug: str, exam_type: str, exam_year: int, limit: int
    ) -> DrawResult | None:
        params: dict[str, str | int] = {
            "subject": SUBJECT_ALIASES.get(subject_slug, subject_slug),
            "examType": EXAM_ALIASES.get(exam_type.upper(), exam_type.lower()),
            "year": exam_year,
            "limit": ALOC_PAGE_LIMIT,
        }
        result = DrawResult()
        cursor: str | None = None
        try:
            async with self.client() as client:
                for _ in range(ALOC_MAX_PAGES):
                    page_params = {**params, "cursor": cursor} if cursor else params
                    response = await client.get(
                        f"{self.base_url}/questions",
                        params=page_params,
                        headers=self.headers,
                    )
                    if response.status_code >= 400:
                        result.status_code = response.status_code
                        result.body = response.text
                        return result
                    payload = response.json()
                    result.items.extend(
                        item
                        for item in payload.get("data") or []
                        if isinstance(item, dict)
                    )
                    credits = (payload.get("meta") or {}).get("creditsRemaining")
                    if credits is not None:
                        result.credits_remaining = int(credits)
                    pagination = payload.get("pagination") or {}
                    cursor = pagination.get("nextCursor")
                    if not pagination.get("hasMore") or not cursor:
                        result.exhausted = True
                        result.complete = True
                        return result
        except httpx.HTTPError:
            result.status_code = 503
            return result if result.items else None
        result.exhausted = True
        return result

    async def search(
        self,
        *,
        subject_slug: str | None,
        exam_type: str | None,
        exam_year: int | None,
        limit: int,
        cursor: str | None = None,
        topic: str | None = None,
        subtopic: str | None = None,
        random: bool = False,
    ) -> SearchPage | None:
        params: dict[str, str | int] = {"limit": min(limit, ALOC_SEARCH_LIMIT)}
        if topic:
            params["topic"] = topic
        if subtopic:
            params["subtopic"] = subtopic
        if random:
            params["random"] = "true"
        if subject_slug:
            params["subject"] = SUBJECT_ALIASES.get(subject_slug, subject_slug)
        if exam_type:
            params["examType"] = EXAM_ALIASES.get(exam_type.upper(), exam_type.lower())
        if exam_year:
            params["year"] = exam_year
        if cursor:
            params["cursor"] = cursor
        try:
            async with self.client() as client:
                response = await client.get(
                    f"{self.base_url}/questions", params=params, headers=self.headers
                )
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return SearchPage(status_code=response.status_code, body=response.text)
        payload = response.json()
        pagination = payload.get("pagination") or {}
        credits = (payload.get("meta") or {}).get("creditsRemaining")
        try:
            credits_remaining = int(credits) if credits is not None else None
        except (TypeError, ValueError):
            credits_remaining = None
        return SearchPage(
            items=[
                item for item in payload.get("data") or [] if isinstance(item, dict)
            ],
            next_cursor=pagination.get("nextCursor"),
            has_more=bool(pagination.get("hasMore")),
            credits_remaining=credits_remaining,
        )

    def normalize(self, item: dict) -> NormalizedQuestion:
        options = item.get("options") or {}
        # ``section`` is the comprehension passage itself when ``hasPassage``,
        # otherwise the instruction shared by a group of questions.
        section = item.get("section")
        section = section if isinstance(section, str) and section.strip() else None
        has_passage = bool(item.get("hasPassage"))
        if isinstance(options, list):
            options = {chr(65 + index): value for index, value in enumerate(options)}
        return NormalizedQuestion(
            provider_id=str(item.get("id") or ""),
            text=str(item.get("text") or item.get("question") or ""),
            options={str(key).upper(): str(value) for key, value in options.items()},
            answer=str(item.get("correctAnswer") or item.get("answer") or ""),
            explanation=str(item.get("explanation") or ""),
            image_url=item.get("imageUrl"),
            raw=item,
            exam_type=item.get("examType"),
            exam_year=item.get("year")
            if isinstance(item.get("year"), int)
            and not isinstance(item.get("year"), bool)
            else None,
            question_number=item.get("questionNumber")
            if isinstance(item.get("questionNumber"), int)
            and not isinstance(item.get("questionNumber"), bool)
            else None,
            passage=section if has_passage else None,
            instruction=None if has_passage else section,
        )

    def should_saturate(
        self, row: ProviderFetch, result: DrawResult, new_count: int
    ) -> bool:
        return result.exhausted

    async def explain(
        self, provider_question_id: str, depth: str = "step_by_step"
    ) -> dict | None:
        try:
            async with self.client(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/questions/{provider_question_id}/explain",
                    json={"depth": depth},
                    headers=self.headers,
                )
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None

    def flatten_explanation(self, payload: dict) -> str:
        return flatten_explanation(payload)

    def _discovery_path(
        self, resource: DiscoveryResource, key: str | int | None
    ) -> str:
        if resource == DiscoveryResource.COVERAGE:
            return "coverage"
        if resource == DiscoveryResource.SUBJECTS:
            return "subjects"
        if resource == DiscoveryResource.YEAR_SUBJECTS:
            return f"subjects/years/{int(key or 0)}"
        slug = str(key or "").strip().lower()
        subject = quote(SUBJECT_ALIASES.get(slug, slug), safe="")
        if resource == DiscoveryResource.SUBJECT_TOPICS:
            return f"subjects/{subject}/topics"
        if resource == DiscoveryResource.SUBJECT_YEARS:
            return f"subjects/{subject}/years"
        return f"subjects/{subject}"

    async def discover(
        self, resource: DiscoveryResource, key: str | int | None = None
    ) -> dict | list | None:
        headers = self.headers if self.api_key else {}
        try:
            async with self.client() as client:
                response = await client.get(
                    f"{self.base_url}/{self._discovery_path(resource, key)}",
                    headers=headers,
                )
        except httpx.HTTPError:
            return None
        if response.status_code == 404 and resource != DiscoveryResource.COVERAGE:
            raise ProviderNotFound(str(key))
        if response.status_code >= 400:
            return None
        data = response.json().get("data")
        if resource == DiscoveryResource.COVERAGE:
            return self._coverage(data) if isinstance(data, dict) else None
        return data if isinstance(data, dict | list) else None

    def _coverage(self, data: dict) -> dict:
        exam_types = {value: key for key, value in EXAM_ALIASES.items()}
        return {
            "summary": data.get("summary") or {},
            "examBodies": [
                {**body, "examType": exam_types.get(str(body.get("slug")))}
                for body in data.get("examBodies") or []
                if isinstance(body, dict)
            ],
        }
