from app.core.config import settings
from app.services.provider.aloc import AlocProvider
from app.services.provider.base import QuestionProvider
from app.services.provider.sdash import SdashProvider


class ProviderFactory:
    _registry: dict[str, type[QuestionProvider]] = {
        SdashProvider.name: SdashProvider,
        AlocProvider.name: AlocProvider,
    }

    @classmethod
    def register(cls, name: str, provider: type[QuestionProvider]) -> None:
        cls._registry[name.upper()] = provider

    @classmethod
    def names(cls) -> list[str]:
        return list(cls._registry)

    @classmethod
    def create(cls, name: str | None = None) -> QuestionProvider:
        key = (name or settings.question_provider).upper()
        provider = cls._registry.get(key)
        if provider is None:
            raise ValueError(
                f"Unknown question provider {key!r}; expected one of {cls.names()}"
            )
        return provider()
