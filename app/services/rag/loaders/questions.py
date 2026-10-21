"""Turn question rows into RAG Documents."""

from __future__ import annotations

from langchain_core.documents import Document

from app.database.models import Question
from app.services.rag.chunking import split_if_needed


def _options_text(options: dict | list | None) -> str:
    if options is None:
        return ""
    if isinstance(options, dict):
        return "\n".join(f"{key}. {value}" for key, value in options.items() if value)
    if isinstance(options, list):
        lines: list[str] = []
        for item in options:
            if isinstance(item, dict):
                key = item.get("key") or item.get("label") or ""
                value = item.get("text") or item.get("value") or ""
                lines.append(f"{key}. {value}".strip(". "))
            else:
                lines.append(str(item))
        return "\n".join(lines)
    return str(options)


def question_documents(question: Question) -> list[Document]:
    parts = [
        f"Exam: {question.exam_type}"
        + (f" {question.exam_year}" if question.exam_year else ""),
        f"Question: {question.question_text}",
    ]
    options = _options_text(question.options)
    if options:
        parts.append(f"Options:\n{options}")
    parts.append(f"Correct answer: {question.correct_answer}")
    if question.explanation:
        parts.append(f"Explanation: {question.explanation}")

    text = "\n\n".join(parts)
    meta = {
        "doc_id": f"question:{question.id}",
        "source_type": "question",
        "subject_id": question.subject_id,
        "topic_id": question.topic_id or "",
        "question_id": question.id,
        "exam_type": question.exam_type,
        "title": f"{question.exam_type} question",
        "chunk_index": 0,
    }
    return split_if_needed(Document(page_content=text, metadata=meta))
