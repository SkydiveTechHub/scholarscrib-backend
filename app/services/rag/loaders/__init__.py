from app.services.rag.loaders.lessons import lesson_documents
from app.services.rag.loaders.materials import material_documents_from_text
from app.services.rag.loaders.questions import question_documents

__all__ = [
    "lesson_documents",
    "material_documents_from_text",
    "question_documents",
]
