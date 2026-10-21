"""Provider-agnostic chat and embedding factories."""

from __future__ import annotations

import os
from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

from app.core.config import settings


def _export_provider_keys() -> None:
    """LangChain provider packages read standard env vars."""
    mapping = {
        "OPENAI_API_KEY": settings.openai_api_key,
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        "GOOGLE_API_KEY": settings.google_api_key,
        "COHERE_API_KEY": getattr(settings, "cohere_api_key", None),
        "LANGSMITH_API_KEY": settings.langsmith_api_key,
        "LANGSMITH_PROJECT": settings.langsmith_project,
        "TYPESAFE_API_KEY": settings.typesafe_api_key,
    }
    for key, value in mapping.items():
        if value and not os.environ.get(key):
            os.environ[key] = value
    if settings.typesafe_base_url and not os.environ.get("TYPESAFE_BASE_URL"):
        os.environ["TYPESAFE_BASE_URL"] = settings.typesafe_base_url
    if settings.langsmith_tracing:
        os.environ.setdefault("LANGSMITH_TRACING", "true")


@lru_cache(maxsize=8)
def get_chat_model(model: str | None = None) -> BaseChatModel:
    _export_provider_keys()
    return init_chat_model(model or settings.llm_model, temperature=0.2)


@lru_cache(maxsize=8)
def get_grader_model(model: str | None = None) -> BaseChatModel:
    _export_provider_keys()
    return init_chat_model(model or settings.effective_grader_model, temperature=0)


@lru_cache(maxsize=4)
def get_embeddings(model: str | None = None) -> Embeddings:
    _export_provider_keys()
    return init_embeddings(model or settings.embedding_model)


def clear_model_caches() -> None:
    get_chat_model.cache_clear()
    get_grader_model.cache_clear()
    get_embeddings.cache_clear()
