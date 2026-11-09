"""The provider-neutral catalogue the past-question picker reads.

Students never see a provider's own listing. Subjects are ours (``Subject``
flagged for an exam), and the papers on offer for one subject are merged from
three places, each answering a different question:

* the bank (``Question`` rows): what we hold, with real counts;
* ``ProviderCatalogue`` rows: papers the active provider is known to have,
  recorded by earlier discovery or by any paper ingested since;
* the provider adapter: what the provider says today.

A provider that can list a subject's years in one cheap call (ALOC) is asked
every time. One that can only be probed, a call per year (SDash), is asked once
per ``(provider, subject, exam)`` every 30 days, under a single-flight lock so
a crowd opening the same subject pays for one discovery, and what it finds is
stored for good in ``ProviderCatalogue``.
"""

import asyncio
import logging
import secrets
from datetime import timedelta

from redis.exceptions import RedisError
from redis_fastapi import get_settings
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.database.models import Subject
from app.database.repositories.curriculum import subjects_repository
from app.database.repositories.provider import catalogues_repository
from app.database.repositories.question import questions_repository
from app.services.provider.base import QuestionProvider
from app.services.provider.cache import AsyncRedisClient
from app.services.provider.factory import ProviderFactory
from app.services.provider.mapping import exam_type_for

logger = logging.getLogger(__name__)

# How long a finished discovery is trusted before a subject is probed again.
DISCOVERY_MARKER_TTL = int(timedelta(days=30).total_seconds())
# A discovery that has not finished by now is answered with what is known.
DISCOVERY_BUDGET_SECONDS = 8.0
# Longer than any single discovery can run, short enough to heal a crashed one.
DISCOVERY_LOCK_TTL = 120
DISCOVERY_POLL_SECONDS = 0.25

_RELEASE_LOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
"""

EXAM_KEYS = {"JAMB": "jamb", "WAEC": "waec", "NECO": "neco"}
EXAM_FLAGS = {"jamb": "is_jamb", "waec": "is_waec", "neco": "is_neco"}


def exam_key(exam: str) -> str:
    """Our lower-case exam key, or a 400 for a board we have no papers for."""
    exam_type = exam_type_for(exam)
    key = EXAM_KEYS.get(exam_type or "")
    if key is None:
        raise ApiError(400, "This exam isn't available for past papers.")
    return key


def _exam_types(subject: Subject) -> list[str]:
    return [key for key, flag in EXAM_FLAGS.items() if getattr(subject, flag)]


class CatalogueSubjectsService:
    """Our subjects for one exam, narrowed to those the provider also holds."""

    def __init__(
        self,
        session: AsyncSession,
        exam: str | None = None,
        provider: QuestionProvider | None = None,
    ) -> None:
        self.session = session
        self.exam = exam_key(exam) if exam else None
        self.provider = provider or ProviderFactory.create()

    async def process(self) -> dict:
        subjects = await subjects_repository.active_ordered(self.session)
        if self.exam:
            flag = EXAM_FLAGS[self.exam]
            subjects = [subject for subject in subjects if getattr(subject, flag)]
        else:
            subjects = [subject for subject in subjects if _exam_types(subject)]
        available = await self._provider_subjects()
        if available:
            offered = [subject for subject in subjects if subject.slug in available]
            # A provider whose names we cannot match must not empty the picker.
            subjects = offered or subjects
        return {
            "provider": self.provider.name,
            "data": [
                {
                    "slug": subject.slug,
                    "name": subject.name,
                    "displayName": subject.name,
                    "category": subject.track_category,
                    "examTypes": _exam_types(subject),
                }
                for subject in subjects
            ],
        }

    async def _provider_subjects(self) -> set[str] | None:
        if not self.provider.configured:
            return None
        try:
            return await self.provider.catalogue_subjects(self.exam)
        except Exception:  # noqa: BLE001 - a provider fault must not break the picker
            logger.warning("Provider subject listing failed", exc_info=True)
            return None


class CatalogueYearsService:
    """The papers on offer for one subject and exam, newest first."""

    def __init__(
        self,
        session: AsyncSession,
        redis: AsyncRedisClient,
        subject_key: str,
        exam: str,
        provider: QuestionProvider | None = None,
        *,
        budget_seconds: float = DISCOVERY_BUDGET_SECONDS,
    ) -> None:
        self.session = session
        self.redis = redis
        self.subject_key = subject_key
        self.exam = exam_key(exam)
        self.exam_type = exam_type_for(exam) or ""
        self.provider = provider or ProviderFactory.create()
        self.budget = budget_seconds

    async def process(self) -> dict:
        subject = await subjects_repository.by_id_or_slug(
            self.session, self.subject_key
        )
        if subject is None:
            raise ApiError(404, "Subject not found")
        papers: dict[int, dict] = {}
        for (
            _board,
            year,
            _subject,
            count,
        ) in await questions_repository.past_paper_groups(
            self.session, self.exam_type, subject.id
        ):
            papers[year] = {"questionCount": count, "cached": True}
        stored = await catalogues_repository.matching(
            self.session,
            self.exam_type,
            subject.id,
            provider=self.provider.name,
        )
        for row in stored:
            papers.setdefault(row.exam_year, {"questionCount": None, "cached": False})

        complete = await self._discover(
            subject, papers, {row.exam_year for row in stored}
        )
        return {
            "provider": self.provider.name,
            "subject": {"slug": subject.slug, "name": subject.name},
            "exam": self.exam,
            "complete": complete,
            "data": [
                {"year": year, **papers[year]} for year in sorted(papers, reverse=True)
            ],
        }

    async def _discover(
        self, subject: Subject, papers: dict[int, dict], known: set[int]
    ) -> bool:
        """Learn the provider's years into ``papers``. False if still unknown."""
        if not self.provider.configured:
            return False
        if self.provider.native_discovery:
            found = await self._ask(subject, known)
            self._merge(subject, papers, found)
            await self._remember(subject, found, known)
            return found is not None

        marker, lock = self._keys(subject)
        if await self._marked(marker):
            return True
        token = await self._acquire(lock)
        if token is None:
            # Someone else is discovering this pair; wait for their answer.
            return await self._wait_for(marker, subject, papers)
        progress: dict[int, int | None] = {}
        try:
            # Re-check: the winner of the previous lock may just have finished.
            if await self._marked(marker):
                return True
            found = await self._ask(subject, known, progress)
            if found is None:
                # Keep what a timed-out or partial discovery did find.
                self._merge(subject, papers, progress)
                await self._remember(subject, progress, known)
                return False
            self._merge(subject, papers, found)
            await self._remember(subject, found, known)
            await self._mark(marker)
            return True
        finally:
            await self._release(lock, token)

    async def _ask(
        self,
        subject: Subject,
        known: set[int],
        progress: dict[int, int | None] | None = None,
    ) -> dict[int, int | None] | None:
        """The adapter's answer within the time budget, ``None`` if not trustworthy."""
        try:
            return await asyncio.wait_for(
                self.provider.catalogue_years(
                    subject.slug, self.exam, known=known, progress=progress
                ),
                timeout=self.budget,
            )
        except TimeoutError:
            logger.info(
                "Catalogue discovery for %s/%s ran past %.1fs",
                subject.slug,
                self.exam,
                self.budget,
            )
        except Exception:  # noqa: BLE001 - a provider fault must not break the picker
            logger.warning("Provider year discovery failed", exc_info=True)
        return None

    def _merge(
        self,
        subject: Subject,
        papers: dict[int, dict],
        found: dict[int, int | None] | None,
    ) -> None:
        for year, count in (found or {}).items():
            paper = papers.setdefault(year, {"questionCount": None, "cached": False})
            if count is not None:
                paper["questionCount"] = count

    async def _remember(
        self,
        subject: Subject,
        found: dict[int, int | None] | None,
        known: set[int],
    ) -> None:
        fresh = set(found or {}) - known
        if fresh:
            await catalogues_repository.upsert_years(
                self.session,
                provider=self.provider.name,
                subject_id=subject.id,
                exam_type=self.exam_type,
                years=fresh,
            )
            # Committed before the 30 day marker is set (and before any waiter
            # re-reads), so "discovered" can never outlive the rows it stands for.
            await self.session.commit()

    # ----- the 30 day marker and the single-flight lock ---------------

    def _keys(self, subject: Subject) -> tuple[str, str]:
        prefix = get_settings().pattern_prefix("provider-catalogue")
        tag = f"{{{self.provider.name}:{subject.slug}:{self.exam}}}"
        return f"{prefix}:{tag}:done", f"{prefix}:{tag}:lock"

    async def _marked(self, marker: str) -> bool:
        try:
            return bool(await self.redis.exists(marker))
        except (RedisError, OSError):
            logger.warning("Catalogue marker unreadable", exc_info=True)
            return False

    async def _mark(self, marker: str) -> None:
        try:
            await self.redis.set(marker, "1", ex=DISCOVERY_MARKER_TTL)
        except (RedisError, OSError):
            logger.warning("Catalogue marker unwritable", exc_info=True)

    async def _acquire(self, lock: str) -> str | None:
        """A lock token, or ``None`` when another request holds the lock.

        If Redis itself is down the discovery runs unguarded: a few duplicate
        probes cost less than a picker that cannot open.
        """
        token = secrets.token_urlsafe(16)
        try:
            acquired = await self.redis.set(lock, token, nx=True, ex=DISCOVERY_LOCK_TTL)
        except (RedisError, OSError):
            logger.warning("Catalogue lock unavailable", exc_info=True)
            return token
        return token if acquired else None

    async def _release(self, lock: str, token: str) -> None:
        try:
            await self.redis.eval(_RELEASE_LOCK_SCRIPT, 1, lock, token)
        except (RedisError, OSError):
            logger.warning("Catalogue lock not released", exc_info=True)

    async def _wait_for(
        self, marker: str, subject: Subject, papers: dict[int, dict]
    ) -> bool:
        """Poll for the other request's finished discovery, then re-read its years."""
        deadline = asyncio.get_running_loop().time() + self.budget
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(DISCOVERY_POLL_SECONDS)
            if await self._marked(marker):
                rows = await catalogues_repository.matching(
                    self.session,
                    self.exam_type,
                    subject.id,
                    provider=self.provider.name,
                )
                for row in rows:
                    papers.setdefault(
                        row.exam_year, {"questionCount": None, "cached": False}
                    )
                return True
        return False
