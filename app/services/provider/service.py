import time
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.timeutil import utcnow
from app.database.models import (
    ProviderFetch,
    ProviderQuestion,
    ProviderState,
    Question,
    Subject,
)
from app.database.repositories.curriculum import subjects_repository
from app.database.repositories.provider import (
    fetches_repository,
    provider_questions_repository,
    states_repository,
)
from app.database.repositories.question import questions_repository
from app.services.catalogue import bust_catalogue
from app.services.provider.base import (
    DiscoveryResource,
    DrawResult,
    ProviderNotFound,
    QuestionProvider,
    SearchPage,
)
from app.services.provider.factory import ProviderFactory
from app.services.provider.rules import (
    COOLDOWN_MINUTES,
    LEASE_WINDOW_MS,
    MAPPER_VERSION,
    MAX_DRAWS,
    cache_key,
    fingerprint,
    next_circuit,
    objective_ok,
)


async def load_provider_state(
    session: AsyncSession, provider_name: str | None = None
) -> ProviderState:
    name = (provider_name or settings.question_provider).upper()
    state = await states_repository.by_id(session, name)
    if state is None:
        state = ProviderState(provider=name, circuit="OK")
        await states_repository.add(session, state, flush=True)
    return state


class EnsureProviderQuestionsService:
    def __init__(
        self,
        session: AsyncSession,
        subject_slug: str,
        exam_type: str,
        exam_year: int,
        limit: int = 50,
        provider: QuestionProvider | None = None,
    ) -> None:
        self.session = session
        self.subject_slug = subject_slug
        self.exam_type = exam_type
        self.exam_year = exam_year
        self.limit = limit
        self.provider = provider or ProviderFactory.create()

    async def process(self) -> dict:
        subject = await subjects_repository.by_slug(self.session, self.subject_slug)
        key = cache_key(self.subject_slug, self.exam_type, self.exam_year)
        row = await fetches_repository.by_cache_key(
            self.session, self.provider.name, key
        )
        if subject is None:
            return await self._unknown_subject(row, key)
        if row and row.status in {"SATURATED", "FAILED"}:
            return {"status": row.status, "cacheKey": key}
        now = utcnow()
        if row and row.status == "PENDING" and row.started_at:
            age_ms = (now - row.started_at).total_seconds() * 1000
            if age_ms < LEASE_WINDOW_MS:
                return {
                    "status": "PENDING",
                    "leased": True,
                    "cacheKey": key,
                }
        row = await self._lease(row, subject, key, now)
        await self.session.flush()
        state = await load_provider_state(self.session, self.provider.name)
        if state.circuit == "BLOCKED" or (
            state.circuit == "EXHAUSTED"
            and state.cooldown_until
            and state.cooldown_until > now
        ):
            return {
                "status": row.status,
                "circuit": state.circuit,
                "cacheKey": key,
            }
        if not self.provider.configured:
            return {"status": row.status, "cacheKey": key}
        await self._draw(row, subject)
        return {
            "status": row.status,
            "cacheKey": key,
            "promoted": row.promoted_count,
        }

    async def _unknown_subject(self, row: ProviderFetch | None, key: str) -> dict:
        if row is None:
            row = ProviderFetch(
                id=cuid(),
                provider=self.provider.name,
                cache_key=key,
                status="FAILED",
                exam_type=self.exam_type,
                exam_year=self.exam_year,
            )
            await fetches_repository.add(self.session, row)
        else:
            row.status = "FAILED"
        return {"status": "FAILED", "reason": "unknown-subject"}

    async def _lease(
        self,
        row: ProviderFetch | None,
        subject: Subject,
        key: str,
        now,
    ) -> ProviderFetch:
        if row is None:
            row = ProviderFetch(
                id=cuid(),
                provider=self.provider.name,
                cache_key=key,
                status="PENDING",
                started_at=now,
                subject_id=subject.id,
                exam_type=self.exam_type,
                exam_year=self.exam_year,
            )
            await fetches_repository.add(self.session, row)
        else:
            row.started_at = now
            row.subject_id = subject.id
        return row

    async def _draw(self, row: ProviderFetch, subject: Subject) -> None:
        result = await self.provider.draw(
            subject.slug, self.exam_type, self.exam_year, self.limit
        )
        if result is None:
            return
        state = await load_provider_state(self.session, self.provider.name)
        if result.credits_remaining is not None:
            state.credits_remaining = result.credits_remaining
        if result.failed:
            state.circuit = next_circuit(result.status_code, result.body, state.circuit)
            if state.circuit == "EXHAUSTED":
                state.cooldown_until = utcnow() + timedelta(minutes=COOLDOWN_MINUTES)
            if result.status_code == 404:
                row.status = "SATURATED"
            if not result.items:
                return
        await self._ingest(row, subject, result)

    async def _ingest(
        self, row: ProviderFetch, subject: Subject, result: DrawResult
    ) -> None:
        new_count = 0
        for item in result.items:
            if not isinstance(item, dict):
                continue
            if await self._stage_item(row, subject, item):
                new_count += 1
        row.draw_count += 1
        row.raw_count += len(result.items)
        row.new_in_last_draw = new_count
        if not result.failed and self.provider.should_saturate(row, result, new_count):
            row.status = "SATURATED"
            bust_catalogue()

    async def _stage_item(
        self, row: ProviderFetch, subject: Subject, item: dict
    ) -> bool:
        normalized = self.provider.normalize(item)
        digest = fingerprint(normalized.text, normalized.options)
        staged = ProviderQuestion(
            id=cuid(),
            fetch_id=row.id,
            provider_question_id=normalized.provider_id or digest[:16],
            fingerprint=digest,
            payload=item,
            mapper_version=MAPPER_VERSION,
        )
        if (
            not objective_ok(normalized.options, normalized.answer)
            or not normalized.text
        ):
            staged.status = "REJECTED"
            staged.rejection_reasons = ["incomplete"]
            row.rejected_count += 1
            await provider_questions_repository.add(self.session, staged)
            return False
        explanation = normalized.explanation
        if (
            not explanation
            and normalized.provider_id
            and self.provider.fetch_explanations
        ):
            bought = await self.provider.explain(normalized.provider_id)
            if bought:
                staged.payload = {**item, "explain": bought}
                explanation = self.provider.flatten_explanation(bought)
        question = Question(
            id=cuid(),
            subject_id=subject.id,
            exam_type=self.exam_type,
            exam_year=self.exam_year,
            question_text=normalized.text,
            question_image_url=normalized.image_url or None,
            question_type="OBJECTIVE",
            options=normalized.options,
            correct_answer=normalized.answer.upper(),
            explanation=explanation,
            difficulty="INTERMEDIATE",
        )
        await questions_repository.add(self.session, question)
        staged.status = "PROMOTED"
        staged.question_id = question.id
        staged.promoted_at = utcnow()
        row.promoted_count += 1
        await provider_questions_repository.add(self.session, staged)
        return True


class SaturateProviderService:
    def __init__(
        self,
        session: AsyncSession,
        subject_slug: str,
        exam_type: str,
        exam_year: int,
        provider: QuestionProvider | None = None,
    ) -> None:
        self.session = session
        self.subject_slug = subject_slug
        self.exam_type = exam_type
        self.exam_year = exam_year
        self.provider = provider or ProviderFactory.create()

    async def process(self) -> dict:
        last = {}
        for _ in range(MAX_DRAWS):
            last = await EnsureProviderQuestionsService(
                self.session,
                self.subject_slug,
                self.exam_type,
                self.exam_year,
                provider=self.provider,
            ).process()
            if last.get("status") in {"SATURATED", "FAILED"}:
                break
        return last


class ResetFailedFetchService:
    def __init__(
        self,
        session: AsyncSession,
        subject_slug: str,
        exam_type: str,
        exam_year: int,
        provider: QuestionProvider | None = None,
    ) -> None:
        self.session = session
        self.subject_slug = subject_slug
        self.exam_type = exam_type
        self.exam_year = exam_year
        self.provider = provider or ProviderFactory.create()

    async def process(self) -> bool:
        key = cache_key(self.subject_slug, self.exam_type, self.exam_year)
        row = await fetches_repository.by_cache_key(
            self.session, self.provider.name, key
        )
        if row and row.status == "FAILED":
            row.status = "PENDING"
            row.started_at = None
            return True
        return False


class ClearProviderBlockService:
    def __init__(
        self, session: AsyncSession, provider: QuestionProvider | None = None
    ) -> None:
        self.session = session
        self.provider = provider or ProviderFactory.create()

    async def process(self) -> bool:
        state = await load_provider_state(self.session, self.provider.name)
        if state.circuit != "BLOCKED":
            return False
        state.circuit = "OK"
        state.cooldown_until = None
        return True


class SearchProviderQuestionsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        subject_key: str | None,
        exam_type: str | None,
        exam_year: int | None,
        limit: int,
        cursor: str | None = None,
        provider: QuestionProvider | None = None,
    ) -> None:
        self.session = session
        self.subject_key = subject_key
        self.exam_type = exam_type
        self.exam_year = exam_year
        self.limit = limit
        self.cursor = cursor
        self.provider = provider or ProviderFactory.create()

    async def process(self) -> dict:
        if not (self.subject_key or self.exam_type or self.exam_year):
            raise ApiError(400, "Pass at least one of subjectId, examType or examYear")
        if not self.provider.configured:
            raise ApiError(503, "Questions are unavailable right now.")
        subject = (
            await subjects_repository.by_id_or_slug(self.session, self.subject_key)
            if self.subject_key
            else None
        )
        page = await self.provider.search(
            subject_slug=subject.slug if subject else self.subject_key,
            exam_type=self.exam_type,
            exam_year=self.exam_year,
            limit=self.limit,
            cursor=self.cursor,
        )
        if page is None:
            raise ApiError(503, "Questions are unavailable right now.")
        if page.status_code == 404:
            page = SearchPage()
        elif page.status_code == 429:
            raise ApiError(503, "Too many question requests. Try again shortly.")
        elif page.failed:
            raise ApiError(503, "Questions are unavailable right now.")
        return {
            "questions": [self._question(item, subject) for item in page.items],
            "pagination": {
                "limit": self.limit,
                "cursor": self.cursor,
                "nextCursor": page.next_cursor,
                "hasMore": page.has_more,
            },
        }

    def _question(self, item: dict, subject: Subject | None) -> dict:
        normalized = self.provider.normalize(item)
        exam_type = item.get("examType") or self.exam_type
        payload = {
            "id": normalized.provider_id,
            "subjectId": subject.id if subject else None,
            "topicId": None,
            "examType": str(exam_type).upper() if exam_type else None,
            "examYear": item.get("year") or self.exam_year,
            "questionNumber": None,
            "questionText": normalized.text,
            "questionImageUrl": normalized.image_url or None,
            "questionType": "OBJECTIVE",
            "options": normalized.options,
            "difficulty": None,
            "marks": 1,
            "timeEstimateSeconds": 90,
            "correctAnswer": normalized.answer.upper(),
            "explanation": normalized.explanation or None,
            "explanationImageUrl": None,
        }
        if item.get("hasPassage"):
            payload["hasPassage"] = True
            payload["passage"] = item.get("section")
            payload["passageGroup"] = item.get("category")
        return payload


_DISCOVERY_CACHE: dict[tuple[str, str, str], tuple[float, dict | list]] = {}
DISCOVERY_TTL_SECONDS = 600


class ProviderDiscoveryService:
    def __init__(
        self,
        resource: DiscoveryResource,
        key: str | int | None = None,
        provider: QuestionProvider | None = None,
    ) -> None:
        self.resource = resource
        self.key = key
        self.provider = provider or ProviderFactory.create()

    async def process(self) -> dict:
        return {"provider": self.provider.name, "data": await self._fetch()}

    async def _fetch(self) -> dict | list:
        cache_id = (
            self.provider.name,
            self.resource.value,
            str(self.key or "").strip().lower(),
        )
        cached = _DISCOVERY_CACHE.get(cache_id)
        if cached and time.monotonic() - cached[0] < DISCOVERY_TTL_SECONDS:
            return cached[1]
        try:
            data = await self.provider.discover(self.resource, self.key)
        except ProviderNotFound as missing:
            raise ApiError(
                404, "Not found in the question provider's catalogue."
            ) from missing
        if data is None:
            if cached:
                return cached[1]
            raise ApiError(503, "Question coverage is unavailable right now.")
        _DISCOVERY_CACHE[cache_id] = (time.monotonic(), data)
        return data


class ProviderCoverageService(ProviderDiscoveryService):
    def __init__(self, provider: QuestionProvider | None = None) -> None:
        super().__init__(DiscoveryResource.COVERAGE, provider=provider)

    async def process(self) -> dict:
        coverage = await self._fetch()
        if not isinstance(coverage, dict):
            raise ApiError(503, "Question coverage is unavailable right now.")
        return {"provider": self.provider.name, **coverage}
