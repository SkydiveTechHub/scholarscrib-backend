"""Turn lesson rows into RAG Documents."""

from __future__ import annotations

from typing import Any

from langchain_core.documents import Document

from app.ai.rag.chunking import split_if_needed
from app.database.models import Lesson


def _block_text(block: dict[str, Any]) -> str:
    parts: list[str] = []
    kind = block.get("type") or block.get("kind") or "block"
    title = block.get("title") or block.get("heading")
    if title:
        parts.append(str(title))
    for key in (
        "body",
        "text",
        "content",
        "prompt",
        "answer",
        "explanation",
        "wrong",
        "right",
        "mnemonic",
        "tip",
        "example",
    ):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif isinstance(value, list):
            parts.extend(str(item) for item in value if item)
    if not parts and block:
        parts.append(str(block))
    return f"[{kind}] " + "\n".join(parts)


def _list_texts(values: list | None, label: str) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for item in values:
        if isinstance(item, str) and item.strip():
            out.append(f"[{label}] {item.strip()}")
        elif isinstance(item, dict):
            text = _block_text(item)
            if text.strip():
                out.append(f"[{label}] {text}")
    return out


def lesson_documents(
    lesson: Lesson,
    *,
    subject_id: str,
    topic_id: str,
    topic_title: str | None = None,
) -> list[Document]:
    docs: list[Document] = []
    base_meta = {
        "source_type": "lesson",
        "subject_id": subject_id,
        "topic_id": topic_id,
        "lesson_id": lesson.id,
        "title": lesson.title or topic_title or "Lesson",
    }

    blocks = lesson.blocks if isinstance(lesson.blocks, list) else []
    if blocks:
        for index, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            text = _block_text(block).strip()
            if not text:
                continue
            meta = {
                **base_meta,
                "doc_id": f"lesson:{lesson.id}:block:{index}",
                "chunk_index": index,
            }
            docs.extend(split_if_needed(Document(page_content=text, metadata=meta)))
    elif lesson.content:
        meta = {
            **base_meta,
            "doc_id": f"lesson:{lesson.id}:content",
            "chunk_index": 0,
        }
        docs.extend(
            split_if_needed(Document(page_content=lesson.content, metadata=meta))
        )

    extras = (
        _list_texts(lesson.key_points, "key_point")
        + _list_texts(lesson.exam_tips, "exam_tip")
        + _list_texts(lesson.mnemonics, "mnemonic")
        + _list_texts(lesson.worked_examples, "worked_example")
    )
    for index, text in enumerate(extras):
        meta = {
            **base_meta,
            "doc_id": f"lesson:{lesson.id}:extra:{index}",
            "chunk_index": 10_000 + index,
        }
        docs.extend(split_if_needed(Document(page_content=text, metadata=meta)))

    return docs
