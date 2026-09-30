import httpx

from app.core.config import settings
from app.database.models import ProviderFetch
from app.services.provider.base import DrawResult, NormalizedQuestion, QuestionProvider

ALOC_PAGE_LIMIT = 15
ALOC_MAX_PAGES = 20

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
    prose = str(data.get("explanation") or "").strip()
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
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key or ""}

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
                        return result
        except httpx.HTTPError:
            return result if result.items else None
        result.exhausted = True
        return result

    def normalize(self, item: dict) -> NormalizedQuestion:
        options = item.get("options") or {}
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
