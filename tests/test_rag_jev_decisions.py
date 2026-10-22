"""Unit tests for Jev decision routing with a mocked TypeSafeClassifier."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage

from app.services.rag import decisions as decisions_mod
from app.services.rag.graph import (
    classify_intent_node,
    grade_documents_node,
    ground_answer_node,
    remember_node,
)


def _choice_response(choice: str, confidence: float):
    answer = SimpleNamespace(
        type="choice",
        choice=choice,
        confidence=confidence,
        probabilities={choice: confidence},
    )
    return SimpleNamespace(
        choices={"intent": answer},
        nouls={},
        answers={"intent": answer},
    )


def _noul_response(mapping: dict[str, float]):
    nouls = {
        key: SimpleNamespace(type="noul", noul=value) for key, value in mapping.items()
    }
    return SimpleNamespace(nouls=nouls, choices={}, answers=nouls)


@pytest.fixture
def jev_on(monkeypatch):
    monkeypatch.setattr(decisions_mod.settings, "jev_enabled", True)
    monkeypatch.setattr(decisions_mod.settings, "typesafe_api_key", "test-key")
    monkeypatch.setattr(decisions_mod.settings, "jev_confidence_threshold", 0.7)
    monkeypatch.setattr(decisions_mod.settings, "jev_grade_noul_threshold", 0.55)
    from app.core import config

    monkeypatch.setattr(config.settings, "jev_enabled", True)
    monkeypatch.setattr(config.settings, "typesafe_api_key", "test-key")
    monkeypatch.setattr(config.settings, "jev_confidence_threshold", 0.7)
    monkeypatch.setattr(config.settings, "jev_grade_noul_threshold", 0.55)


@pytest.fixture
def mock_classifier(monkeypatch, jev_on):
    classifier = MagicMock()
    classifier.ainvoke = AsyncMock()
    monkeypatch.setattr(decisions_mod, "get_jev_classifier", lambda: classifier)
    return classifier


@pytest.mark.asyncio
async def test_intent_choice_maps_to_route(mock_classifier):
    mock_classifier.ainvoke.return_value = _choice_response("sql", 0.92)
    out = await classify_intent_node(
        {
            "messages": [HumanMessage(content="How many topics in Biology?")],
            "question": "Explain mitosis stages carefully with examples",
        }
    )
    # heuristic may not match; Jev should set rag/sql from mock
    assert out["route"] == "sql"
    assert out["decision_meta"]["source"] == "jev"
    assert out["decision_meta"]["confidence"] == 0.92
    assert out["decision_trace"]


@pytest.mark.asyncio
async def test_intent_low_confidence_falls_back_to_llm(mock_classifier, monkeypatch):
    mock_classifier.ainvoke.return_value = _choice_response("chitchat", 0.4)
    monkeypatch.setattr("app.services.rag.graph._llm_intent_route", lambda _q: "rag")
    out = await classify_intent_node(
        {
            "messages": [],
            "question": "Walk me through Newton second law applications",
        }
    )
    assert out["route"] == "rag"
    assert out["decision_meta"]["source"] == "llm"


@pytest.mark.asyncio
async def test_grade_documents_filters_by_noul(mock_classifier):
    mock_classifier.ainvoke.return_value = _noul_response(
        {"doc_0": 0.9, "doc_1": 0.1, "doc_2": 0.6}
    )
    docs = [
        Document(page_content="relevant osmosis notes", metadata={"doc_id": "a"}),
        Document(page_content="unrelated history", metadata={"doc_id": "b"}),
        Document(page_content="semi useful", metadata={"doc_id": "c"}),
    ]
    out = await grade_documents_node(
        {
            "question": "What is osmosis?",
            "documents": docs,
            "rewrite_count": 0,
        }
    )
    kept_ids = [d.metadata["doc_id"] for d in out["documents"]]
    assert kept_ids == ["a", "c"]
    assert out["decision_meta"]["source"] == "jev"


@pytest.mark.asyncio
async def test_grade_skip_rewrite_when_noul_says_no(mock_classifier):
    # First call: doc grades all low; second: rewrite gate
    mock_classifier.ainvoke.side_effect = [
        _noul_response({"doc_0": 0.1}),
        _noul_response({"rewrite": 0.2}),
    ]
    out = await grade_documents_node(
        {
            "question": "Obscure niche question",
            "documents": [Document(page_content="noise", metadata={})],
            "rewrite_count": 0,
        }
    )
    assert out["documents"] == []
    assert out["skip_rewrite"] is True


@pytest.mark.asyncio
async def test_groundedness_drops_citations_not_answer(mock_classifier):
    mock_classifier.ainvoke.return_value = _noul_response({"grounded": 0.2})
    answer = "The capital of France is Paris and cats fly."
    out = await ground_answer_node(
        {
            "route": "rag",
            "question": "What is osmosis?",
            "documents": [
                Document(page_content="Osmosis is water movement.", metadata={})
            ],
            "messages": [AIMessage(content=answer)],
            "citations": [{"docId": "x"}],
        }
    )
    assert "messages" not in out
    assert out["citations"] == []
    assert out["decision_meta"]["noul"] == 0.2


@pytest.mark.asyncio
async def test_remember_skipped_when_noul_low(mock_classifier, monkeypatch):
    mock_classifier.ainvoke.return_value = _noul_response({"remember": 0.1})
    store = MagicMock()
    store.aput = AsyncMock()
    monkeypatch.setattr(
        "app.services.rag.graph.get_memory_store",
        lambda: store,
    )
    grader = MagicMock()
    monkeypatch.setattr("app.services.rag.graph.get_grader_model", lambda: grader)
    out = await remember_node(
        {
            "user_id": "user-1",
            "route": "rag",
            "question": "Explain osmosis",
            "messages": [AIMessage(content="Osmosis is diffusion of water.")],
        }
    )
    assert out["decision_meta"]["source"] == "jev"
    grader.invoke.assert_not_called()
    store.aput.assert_not_called()


@pytest.mark.asyncio
async def test_heuristic_still_preferred(mock_classifier):
    out = await classify_intent_node({"messages": [], "question": "Hello!"})
    assert out["route"] == "chitchat"
    assert out["decision_meta"]["source"] == "heuristic"
    mock_classifier.ainvoke.assert_not_called()
