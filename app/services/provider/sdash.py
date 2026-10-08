import httpx

from app.core.config import settings
from app.database.models import ProviderFetch
from app.services.provider import rules
from app.services.provider.base import (
    DrawResult,
    NormalizedQuestion,
    PaperFetchMode,
    QuestionProvider,
    SearchPage,
)


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

    @property
    def paper_fetch_mode(self) -> PaperFetchMode:
        return PaperFetchMode.SAMPLED

    async def _search(
        self,
        *,
        subject_slug: str | None,
        exam_type: str | None,
        exam_year: int | None,
        limit: int,
    ) -> SearchPage | None:
        if not self.access_token:
            return SearchPage(status_code=503, body="SDASH access token is missing.")
        params: dict[str, str | int] = {"limit": min(max(limit, 1), 50)}
        if subject_slug:
            subject = subject_slug.strip().lower()
            params["subject"] = rules.SUBJECT_ALIASES.get(subject, subject)
        if exam_type:
            exam_slug = exam_type.strip().lower()
            params["type"] = {
                "jamb": "utme",
                "waec": "wassce",
            }.get(exam_slug, exam_slug)
        if exam_year is not None:
            params["year"] = exam_year

        try:
            async with self.client() as client:
                response = await client.get(
                    f"{self.base_url}/v1/q",
                    params=params,
                    headers={"AccessToken": self.access_token},
                )
        except httpx.HTTPError:
            return None

        try:
            payload = response.json()
        except ValueError:
            return SearchPage(
                status_code=response.status_code
                if response.status_code >= 400
                else 502,
                body=response.text,
            )

        if not isinstance(payload, dict):
            return SearchPage(
                status_code=502, body="Provider returned an invalid response envelope."
            )
        try:
            payload_status = int(payload.get("status", response.status_code))
        except (TypeError, ValueError):
            return SearchPage(
                status_code=502, body="Provider returned an invalid status."
            )
        if response.status_code >= 400 or payload_status >= 400:
            status_code = (
                response.status_code if response.status_code >= 400 else payload_status
            )
            return SearchPage(
                status_code=status_code,
                body=str(payload.get("message") or response.text),
            )

        data = payload.get("data")
        if isinstance(data, dict):
            items = [data]
        elif isinstance(data, list) and all(isinstance(item, dict) for item in data):
            items = data
        else:
            return SearchPage(
                status_code=502, body="Provider returned an invalid question payload."
            )
        return SearchPage(items=items, status_code=payload_status)

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
        if cursor:
            return SearchPage(
                status_code=400,
                body="SDASH does not support provider cursors; use local pagination.",
            )
        return await self._search(
            subject_slug=subject_slug,
            exam_type=exam_type,
            exam_year=exam_year,
            limit=limit,
        )

    async def draw(
        self, subject_slug: str, exam_type: str, exam_year: int, limit: int
    ) -> DrawResult | None:
        page = await self._search(
            subject_slug=subject_slug,
            exam_type=exam_type,
            exam_year=exam_year,
            limit=limit,
        )
        if page is None:
            return None
        return DrawResult(
            items=page.items,
            status_code=page.status_code,
            body=page.body,
            complete=page.status_code == 404,
            exhausted=page.status_code == 404,
        )

    def normalize(self, item: dict) -> NormalizedQuestion:
        options = item.get("option") or item.get("options") or {}
        if isinstance(options, list):
            options = {chr(65 + index): value for index, value in enumerate(options)}
        return NormalizedQuestion(
            provider_id=str(item.get("id") or item.get("questionId") or ""),
            text=str(item.get("question") or item.get("questionText") or ""),
            options={str(key).upper(): str(value) for key, value in options.items()},
            answer=str(item.get("answer") or item.get("correctAnswer") or ""),
            explanation=item.get("solution") or item.get("explanation") or "",
            image_url=item.get("image") or item.get("imageUrl"),
            raw=item,
        )

    def should_saturate(
        self, row: ProviderFetch, result: DrawResult, new_count: int
    ) -> bool:
        returned_count = (
            result.last_batch_count
            if result.last_batch_count is not None
            else len(result.items)
        )
        return rules.should_saturate(row.draw_count, returned_count, new_count)
