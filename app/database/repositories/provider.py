"""Repositories for the SDASH question provider."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    ProviderCatalogue,
    ProviderFetch,
    ProviderQuestion,
    ProviderState,
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
