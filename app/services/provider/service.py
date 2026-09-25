from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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
from app.services.provider.rules import (
    LEASE_WINDOW_MS,
    MAPPER_VERSION,
    cache_key,
    fingerprint,
    next_circuit,
    objective_ok,
    should_saturate,
)


async def load_provider_state(session: AsyncSession) -> ProviderState:
    state = await states_repository.by_id(session, "SDASH")
    if state is None:
        state = ProviderState(provider="SDASH", circuit="OK")
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
    ) -> None:
        self.session = session
        self.subject_slug = subject_slug
        self.exam_type = exam_type
        self.exam_year = exam_year
        self.limit = limit

    async def process(self) -> dict:
        subject = await subjects_repository.by_slug(self.session, self.subject_slug)
        key = cache_key(self.subject_slug, self.exam_type, self.exam_year)
        row = await fetches_repository.by_cache_key(self.session, "SDASH", key)
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
        state = await load_provider_state(self.session)
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
        if not settings.sdash_access_token:
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
                provider="SDASH",
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
                provider="SDASH",
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
        url = f"{settings.sdash_base_url.rstrip('/')}/questions"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    url,
                    params={
                        "subject": subject.slug,
                        "exam": self.exam_type.lower(),
                        "year": self.exam_year,
                        "limit": self.limit,
                    },
                    headers={
                        "Authorization": (f"Bearer {settings.sdash_access_token}")
                    },
                )
        except httpx.HTTPError:
            return
        state = await load_provider_state(self.session)
        if response.status_code >= 400:
            state.circuit = next_circuit(
                response.status_code, response.text, state.circuit
            )
            if state.circuit == "EXHAUSTED":
                state.cooldown_until = utcnow() + timedelta(minutes=15)
            if response.status_code == 404:
                row.status = "SATURATED"
            return
        await self._ingest(row, subject, response.json())

    async def _ingest(self, row: ProviderFetch, subject: Subject, payload) -> None:
        items = (
            payload
            if isinstance(payload, list)
            else payload.get("data") or payload.get("questions") or []
        )
        new_count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            if await self._stage_item(row, subject, item):
                new_count += 1
        row.draw_count += 1
        row.raw_count += len(items)
        row.new_in_last_draw = new_count
        if should_saturate(row.draw_count, len(items), new_count):
            row.status = "SATURATED"
            bust_catalogue()

    async def _stage_item(
        self, row: ProviderFetch, subject: Subject, item: dict
    ) -> bool:
        provider_id = str(item.get("id") or item.get("questionId") or "")
        text = str(item.get("question") or item.get("questionText") or "")
        options = item.get("options") or {}
        if isinstance(options, list):
            options = {chr(65 + index): value for index, value in enumerate(options)}
        digest = fingerprint(
            text, {str(key): str(value) for key, value in options.items()}
        )
        staged = ProviderQuestion(
            id=cuid(),
            fetch_id=row.id,
            provider_question_id=provider_id or digest[:16],
            fingerprint=digest,
            payload=item,
            mapper_version=MAPPER_VERSION,
        )
        answer = str(item.get("answer") or item.get("correctAnswer") or "")
        if not objective_ok(options, answer) or not text:
            staged.status = "REJECTED"
            staged.rejection_reasons = ["incomplete"]
            row.rejected_count += 1
            await provider_questions_repository.add(self.session, staged)
            return False
        question = Question(
            id=cuid(),
            subject_id=subject.id,
            exam_type=self.exam_type,
            exam_year=self.exam_year,
            question_text=text,
            question_type="OBJECTIVE",
            options=options,
            correct_answer=answer.upper(),
            explanation=(item.get("explanation") or item.get("solution") or ""),
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
    ) -> None:
        self.session = session
        self.subject_slug = subject_slug
        self.exam_type = exam_type
        self.exam_year = exam_year

    async def process(self) -> dict:
        from app.services.provider.rules import MAX_DRAWS

        last = {}
        for _ in range(MAX_DRAWS):
            last = await EnsureProviderQuestionsService(
                self.session,
                self.subject_slug,
                self.exam_type,
                self.exam_year,
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
    ) -> None:
        self.session = session
        self.subject_slug = subject_slug
        self.exam_type = exam_type
        self.exam_year = exam_year

    async def process(self) -> bool:
        key = cache_key(self.subject_slug, self.exam_type, self.exam_year)
        row = await fetches_repository.by_cache_key(self.session, "SDASH", key)
        if row and row.status == "FAILED":
            row.status = "PENDING"
            row.started_at = None
            return True
        return False


class ClearProviderBlockService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def process(self) -> bool:
        state = await load_provider_state(self.session)
        if state.circuit != "BLOCKED":
            return False
        state.circuit = "OK"
        state.cooldown_until = None
        return True
