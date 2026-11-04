"""Repositories for the SDASH question provider."""

from collections.abc import Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import cuid
from app.database.models import (
    ProviderCatalogue,
    ProviderFetch,
    ProviderQuestion,
    ProviderState,
    Question,
)
from app.database.repositories.base import BaseRepository


def paper_status_statement(provider: str):
    """One provider's dated fetch rows, for the admin coverage view.

    A paper can have a fetch row per provider, so mixing them would make its
    badge depend on whichever row came back last.
    """
    return select(
        ProviderFetch.subject_id,
        ProviderFetch.exam_type,
        ProviderFetch.exam_year,
        ProviderFetch.status,
        ProviderFetch.checkpoint_at,
    ).where(
        ProviderFetch.provider == provider,
        ProviderFetch.subject_id.is_not(None),
        ProviderFetch.exam_year.is_not(None),
    )


class ProviderFetchRepository(BaseRepository[ProviderFetch]):
    model = ProviderFetch

    async def paper_statuses(self, session: AsyncSession, provider: str) -> list[tuple]:
        result = await session.execute(paper_status_statement(provider))
        return [tuple(row) for row in result.all()]

    async def banked_paper(
        self,
        session: AsyncSession,
        subject_id: str,
        exam_type: str,
        exam_year: int,
    ) -> ProviderFetch | None:
        """The fullest finished fetch of a paper from *any* provider.

        A paper some provider has already walked to its end, with questions
        promoted, can be served from our bank without a provider call.
        """
        return await self.one(
            session,
            select(ProviderFetch)
            .where(
                ProviderFetch.subject_id == subject_id,
                ProviderFetch.exam_type == exam_type,
                ProviderFetch.exam_year == exam_year,
                ProviderFetch.status == "SATURATED",
                ProviderFetch.promoted_count > 0,
            )
            .order_by(ProviderFetch.promoted_count.desc(), ProviderFetch.id)
            .limit(1),
        )

    async def by_cache_key(
        self, session: AsyncSession, provider: str, cache_key: str
    ) -> ProviderFetch | None:
        return await self.first(
            session,
            ProviderFetch.provider == provider,
            ProviderFetch.cache_key == cache_key,
        )


class ProviderQuestionRepository(BaseRepository[ProviderQuestion]):
    model = ProviderQuestion

    async def by_question_id(
        self, session: AsyncSession, question_id: str
    ) -> ProviderQuestion | None:
        return await self.first(session, ProviderQuestion.question_id == question_id)

    async def in_fetch(
        self,
        session: AsyncSession,
        fetch_id: str,
        provider_question_id: str,
        fingerprint: str,
    ) -> ProviderQuestion | None:
        """The row already staged for this paper under either of its unique keys."""
        return await self.one(
            session,
            select(ProviderQuestion)
            .where(
                ProviderQuestion.fetch_id == fetch_id,
                or_(
                    ProviderQuestion.provider_question_id == provider_question_id,
                    ProviderQuestion.fingerprint == fingerprint,
                ),
            )
            .limit(1),
        )

    async def paper_questions(
        self, session: AsyncSession, fetch_id: str, limit: int
    ) -> list[Question]:
        """A paper's stored questions in the order they were set."""
        return list(
            (
                await session.scalars(
                    select(Question)
                    .join(ProviderQuestion, ProviderQuestion.question_id == Question.id)
                    .where(
                        ProviderQuestion.fetch_id == fetch_id,
                        ProviderQuestion.status == "PROMOTED",
                    )
                    .order_by(
                        # Rows stored before question numbers were mapped
                        # still carry the provider's number in their payload.
                        func.coalesce(
                            Question.question_number,
                            ProviderQuestion.payload["questionNumber"].as_integer(),
                        )
                        .asc()
                        .nulls_last(),
                        ProviderQuestion.promoted_at,
                    )
                    .limit(limit)
                )
            ).all()
        )

    async def promoted_count(self, session: AsyncSession, fetch_id: str) -> int:
        return await self.count(
            session,
            ProviderQuestion.fetch_id == fetch_id,
            ProviderQuestion.status == "PROMOTED",
        )

    async def payloads_for(
        self, session: AsyncSession, question_ids: list[str]
    ) -> dict[str, dict]:
        """The provider's raw item behind each stored question, by question id."""
        if not question_ids:
            return {}
        rows = await session.execute(
            select(ProviderQuestion.question_id, ProviderQuestion.payload).where(
                ProviderQuestion.question_id.in_(question_ids)
            )
        )
        return {
            row[0]: row[1] for row in rows.all() if row[0] and isinstance(row[1], dict)
        }


class ProviderCatalogueRepository(BaseRepository[ProviderCatalogue]):
    model = ProviderCatalogue

    async def matching(
        self,
        session: AsyncSession,
        exam_type: str | None = None,
        subject_id: str | None = None,
        provider: str | None = None,
    ) -> list[ProviderCatalogue]:
        criteria = []
        if exam_type:
            criteria.append(ProviderCatalogue.exam_type == exam_type)
        if subject_id:
            criteria.append(ProviderCatalogue.subject_id == subject_id)
        if provider:
            criteria.append(ProviderCatalogue.provider == provider)
        return await self.list_where(session, *criteria)

    async def upsert_years(
        self,
        session: AsyncSession,
        *,
        provider: str,
        subject_id: str,
        exam_type: str,
        years: Iterable[int],
    ) -> int:
        """Record that a provider holds these papers; years already known are kept.

        Idempotent and safe under concurrent callers: the unique key
        (provider, subject, exam, year) decides, so a race only drops the
        duplicate. Returns the number of rows written.
        """
        rows = [
            {
                "id": cuid(),
                "provider": provider,
                "subject_id": subject_id,
                "exam_type": exam_type,
                "exam_year": year,
            }
            for year in sorted(set(years))
        ]
        if not rows:
            return 0
        written = await session.scalars(
            pg_insert(ProviderCatalogue)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=[
                    ProviderCatalogue.provider,
                    ProviderCatalogue.subject_id,
                    ProviderCatalogue.exam_type,
                    ProviderCatalogue.exam_year,
                ]
            )
            .returning(ProviderCatalogue.id)
        )
        return len(written.all())


class ProviderStateRepository(BaseRepository[ProviderState]):
    model = ProviderState


fetches_repository = ProviderFetchRepository()
provider_questions_repository = ProviderQuestionRepository()
catalogues_repository = ProviderCatalogueRepository()
states_repository = ProviderStateRepository()
