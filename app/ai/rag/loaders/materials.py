"""Turn extracted material text into RAG Documents."""

from __future__ import annotations

from langchain_core.documents import Document

from app.ai.rag.chunking import split_if_needed
from app.database.models import SubjectResource


def material_documents_from_text(
    resource: SubjectResource, text: str
) -> list[Document]:
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    meta = {
        "doc_id": f"material:{resource.id}",
        "source_type": "material",
        "subject_id": resource.subject_id,
        "resource_id": resource.id,
        "title": resource.title,
        "chunk_index": 0,
    }
    header = f"Material: {resource.title}\n\n"
    if resource.description:
        header += f"{resource.description}\n\n"
    return split_if_needed(Document(page_content=header + cleaned, metadata=meta))
