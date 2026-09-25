"""Repository for JAMB subject combinations."""

from app.database.models import JambCombination
from app.database.repositories.base import BaseRepository


class JambCombinationRepository(BaseRepository[JambCombination]):
    model = JambCombination


combinations_repository = JambCombinationRepository()
