"""LangGraph time-travel: list checkpoints, replay, and fork threads."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from app.core.errors import ApiError
from app.services.rag.checkpointer import get_checkpointer
from app.services.rag.graph import get_tutor_graph
from app.services.rag.store import get_memory_store


def _config(thread_id: str, checkpoint_id: str | None = None) -> RunnableConfig:
    configurable: dict[str, Any] = {"thread_id": thread_id}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


async def list_checkpoints(thread_id: str, *, limit: int = 30) -> list[dict]:
    checkpointer = await get_checkpointer()
    if checkpointer is None:
        raise ApiError(503, "Checkpointer is not available")
    store = await get_memory_store()
    graph = get_tutor_graph(checkpointer=checkpointer, store=store)
    config = _config(thread_id)
    history = []
    async for snapshot in graph.aget_state_history(config):
        conf = snapshot.config.get("configurable") or {}
        history.append(
            {
                "checkpointId": conf.get("checkpoint_id"),
                "next": list(snapshot.next or ()),
                "createdAt": getattr(snapshot, "created_at", None),
                "metadata": snapshot.metadata or {},
                "values": {
                    "question": (snapshot.values or {}).get("question"),
                    "route": (snapshot.values or {}).get("route"),
                    "summary": (snapshot.values or {}).get("summary"),
                    "decisionMeta": (snapshot.values or {}).get("decision_meta"),
                    "decisionTrace": (snapshot.values or {}).get("decision_trace"),
                },
            }
        )
        if len(history) >= limit:
            break
    return history


async def replay_from_checkpoint(thread_id: str, checkpoint_id: str) -> dict:
    checkpointer = await get_checkpointer()
    if checkpointer is None:
        raise ApiError(503, "Checkpointer is not available")
    store = await get_memory_store()
    graph = get_tutor_graph(checkpointer=checkpointer, store=store)
    config = _config(thread_id, checkpoint_id)
    result = await graph.ainvoke(None, config)
    return {
        "ok": True,
        "checkpointId": checkpoint_id,
        "route": result.get("route"),
        "question": result.get("question"),
    }


async def fork_from_checkpoint(
    thread_id: str,
    checkpoint_id: str,
    *,
    question: str | None = None,
) -> dict:
    """Branch from a prior checkpoint; optionally inject a new user question."""
    checkpointer = await get_checkpointer()
    if checkpointer is None:
        raise ApiError(503, "Checkpointer is not available")
    store = await get_memory_store()
    graph = get_tutor_graph(checkpointer=checkpointer, store=store)
    config = _config(thread_id, checkpoint_id)
    update: dict[str, Any] = {}
    if question:
        update = {
            "question": question,
            "messages": [HumanMessage(content=question)],
            "documents": [],
            "citations": [],
            "rewrite_count": 0,
            "route": None,
        }
    fork_config = await graph.aupdate_state(config, update)
    result = await graph.ainvoke(None, fork_config)
    new_id = (fork_config.get("configurable") or {}).get("checkpoint_id")
    return {
        "ok": True,
        "forkedFrom": checkpoint_id,
        "checkpointId": new_id,
        "route": result.get("route"),
        "question": result.get("question"),
    }
