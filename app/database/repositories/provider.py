"""Repositories for the SDASH question provider."""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    ProviderCatalogue,
    ProviderFetch,
    ProviderQuestion,
    ProviderState,
    Question,
)
from app.database.repositories.base import BaseRepository


class ProviderFetchRepository(BaseRepository[ProviderFetch]):
    model = ProviderFetch

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
    ) -> list[ProviderCatalogue]:
        criteria = []
        if exam_type:
            criteria.append(ProviderCatalogue.exam_type == exam_type)
        if subject_id:
            criteria.append(ProviderCatalogue.subject_id == subject_id)
        return await self.list_where(session, *criteria)


class ProviderStateRepository(BaseRepository[ProviderState]):
    model = ProviderState


fetches_repository = ProviderFetchRepository()
provider_questions_repository = ProviderQuestionRepository()
catalogues_repository = ProviderCatalogueRepository()
states_repository = ProviderStateRepository()
