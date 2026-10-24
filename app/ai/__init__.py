"""ScholarsCrib AI: shared core, RAG index/retrieve, student tutor, admin Astra."""

from app.ai.core.llm import get_chat_model, get_embeddings, get_grader_model
from app.ai.rag.vectorstore import get_vectorstore, rag_ready

__all__ = [
    "get_chat_model",
    "get_embeddings",
    "get_grader_model",
    "get_vectorstore",
    "rag_ready",
]
