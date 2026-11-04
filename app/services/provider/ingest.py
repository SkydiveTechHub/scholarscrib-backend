"""Deduplicated provider page ingest into ProviderQuestion / Question."""

import hashlib

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
    catalogues_repository,
    fetches_repository,
    provider_questions_repository,
)
from app.database.repositories.question import questions_repository
from app.services.provider.base import (
    PROVIDER_PAPER_PAGE_LIMIT,
    PaperFetchMode,
    QuestionProvider,
    SearchPage,
)
from app.services.provider.cache import (
    AsyncRedisClient,
    PaperCacheError,
    decode_page_cursor,
    encode_page_cursor,
    fetch_provider_paper,
    get_or_fill_paper,
    local_cursor_cache_id,
    paginate_paper,
    paper_cache_id,
)
from app.services.provider.mapping import map_item
from app.services.provider.rules import MAPPER_VERSION, cache_key, fingerprint

# Past papers try this many empty provider pages before giving up.
MAX_SKIPPED_PAGES = 5
# A paper in our bank is read whole and paged locally; none is longer than this.
BANK_PAPER_LIMIT = 500
RESTART_PAPER_MESSAGE = (
    "The question source changed while you were sitting this paper. "
    "Please start it again."
)


def bank_cache_id(subject_id: str, exam_type: str, exam_year: int) -> str:
    """Names a bank-served paper inside its local pagination cursors."""
    digest = hashlib.sha256(f"{subject_id}:{exam_type}:{exam_year}".encode())
    return f"bank-{digest.hexdigest()}"


async def record_paper(
    session: AsyncSession,
    provider: str,
    subject_id: str,
    exam_type: str,
    exam_year: int,
) -> None:
    """Upsert one ``ProviderCatalogue`` row (idempotent)."""
    await catalogues_repository.upsert_years(
        session,
        provider=provider,
        subject_id=subject_id,
        exam_type=exam_type,
        years=[exam_year],
    )


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
        redis: AsyncRedisClient | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.provider_subject = provider_subject
        self.subject = subject
        self.exam_type = exam_type
        self.exam_year = exam_year
        self.page_limit = page_limit
        # With Redis, pages come from the bank or a cached snapshot when the
        # provider cannot page live; without it, always live from the provider.
        self.redis = redis
        self._catalogued = False

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
        saturate_at_end = True
        source = "live" if self.redis is None else await self._source(cursor)
        if isinstance(source, ProviderFetch):
            return await self._bank_page(source, cursor)
        if source == "snapshot":
            result, saturate_at_end = await self._snapshot_page(cursor)
        else:
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
            fetch,
            cursor,
            next_cursor,
            has_more=bool(result.has_more and next_cursor),
            saturate_at_end=saturate_at_end,
        )
        await self.record_catalogue(fetch)
        return stored, next_cursor, result.credits_remaining

    async def _source(self, cursor: str | None) -> "ProviderFetch | str":
        """Where this page comes from: a banked paper, a snapshot or the provider.

        A cursor pins the source it was issued by. One that no longer fits the
        active provider (the provider was switched mid-paper) cannot be
        continued, so the student is asked to restart the paper.
        """
        sampled = self.provider.paper_fetch_mode == PaperFetchMode.SAMPLED
        banked = await fetches_repository.banked_paper(
            self.session, self.subject.id, self.exam_type, self.exam_year
        )
        if cursor:
            owner = local_cursor_cache_id(cursor)
            if owner is None:
                if sampled:
                    raise ApiError(409, RESTART_PAPER_MESSAGE, reason="RESTART_PAPER")
                return "live"
            if banked is not None and owner == self._bank_id():
                return banked
            if sampled and owner == self._snapshot_id():
                return "snapshot"
            raise ApiError(409, RESTART_PAPER_MESSAGE, reason="RESTART_PAPER")
        if banked is not None:
            return banked
        return "snapshot" if sampled else "live"

    def _bank_id(self) -> str:
        return bank_cache_id(self.subject.id, self.exam_type, self.exam_year)

    def _snapshot_id(self) -> str:
        return paper_cache_id(
            self.provider.name, self.subject.slug, self.exam_type, self.exam_year
        )

    async def _bank_page(
        self, banked: ProviderFetch, cursor: str | None
    ) -> tuple[list[tuple[Question, dict]], str | None, int | None]:
        """One page of a paper already in our bank, served with no provider call."""
        cache_id = self._bank_id()
        try:
            offset = decode_page_cursor(cursor, cache_id) if cursor else 0
        except ValueError as error:
            raise ApiError(400, str(error)) from error
        questions = await provider_questions_repository.paper_questions(
            self.session, banked.id, BANK_PAPER_LIMIT
        )
        if offset > len(questions):
            raise ApiError(400, "Invalid question pagination cursor")
        end = min(offset + self.page_limit, len(questions))
        page = questions[offset:end]
        payloads = await provider_questions_repository.payloads_for(
            self.session, [question.id for question in page]
        )
        stored = [(question, payloads.get(question.id, {})) for question in page]
        next_cursor = (
            encode_page_cursor(cache_id, end) if end < len(questions) else None
        )
        return stored, next_cursor, None

    async def _snapshot_page(
        self, cursor: str | None
    ) -> tuple[SearchPage | None, bool]:
        """One page of the provider's cached sample, and whether its end is final."""
        assert self.redis is not None
        cache_id = self._snapshot_id()

        async def load():
            return await fetch_provider_paper(
                self.provider,
                subject_slug=self.subject.slug,
                exam_type=self.exam_type,
                exam_year=self.exam_year,
            )

        try:
            paper = await get_or_fill_paper(self.redis, cache_id=cache_id, loader=load)
        except PaperCacheError as error:
            raise ApiError(503, str(error)) from error
        if not paper.cacheable or (paper.failed and paper.status_code != 404):
            if paper.status_code == 429:
                raise ApiError(503, "Too many question requests. Try again shortly.")
            raise ApiError(503, "Questions are unavailable right now.")
        try:
            page = paginate_paper(
                paper, cache_id=cache_id, limit=self.page_limit, cursor=cursor
            )
        except ValueError as error:
            raise ApiError(400, str(error)) from error
        pagination = page["pagination"]
        return (
            SearchPage(
                items=page["questions"],
                next_cursor=pagination["nextCursor"],
                has_more=pagination["hasMore"],
                status_code=404 if paper.status_code == 404 else 200,
                credits_remaining=paper.credits_remaining,
            ),
            # A sample's end is only the end of what was drawn, not of the paper.
            paper.complete,
        )

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
        await self.record_catalogue(fetch)
        return fetch

    async def record_catalogue(self, fetch: ProviderFetch) -> None:
        """Note that the provider has this paper, once it has yielded questions.

        Whatever a student sits or a backfill walks proves the paper exists, so
        the picker's catalogue heals itself without any discovery calls.
        """
        if self._catalogued or (fetch.promoted_count or 0) <= 0:
            return
        await record_paper(
            self.session,
            self.provider.name,
            self.subject.id,
            self.exam_type,
            self.exam_year,
        )
        self._catalogued = True

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
        saturate_at_end: bool = True,
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
            if saturate_at_end:
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
