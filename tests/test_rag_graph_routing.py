"""Unit tests for tutor intent routing and reranker parsing."""

from langchain_core.documents import Document

from app.ai.rag.rerank import _parse_reranker, rerank_documents
from app.ai.tutor.graph import _heuristic_route


def test_heuristic_chitchat_routes():
    assert _heuristic_route("Good morning") == "chitchat"
    assert _heuristic_route("what's the weather like") == "chitchat"
    assert _heuristic_route("Hello!") == "chitchat"


def test_heuristic_sql_routes():
    assert _heuristic_route("What subjects do you have?") == "sql"
    assert _heuristic_route("List topics in Biology") == "sql"


def test_heuristic_falls_through_for_content():
    assert _heuristic_route("Explain osmosis in plant cells") is None


def test_parse_reranker():
    assert _parse_reranker("cohere:rerank-english-v3.0") == (
        "cohere",
        "rerank-english-v3.0",
    )
    assert _parse_reranker("flashrank:ms-marco-MiniLM-L-12-v2")[0] == "flashrank"
    assert _parse_reranker("ms-marco-MiniLM-L-12-v2")[0] == "flashrank"


def test_rerank_without_model_truncates(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "reranker_model", None)
    monkeypatch.setattr(config.settings, "rag_rerank_k", 2)
    docs = [
        Document(page_content=f"doc {i}", metadata={"doc_id": str(i)}) for i in range(5)
    ]
    out = rerank_documents("query", docs)
    assert len(out) == 2
