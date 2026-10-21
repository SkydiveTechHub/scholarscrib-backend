"""Skip re-embedding unchanged chunk text (content-hash cache)."""

from __future__ import annotations

from langchain_core.documents import Document

# Process-local set of hashes recently upserted. Durable stores can replace this.
_seen: set[str] = set()
_CAP = 50_000


def filter_unchanged(documents: list[Document]) -> list[Document]:
    """Drop docs whose content_hash was already upserted in this process."""
    out: list[Document] = []
    for doc in documents:
        digest = str(doc.metadata.get("content_hash") or "")
        if digest and digest in _seen:
            continue
        out.append(doc)
        if digest:
            _seen.add(digest)
    if len(_seen) > _CAP:
        for key in list(_seen)[: len(_seen) // 2]:
            _seen.discard(key)
    return out


def clear_embed_cache() -> None:
    _seen.clear()
