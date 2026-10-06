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
    Topic,
)
from app.database.repositories.curriculum import subjects_repository, topics_repository
from app.database.repositories.provider import (
    fetches_repository,
    provider_questions_repository,
    states_repository,
)
from app.database.repositories.question import questions_repository
from app.services.catalogue import bust_catalogue, public_question
from app.services.provider.base import (
    DiscoveryResource,
    DrawResult,
    ProviderNotFound,
    QuestionProvider,
    SearchPage,
)
from app.services.provider.factory import ProviderFactory
from app.services.provider.mapping import map_item, map_topic, provider_topic_filters
from app.services.provider.rules import (
    COOLDOWN_MINUTES,
    LEASE_WINDOW_MS,
    MAPPER_VERSION,
    MAX_DRAWS,
    cache_key,
    fingerprint,
    next_circuit,
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
        topics = {
            topic.slug: topic.id
            for topic in await topics_repository.for_subject(self.session, subject.id)
        }
        new_count = 0
        for item in result.items:
            if not isinstance(item, dict):
                continue
            if await self._stage_item(row, subject, item, topics):
                new_count += 1
        row.draw_count += 1
        row.raw_count += len(result.items)
        row.new_in_last_draw = new_count
        if not result.failed and self.provider.should_saturate(row, result, new_count):
            row.status = "SATURATED"
            bust_catalogue()

    async def _stage_item(
        self, row: ProviderFetch, subject: Subject, item: dict, topics: dict[str, str]
    ) -> bool:
        normalized = self.provider.normalize(item)
        digest = fingerprint(normalized.text, normalized.options)
        provider_id = normalized.provider_id or digest[:16]
        # Already stored for this paper (an earlier draw, or a student sitting
        # it as a past paper): staging it again would break the fetch's unique
        # keys and roll back the whole draw.
        if await provider_questions_repository.in_fetch(
            self.session, row.id, provider_id, digest
        ):
            return False
        staged = ProviderQuestion(
            id=cuid(),
            fetch_id=row.id,
            provider_question_id=provider_id,
            fingerprint=digest,
            payload=item,
            mapper_version=MAPPER_VERSION,
        )
        mapped = map_item(
            item,
            normalized,
            subject_slug=subject.slug,
            expected_exam=self.exam_type,
            expected_year=self.exam_year,
            topic_slugs=set(topics),
        )
        if not mapped.ok:
            staged.status = "REJECTED"
            staged.rejection_reasons = mapped.rejection_reasons
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
            topic_id=topics.get(mapped.topic_slug) if mapped.topic_slug else None,
            exam_type=mapped.exam_type,
            exam_year=mapped.exam_year,
            question_number=mapped.question_number,
            question_text=normalized.text,
            question_image_url=normalized.image_url or None,
            question_type="OBJECTIVE",
            options=normalized.options,
            correct_answer=normalized.answer.strip().upper(),
            explanation=explanation,
            difficulty=mapped.difficulty,
            time_estimate_seconds=mapped.time_estimate_seconds,
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
            "questions": [
                provider_question(
                    self.provider, item, subject, self.exam_type, self.exam_year
                )
                for item in page.items
            ],
            "pagination": {
                "limit": self.limit,
                "cursor": self.cursor,
                "nextCursor": page.next_cursor,
                "hasMore": page.has_more,
            },
        }


def provider_question(
    provider: QuestionProvider,
    item: dict,
    subject: Subject | None,
    exam_type: str | None,
    exam_year: int | None,
) -> dict:
    """One provider item shaped like a bank question, answer included."""
    normalized = provider.normalize(item)
    exam_type = item.get("examType") or exam_type
    payload = {
        "id": normalized.provider_id,
        "subjectId": subject.id if subject else None,
        "topicId": None,
        "examType": str(exam_type).upper() if exam_type else None,
        "examYear": item.get("year") or exam_year,
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


class TopicQuizQuestionsService:
    """A topic's quiz questions: our bank first, then the provider.

    Each exam is tried in turn (the requested one, then JAMB): the bank's
    topic-tagged questions are drawn first, and the provider is asked for the
    rest under the reviewed ALOC topics that map onto ours. A provider item
    whose own classification maps to a different topic of ours is dropped.
    Answers are included: the quiz is a self-check graded in the browser.
    """

    FALLBACK_EXAM = "JAMB"

    def __init__(
        self,
        session: AsyncSession,
        *,
        subject_key: str,
        topic_key: str,
        exam_type: str,
        limit: int,
        random: bool,
        provider: QuestionProvider | None = None,
    ) -> None:
        self.session = session
        self.subject_key = subject_key
        self.topic_key = topic_key
        self.exam_type = exam_type
        self.limit = limit
        self.random = random
        self.provider = provider or ProviderFactory.create()

    async def process(self) -> dict:
        subject = await subjects_repository.by_id_or_slug(
            self.session, self.subject_key
        )
        if subject is None:
            raise ApiError(404, "Subject not found")
        topic = await topics_repository.by_slug(
            self.session, subject.id, self.topic_key
        ) or await topics_repository.by_id(self.session, self.topic_key)
        if topic is None or topic.subject_id != subject.id:
            raise ApiError(404, "Topic not found")
        topic_slugs = {
            row.slug
            for row in await topics_repository.for_subject(self.session, subject.id)
        }
        exams = list(dict.fromkeys([self.exam_type, self.FALLBACK_EXAM]))
        for exam_type in exams:
            questions = await self._for_exam(subject, topic, exam_type, topic_slugs)
            if questions:
                return {
                    "questions": questions[: self.limit],
                    "examType": exam_type,
                    "requestedExamType": self.exam_type,
                }
        return {
            "questions": [],
            "examType": None,
            "requestedExamType": self.exam_type,
        }

    async def _for_exam(
        self, subject: Subject, topic: Topic, exam_type: str, topic_slugs: set[str]
    ) -> list[dict]:
        stored = await questions_repository.pick_objective(
            self.session,
            subject_id=subject.id,
            count=self.limit,
            topic_ids=[topic.id],
            exam_type=exam_type,
        )
        out = [public_question(row, include_answers=True) for row in stored]
        if len(out) >= self.limit or not self.provider.configured:
            return out
        seen = {question["questionText"] for question in out}
        for aloc_topic, aloc_subtopic in provider_topic_filters(
            subject.slug, topic.slug
        ):
            page = await self.provider.search(
                subject_slug=subject.slug,
                exam_type=exam_type,
                exam_year=None,
                limit=self.limit - len(out),
                topic=aloc_topic,
                subtopic=aloc_subtopic,
                random=self.random,
            )
            if page is None or page.failed:
                continue
            for item in page.items:
                metadata = item.get("metadata")
                if isinstance(metadata, dict) and metadata.get("topic"):
                    mapped = map_topic(subject.slug, metadata, topic_slugs)
                    if mapped is not None and mapped != topic.slug:
                        continue
                question = provider_question(
                    self.provider, item, subject, exam_type, None
                )
                if not question["questionText"] or question["questionText"] in seen:
                    continue
                question["topicId"] = topic.id
                seen.add(question["questionText"])
                out.append(question)
                if len(out) >= self.limit:
                    return out
        return out


class GetQuestionExplanationService:
    """Explain a bank question id or a provider question id.

    Bank questions that already carry an explanation never reach the provider;
    a bought explanation is written back so the same question is paid for once.
    """

    def __init__(
        self,
        session: AsyncSession,
        question_id: str,
        provider: QuestionProvider | None = None,
    ) -> None:
        self.session = session
        self.question_id = question_id
        self.provider = provider or ProviderFactory.create()

    async def process(self) -> dict:
        question = await questions_repository.by_id(self.session, self.question_id)
        if question is not None and question.explanation:
            return self._payload(question.explanation, source="BANK")
        provider_id = self.question_id
        if question is not None:
            staged = await provider_questions_repository.by_question_id(
                self.session, question.id
            )
            if staged is None:
                raise ApiError(404, "No explanation is available for this question.")
            provider_id = staged.provider_question_id
        if not self.provider.configured:
            raise ApiError(503, "Explanations are unavailable right now.")
        bought = await self.provider.explain(provider_id)
        if not bought:
            raise ApiError(503, "Explanations are unavailable right now.")
        explanation = self.provider.flatten_explanation(bought)
        if not explanation:
            raise ApiError(404, "No explanation is available for this question.")
        if question is not None:
            question.explanation = explanation
        nested = bought.get("data")
        data: dict = nested if isinstance(nested, dict) else bought
        return self._payload(
            explanation,
            source=self.provider.name,
            simplified=data.get("simplifiedExplanation"),
            mistakes=data.get("commonMistakes"),
            image_url=data.get("solutionImageUrl"),
            needs_review=data.get("needsReview"),
        )

    def _payload(
        self,
        explanation: str,
        *,
        source: str,
        simplified: str | None = None,
        mistakes: list | None = None,
        image_url: str | None = None,
        needs_review: bool | None = None,
    ) -> dict:
        return {
            "questionId": self.question_id,
            "explanation": explanation,
            "simplifiedExplanation": simplified,
            "commonMistakes": mistakes or [],
            "solutionImageUrl": image_url,
            "needsReview": bool(needs_review),
            "source": source,
        }


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
