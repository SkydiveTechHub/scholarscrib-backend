"""Zilliz Cloud (Milvus) vector store helpers."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain_core.documents import Document
from langchain_milvus import Milvus

from app.core.config import settings
from app.core.errors import ApiError
from app.services.rag.llm import get_embeddings


def rag_ready() -> bool:
    return settings.rag_enabled


def require_rag() -> None:
    if not settings.ai_tutor_enabled:
        raise ApiError(503, "AI tutor is disabled")
    if not (settings.zilliz_uri and settings.zilliz_token):
        raise ApiError(503, "Vector store is not configured")


@lru_cache(maxsize=1)
def get_vectorstore() -> Milvus:
    require_rag()
    return Milvus(
        embedding_function=get_embeddings(),
        connection_args={
            "uri": settings.zilliz_uri,
            "token": settings.zilliz_token,
        },
        collection_name=settings.milvus_collection,
        auto_id=False,
        drop_old=False,
    )


def clear_vectorstore_cache() -> None:
    get_vectorstore.cache_clear()


def upsert_documents(documents: list[Document]) -> int:
    """Upsert by stable doc_id. Callers that delete-then-reindex must not skip."""
    if not documents:
        return 0
    store = get_vectorstore()
    ids = [
        str(doc.metadata.get("doc_id") or f"anon-{i}")
        for i, doc in enumerate(documents)
    ]
    store.add_documents(documents=documents, ids=ids)
    return len(documents)


def delete_by_expr(expr: str) -> None:
    store = get_vectorstore()
    store.delete(expr=expr)


def similarity_search(
    query: str,
    *,
    k: int | None = None,
    expr: str | None = None,
) -> list[Document]:
    store = get_vectorstore()
    kwargs: dict[str, Any] = {"k": k or settings.rag_retrieve_k}
    if expr:
        kwargs["expr"] = expr
    return store.similarity_search(query, **kwargs)


def similarity_search_with_score(
    query: str,
    *,
    k: int | None = None,
    expr: str | None = None,
) -> list[tuple[Document, float]]:
    store = get_vectorstore()
    kwargs: dict[str, Any] = {"k": k or settings.rag_retrieve_k}
    if expr:
        kwargs["expr"] = expr
    return store.similarity_search_with_score(query, **kwargs)
