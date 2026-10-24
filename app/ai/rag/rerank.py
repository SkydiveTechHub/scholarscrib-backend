"""Document reranking: Flashrank (local) or Cohere when configured."""

from __future__ import annotations

from functools import lru_cache

from langchain_core.documents import Document

from app.ai.core.llm import _export_provider_keys
from app.core.config import settings
from app.core.logger import logger


def _parse_reranker(spec: str) -> tuple[str, str]:
    value = (spec or "").strip()
    if ":" in value:
        provider, _, model = value.partition(":")
        return provider.lower(), model
    # Bare model name → flashrank by default (no API key)
    return "flashrank", value or "ms-marco-MiniLM-L-12-v2"


@lru_cache(maxsize=4)
def _flashrank(model: str):
    from flashrank import Ranker
    from langchain_community.document_compressors import FlashrankRerank

    # client is required in the type stub but normally built in a validator.
    return FlashrankRerank(
        client=Ranker(model_name=model),
        model=model,
        top_n=settings.rag_rerank_k,
    )


@lru_cache(maxsize=4)
def _cohere(model: str):
    _export_provider_keys()
    from langchain_cohere import CohereRerank

    return CohereRerank(model=model, top_n=settings.rag_rerank_k)


def clear_reranker_cache() -> None:
    _flashrank.cache_clear()
    _cohere.cache_clear()


def rerank_documents(
    query: str, documents: list[Document], *, top_k: int | None = None
) -> list[Document]:
    k = top_k or settings.rag_rerank_k
    if not documents:
        return []
    if not settings.reranker_model:
        return documents[:k]

    provider, model = _parse_reranker(settings.reranker_model)
    try:
        if provider in ("flashrank", "local"):
            compressor = _flashrank(model)
            return list(compressor.compress_documents(documents, query))[:k]
        if provider == "cohere":
            compressor = _cohere(model)
            return list(compressor.compress_documents(documents, query))[:k]
        logger.warning("Unknown reranker provider %s; truncating", provider)
    except Exception:  # noqa: BLE001
        logger.exception("Rerank failed (%s:%s); truncating", provider, model)
    return documents[:k]
