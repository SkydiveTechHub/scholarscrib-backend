from app.core.config import settings
from app.services.provider.aloc import AlocProvider
from app.services.provider.base import NormalizedQuestion, QuestionProvider
from app.services.provider.sdash import SdashProvider


def normalize_stored_item(item: dict) -> NormalizedQuestion:
    """Normalize a raw item kept on ``ProviderQuestion``, whichever provider made it.

    A bank row can outlive a provider switch, so the active provider's reader
    cannot be assumed. The two payload shapes are unambiguous: SDash nests its
    choices under ``option`` and dates items with ``examyear``.
    """
    if "option" in item or "examyear" in item:
        return SdashProvider(access_token="-").normalize(item)
    return AlocProvider(api_key="-").normalize(item)


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
