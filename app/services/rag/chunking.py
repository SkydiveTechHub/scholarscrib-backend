"""Text splitting helpers for oversized lesson/question/material chunks."""

from __future__ import annotations

import hashlib

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

MAX_CHUNK_CHARS = 1800
CHUNK_OVERLAP = 200

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=MAX_CHUNK_CHARS,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " ", ""],
)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def split_if_needed(doc: Document) -> list[Document]:
    text = doc.page_content or ""
    if len(text) <= MAX_CHUNK_CHARS:
        meta = dict(doc.metadata)
        meta.setdefault("chunk_index", 0)
        meta["content_hash"] = content_hash(text)
        return [Document(page_content=text, metadata=meta)]

    parts = _splitter.split_text(text)
    base_id = str(doc.metadata.get("doc_id") or "doc")
    out: list[Document] = []
    for index, part in enumerate(parts):
        meta = dict(doc.metadata)
        meta["doc_id"] = f"{base_id}:{index}"
        meta["chunk_index"] = index
        meta["content_hash"] = content_hash(part)
        out.append(Document(page_content=part, metadata=meta))
    return out
