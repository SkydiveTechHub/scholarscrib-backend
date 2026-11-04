import asyncio
import re
import time
from collections.abc import Collection
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.database.models import ProviderFetch
from app.services.provider import rules
from app.services.provider.aloc import flatten_explanation
from app.services.provider.base import (
    DrawResult,
    NormalizedQuestion,
    PaperFetchMode,
    QuestionProvider,
    SearchPage,
)

# Years and subjects change a few times a year at most; one call a day is plenty.
REFERENCE_TTL_SECONDS = 24 * 3600
# How many year probes run at once for one (subject, exam) discovery.
PROBE_CONCURRENCY = 6
# Above this share of failed probes the answer is not trustworthy, so nothing
# is reported (and nothing is remembered as "discovered").
PROBE_FAILURE_LIMIT = 0.2
# A shared block longer than this (or split into paragraphs) is a reading
# passage; anything shorter is an instruction line such as "Choose the word...".
PASSAGE_MIN_CHARS = 200
# SDash draws at random with no cursors, so a paper is a snapshot merged from
# this many parallel draws of ``PROVIDER_SAMPLE_BATCH_LIMIT`` questions each.
SAMPLE_DRAWS = 3

# (base url, token, path) -> (stored at, data). Kept per process: the provider
# object is rebuilt on every request, so an instance cache would never be hit.
_REFERENCE_CACHE: dict[tuple[str, str, str], tuple[float, list]] = {}


def clear_reference_cache() -> None:
    _REFERENCE_CACHE.clear()


@dataclass
class SdashReply:
    status_code: int
    data: object = None
    body: str = ""
    payload: dict | None = None

    @property
    def failed(self) -> bool:
        return self.status_code >= 400


def _year_of(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, dict):
        value = value.get("year") or value.get("slug") or value.get("name")
    try:
        year = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return year if 1900 <= year <= 2100 else None


def _is_passage(text: str) -> bool:
    return len(text) >= PASSAGE_MIN_CHARS or "\n\n" in text


def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.ASCII)
    return re.sub(r"[\s_]+", "-", text).strip("-")


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

    @property
    def sample_draws(self) -> int:
        return SAMPLE_DRAWS

    @property
    def headers(self) -> dict[str, str]:
        return {"AccessToken": self.access_token or ""}

    def next_circuit(self, status_code: int, body: str, current: str) -> str:
        """429 is the monthly quota; 401 and 403 mean our token cannot be used.

        A 403 saying a *feature* is outside the plan (v2 and up) is not a
        problem for the v1 calls the bank is built on, so it leaves the
        circuit alone.
        """
        if status_code == 429:
            return "EXHAUSTED"
        if status_code == 403 and "not available" in body.lower():
            return current
        return rules.next_circuit(status_code, body, current)

    async def _call(
        self, client: httpx.AsyncClient, path: str, params: dict | None = None
    ) -> SdashReply | None:
        """One GET. ``None`` when the provider could not be reached at all."""
        try:
            response = await client.get(
                f"{self.base_url}{path}", params=params, headers=self.headers
            )
        except httpx.HTTPError:
            return None
        try:
            payload = response.json()
        except ValueError:
            return SdashReply(
                status_code=response.status_code
                if response.status_code >= 400
                else 502,
                body=response.text,
            )
        if not isinstance(payload, dict):
            return SdashReply(
                status_code=502, body="Provider returned an invalid response envelope."
            )
        try:
            payload_status = int(payload.get("status", response.status_code))
        except (TypeError, ValueError):
            return SdashReply(
                status_code=502, body="Provider returned an invalid status."
            )
        if response.status_code >= 400 or payload_status >= 400:
            return SdashReply(
                status_code=response.status_code
                if response.status_code >= 400
                else payload_status,
                body=str(payload.get("message") or response.text),
                payload=payload,
            )
        return SdashReply(
            status_code=payload_status, data=payload.get("data"), payload=payload
        )

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
            params["subject"] = rules.sdash_subject(subject_slug)
        if exam_type:
            params["type"] = rules.sdash_exam_type(exam_type)
        if exam_year is not None:
            params["year"] = exam_year

        async with self.client() as client:
            reply = await self._call(client, "/v1/q", params)
        if reply is None:
            return None
        if reply.failed:
            return SearchPage(status_code=reply.status_code, body=reply.body)
        data = reply.data
        if isinstance(data, dict):
            items = [data]
        elif isinstance(data, list) and all(isinstance(item, dict) for item in data):
            items = data
        else:
            return SearchPage(
                status_code=502, body="Provider returned an invalid question payload."
            )
        return SearchPage(items=items, status_code=reply.status_code)

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
        exam_type = str(item.get("examtype") or item.get("examType") or "").strip()
        section = item.get("section")
        section = section.strip() if isinstance(section, str) else ""
        return NormalizedQuestion(
            provider_id=str(item.get("id") or item.get("questionId") or ""),
            text=str(item.get("question") or item.get("questionText") or ""),
            options={str(key).upper(): str(value) for key, value in options.items()},
            answer=str(item.get("answer") or item.get("correctAnswer") or ""),
            explanation=item.get("solution") or item.get("explanation") or "",
            image_url=item.get("image") or item.get("imageUrl"),
            raw=item,
            exam_type=exam_type or None,
            exam_year=_year_of(item.get("examyear") or item.get("year")),
            passage=section if section and _is_passage(section) else None,
            instruction=section if section and not _is_passage(section) else None,
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

    # ----- catalogue --------------------------------------------------

    async def _reference(self, client: httpx.AsyncClient, path: str) -> list | None:
        """A list endpoint (``/v1/subjects``, ``/v1/years``), cached for a day."""
        cache_id = (self.base_url, self.access_token or "", path)
        cached = _REFERENCE_CACHE.get(cache_id)
        if cached and time.monotonic() - cached[0] < REFERENCE_TTL_SECONDS:
            return cached[1]
        reply = await self._call(client, path)
        if reply is None or reply.failed or not isinstance(reply.data, list):
            return None
        _REFERENCE_CACHE[cache_id] = (time.monotonic(), reply.data)
        return reply.data

    async def catalogue_subjects(self, exam: str | None = None) -> set[str] | None:
        """SDash lists subjects without saying which exams carry them."""
        if not self.access_token:
            return None
        async with self.client() as client:
            rows = await self._reference(client, "/v1/subjects")
        if rows is None:
            return None
        names: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or not row.get("slug"):
                continue
            names |= rules.our_slugs_for_sdash(str(row["slug"]))
            if row.get("name"):
                names.add(_slugify(str(row["name"])))
        return names

    async def catalogue_years(
        self,
        subject_slug: str,
        exam: str,
        *,
        known: Collection[int] = (),
        progress: dict[int, int | None] | None = None,
    ) -> dict[int, int | None] | None:
        """Probe ``/v1/q?limit=1`` for every year SDash reports, newest first.

        SDash has no per-subject index, so a year is "present" when a one
        question draw for it is not a 404. Counts are unknown. Each pair costs
        at most one call per year (about 39), and the caller stores the result.
        """
        if not self.access_token:
            return None
        subject = rules.sdash_subject(subject_slug)
        wire_exam = rules.sdash_exam_type(exam)
        async with self.client() as client:
            rows = await self._reference(client, "/v1/years")
            reported = {
                year for row in rows or [] if (year := _year_of(row)) is not None
            }
            if not reported:
                return None
            # A year already confirmed never needs paying for again.
            confirmed = reported & set(known)
            years = sorted(reported - confirmed, reverse=True)
            if progress is None:
                progress = {}
            progress.update({year: None for year in confirmed})
            if not years:
                return dict(progress)
            gate = asyncio.Semaphore(PROBE_CONCURRENCY)
            stop = asyncio.Event()

            async def probe(year: int) -> bool | None:
                """True: has a paper. False: none. None: could not tell."""
                async with gate:
                    if stop.is_set():
                        return None
                    reply = await self._call(
                        client,
                        "/v1/q",
                        {
                            "subject": subject,
                            "type": wire_exam,
                            "year": year,
                            "limit": 1,
                        },
                    )
                if reply is None:
                    return None
                if reply.status_code == 404:
                    return False
                if reply.status_code in {401, 403, 429}:
                    # The token or quota is the problem: more probes would
                    # only burn calls that cannot succeed.
                    stop.set()
                    return None
                if reply.failed:
                    return None
                found = bool(reply.data)
                if found:
                    progress[year] = None
                return found

            results = await asyncio.gather(*(probe(year) for year in years))
        unknown = sum(1 for found in results if found is None)
        if unknown / len(years) > PROBE_FAILURE_LIMIT:
            return None
        return dict(progress)

    # ----- explanations -----------------------------------------------

    async def explain(
        self, provider_question_id: str, depth: str = "step_by_step"
    ) -> dict | None:
        """The solution SDash already carries, in ALOC's explanation shape.

        ``/v1/q?id=`` is tried first (every plan has it). ``/v3/q?id=`` adds a
        structured explanation, and is only asked when v1 has no solution, as
        it may be outside the plan.
        """
        if not self.access_token:
            return None
        async with self.client(timeout=30) as client:
            reply = await self._call(client, "/v1/q", {"id": provider_question_id})
            solution = self._solution(reply)
            if solution:
                return {"data": {"explanation": solution}}
            richer = await self._call(client, "/v3/q", {"id": provider_question_id})
        return self._explanation_v3(richer)

    @staticmethod
    def _record(reply: SdashReply | None) -> dict | None:
        if reply is None or reply.failed:
            return None
        data = reply.data
        if isinstance(data, list):
            data = data[0] if data else None
        return data if isinstance(data, dict) else None

    def _solution(self, reply: SdashReply | None) -> str:
        record = self._record(reply)
        if not record:
            return ""
        return str(record.get("solution") or record.get("explanation") or "").strip()

    def _explanation_v3(self, reply: SdashReply | None) -> dict | None:
        record = self._record(reply)
        block = record.get("explanation") if record else None
        if isinstance(block, str):
            block = {"detailed": block}
        if not isinstance(block, dict):
            return None
        prose = str(
            block.get("detailed")
            or block.get("summary")
            or block.get("why_correct")
            or ""
        ).strip()
        steps = [str(step) for step in block.get("steps") or [] if str(step).strip()]
        wrong = block.get("why_others_wrong")
        mistakes = (
            [
                {"mistake": f"Option {str(key).upper()}", "whyWrong": str(why)}
                for key, why in wrong.items()
            ]
            if isinstance(wrong, dict)
            else []
        )
        if not (prose or steps):
            return None
        return {
            "data": {
                "explanation": prose,
                "steps": steps,
                "commonMistakes": mistakes,
            }
        }

    def flatten_explanation(self, payload: dict) -> str:
        return flatten_explanation(payload)
