"""Deduplicated provider page ingest into ProviderQuestion / Question."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.timeutil import utcnow
from app.database.models import (
    ProviderFetch,
    ProviderQuestion,
    Question,
    Subject,
)
from app.database.repositories.curriculum import topics_repository
from app.database.repositories.provider import (
    fetches_repository,
    provider_questions_repository,
)
from app.database.repositories.question import questions_repository
from app.services.provider.base import PROVIDER_PAPER_PAGE_LIMIT, QuestionProvider
from app.services.provider.mapping import map_item
from app.services.provider.rules import MAPPER_VERSION, cache_key, fingerprint

# Past papers try this many empty provider pages before giving up.
MAX_SKIPPED_PAGES = 5


class ProviderPaperIngest:
    """Fetches one provider page and stores it, deduplicated, in our bank."""

    def __init__(
        self,
        session: AsyncSession,
        provider: QuestionProvider,
        *,
        provider_subject: str,
        subject: Subject,
        exam_type: str,
        exam_year: int,
        page_limit: int = PROVIDER_PAPER_PAGE_LIMIT,
    ) -> None:
        self.session = session
        self.provider = provider
        self.provider_subject = provider_subject
        self.subject = subject
        self.exam_type = exam_type
        self.exam_year = exam_year
        self.page_limit = page_limit

    async def page(
        self, cursor: str | None = None
    ) -> tuple[list[tuple[Question, dict]], str | None]:
        """The stored questions of one page, in paper order, and the next cursor."""
        stored, next_cursor, _credits = await self.page_result(cursor)
        return stored, next_cursor

    async def page_result(
        self, cursor: str | None = None
    ) -> tuple[list[tuple[Question, dict]], str | None, int | None]:
        """Like ``page``, also returning provider credits remaining when present."""
        result = await self.provider.search(
            subject_slug=self.provider_subject,
            exam_type=self.exam_type,
            exam_year=self.exam_year,
            limit=self.page_limit,
            cursor=cursor,
        )
        if result is None:
            raise ApiError(503, "Questions are unavailable right now.")
        fetch = await self._fetch_row()
        if result.status_code == 404:
            self._advance_checkpoint(fetch, cursor, None, has_more=False)
            return [], None, result.credits_remaining
        if result.status_code == 429:
            raise ApiError(503, "Too many question requests. Try again shortly.")
        if result.failed:
            raise ApiError(503, "Questions are unavailable right now.")
        topics = {
            topic.slug: topic.id
            for topic in await topics_repository.for_subject(
                self.session, self.subject.id
            )
        }
        stored: list[tuple[Question, dict]] = []
        for item in result.items:
            question = await self._store(fetch, item, topics)
            if question is not None:
                stored.append((question, item))
        next_cursor = result.next_cursor if result.has_more else None
        self._advance_checkpoint(
            fetch, cursor, next_cursor, has_more=bool(result.has_more and next_cursor)
        )
        return stored, next_cursor, result.credits_remaining

    async def page_with_questions(
        self, cursor: str | None = None, exclude: set[str] | frozenset = frozenset()
    ) -> tuple[list[tuple[Question, dict]], str | None]:
        """The next page that adds something, skipping pages that add nothing.

        A page whose items were all rejected (or are already on the paper)
        would otherwise end the paper early: the client stops paging once a
        page adds no questions.
        """
        for _ in range(MAX_SKIPPED_PAGES):
            stored, next_cursor = await self.page(cursor)
            fresh = [(q, item) for q, item in stored if q.id not in exclude]
            if fresh or not next_cursor:
                return fresh, next_cursor
            cursor = next_cursor
        return [], None

    async def store_items(self, items: list[dict]) -> ProviderFetch:
        """Stores items fetched elsewhere (e.g. pages walked concurrently)."""
        fetch = await self._fetch_row()
        topics = {
            topic.slug: topic.id
            for topic in await topics_repository.for_subject(
                self.session, self.subject.id
            )
        }
        for item in items:
            await self._store(fetch, item, topics)
        return fetch

    async def fetch_row(self) -> ProviderFetch:
        """This paper's fetch ledger row, created on first use."""
        return await self._fetch_row()

    def _advance_checkpoint(
        self,
        fetch: ProviderFetch,
        request_cursor: str | None,
        next_cursor: str | None,
        *,
        has_more: bool,
    ) -> None:
        """Advance the paper checkpoint only when this page is the next expected one."""
        if request_cursor != fetch.next_cursor:
            return
        fetch.checkpoint_at = utcnow()
        fetch.draw_count = (fetch.draw_count or 0) + 1
        if has_more and next_cursor:
            fetch.next_cursor = next_cursor
            if fetch.status not in {"SATURATED", "FAILED"}:
                fetch.status = "PENDING"
        else:
            fetch.next_cursor = None
            fetch.status = "SATURATED"

    async def _fetch_row(self) -> ProviderFetch:
        key = cache_key(self.subject.slug, self.exam_type, self.exam_year)
        row = await fetches_repository.by_cache_key(
            self.session, self.provider.name, key
        )
        if row is not None:
            return row
        row = ProviderFetch(
            id=cuid(),
            provider=self.provider.name,
            cache_key=key,
            subject_id=self.subject.id,
            exam_type=self.exam_type,
            exam_year=self.exam_year,
        )
        try:
            async with self.session.begin_nested():
                await fetches_repository.add(self.session, row, flush=True)
        except IntegrityError:
            existing = await fetches_repository.by_cache_key(
                self.session, self.provider.name, key
            )
            if existing is None:
                raise
            return existing
        return row

    async def _existing(
        self, fetch: ProviderFetch, provider_id: str, digest: str
    ) -> ProviderQuestion | None:
        return await provider_questions_repository.in_fetch(
            self.session, fetch.id, provider_id, digest
        )

    async def _store(
        self, fetch: ProviderFetch, item: dict, topics: dict[str, str]
    ) -> Question | None:
        if not isinstance(item, dict):
            return None
        normalized = self.provider.normalize(item)
        digest = fingerprint(normalized.text, normalized.options)
        provider_id = normalized.provider_id or digest[:16]
        mapped = map_item(
            item,
            normalized,
            subject_slug=self.subject.slug,
            expected_exam=self.exam_type,
            expected_year=self.exam_year,
            topic_slugs=set(topics),
        )
        topic_id = topics.get(mapped.topic_slug) if mapped.topic_slug else None

        existing = await self._existing(fetch, provider_id, digest)
        if existing is not None:
            return await self._reuse(existing, topic_id)

        staged = ProviderQuestion(
            id=cuid(),
            fetch_id=fetch.id,
            provider_question_id=provider_id,
            fingerprint=digest,
            payload=item,
            mapper_version=MAPPER_VERSION,
        )
        question: Question | None = None
        if mapped.ok:
            question = Question(
                id=cuid(),
                subject_id=self.subject.id,
                topic_id=topic_id,
                exam_type=mapped.exam_type,
                exam_year=mapped.exam_year,
                question_number=mapped.question_number,
                question_text=normalized.text,
                question_image_url=normalized.image_url or None,
                question_type="OBJECTIVE",
                options=normalized.options,
                correct_answer=normalized.answer.strip().upper(),
                explanation=normalized.explanation or None,
                difficulty=mapped.difficulty,
                time_estimate_seconds=mapped.time_estimate_seconds,
            )
            staged.status = "PROMOTED"
            staged.question_id = question.id
            staged.promoted_at = utcnow()
        else:
            staged.status = "REJECTED"
            staged.rejection_reasons = mapped.rejection_reasons
        try:
            async with self.session.begin_nested():
                if question is not None:
                    await questions_repository.add(self.session, question, flush=True)
                await provider_questions_repository.add(
                    self.session, staged, flush=True
                )
        except IntegrityError:
            existing = await self._existing(fetch, provider_id, digest)
            if existing is None:
                raise
            return await self._reuse(existing, topic_id)
        if question is not None:
            fetch.promoted_count += 1
        else:
            fetch.rejected_count += 1
        fetch.raw_count += 1
        return question

    async def _reuse(
        self, existing: ProviderQuestion, topic_id: str | None
    ) -> Question | None:
        if existing.status != "PROMOTED" or not existing.question_id:
            return None
        question = await questions_repository.by_id(self.session, existing.question_id)
        if question is not None and question.topic_id is None and topic_id:
            question.topic_id = topic_id
        return question
