"""Shared shapes for RAG documents and citations."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

SourceType = Literal["lesson", "question", "material"]


class ChunkMetadata(TypedDict, total=False):
    doc_id: str
    source_type: SourceType
    subject_id: str
    topic_id: str
    lesson_id: str
    question_id: str
    resource_id: str
    exam_type: str
    chunk_index: int
    title: str
    content_hash: str


class GroundedCitation(TypedDict, total=False):
    sourceType: SourceType
    docId: str
    subjectId: str
    topicId: str
    lessonId: str
    questionId: str
    resourceId: str
    title: str
    excerpt: str


def citation_from_metadata(meta: dict[str, Any], excerpt: str = "") -> GroundedCitation:
    return GroundedCitation(
        sourceType=meta.get("source_type") or meta.get("sourceType"),  # type: ignore[typeddict-item]
        docId=str(meta.get("doc_id") or meta.get("docId") or ""),
        subjectId=str(meta.get("subject_id") or meta.get("subjectId") or ""),
        topicId=str(meta.get("topic_id") or meta.get("topicId") or ""),
        lessonId=str(meta.get("lesson_id") or meta.get("lessonId") or ""),
        questionId=str(meta.get("question_id") or meta.get("questionId") or ""),
        resourceId=str(meta.get("resource_id") or meta.get("resourceId") or ""),
        title=str(meta.get("title") or ""),
        excerpt=(excerpt or "")[:280],
    )
