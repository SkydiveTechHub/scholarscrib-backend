import httpx

from app.core.config import settings
from app.database.models import ProviderFetch
from app.services.provider import rules
from app.services.provider.base import DrawResult, NormalizedQuestion, QuestionProvider


class SdashProvider(QuestionProvider):
    name = "SDASH"

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str | None = None,
        access_token: str | None = None,
    ) -> None:
        super().__init__(transport)
        self.base_url = (base_url or settings.sdash_base_url).rstrip("/")
        self.access_token = access_token or settings.sdash_access_token

    @property
    def configured(self) -> bool:
        return bool(self.access_token)

    async def draw(
        self, subject_slug: str, exam_type: str, exam_year: int, limit: int
    ) -> DrawResult | None:
        try:
            async with self.client() as client:
                response = await client.get(
                    f"{self.base_url}/questions",
                    params={
                        "subject": subject_slug,
                        "exam": exam_type.lower(),
                        "year": exam_year,
                        "limit": limit,
                    },
                    headers={"Authorization": f"Bearer {self.access_token}"},
                )
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return DrawResult(status_code=response.status_code, body=response.text)
        payload = response.json()
        items = (
            payload
            if isinstance(payload, list)
            else payload.get("data") or payload.get("questions") or []
        )
        return DrawResult(items=items, status_code=response.status_code)

    def normalize(self, item: dict) -> NormalizedQuestion:
        options = item.get("options") or {}
        if isinstance(options, list):
            options = {chr(65 + index): value for index, value in enumerate(options)}
        return NormalizedQuestion(
            provider_id=str(item.get("id") or item.get("questionId") or ""),
            text=str(item.get("question") or item.get("questionText") or ""),
            options={str(key): str(value) for key, value in options.items()},
            answer=str(item.get("answer") or item.get("correctAnswer") or ""),
            explanation=item.get("explanation") or item.get("solution") or "",
            image_url=item.get("image") or item.get("imageUrl"),
            raw=item,
        )

    def should_saturate(
        self, row: ProviderFetch, result: DrawResult, new_count: int
    ) -> bool:
        return rules.should_saturate(row.draw_count, len(result.items), new_count)
