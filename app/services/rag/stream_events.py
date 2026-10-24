"""Translate graph callbacks into student-safe SSE payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

_STATUS_NODES = frozenset(
    {
        "classify_intent",
        "chitchat",
        "sql_agent",
        "load_memory",
        "summarize",
        "retrieve",
        "grade_documents",
        "rewrite_question",
        "rerank",
        "generate_answer",
        "ground_answer",
        "remember",
        "pick_domain",
        "action_agent",
        "action_tools",
        "inspect_action_tools",
        "await_action_approval",
        "execute_action",
        "action_outcome",
    }
)

_TOOL_SUMMARIES = {
    "grade_documents": "Checking lesson notes…",
    "get_study_plan": "Looking at your study plan…",
    "list_subjects": "Checking subjects…",
    "get_dashboard_summary": "Checking your dashboard…",
    "get_performance_summary": "Checking your progress…",
    "get_topic_standings": "Checking your topic standings…",
    "create_study_plan": "Preparing a study plan…",
    "update_study_plan": "Preparing a study plan change…",
    "delete_study_plan": "Preparing to archive a study plan…",
    "set_study_plan_positions": "Preparing a class update…",
    "set_study_plan_item_status": "Preparing a session update…",
    "list_curriculum_tables": "Looking up the catalogue…",
    "curriculum_table_schema": "Looking up the catalogue…",
    "run_readonly_sql": "Looking up the catalogue…",
}

# Nodes that also show on the tool channel. ``grade_documents`` is the
# contract example: a short line, with no retrieved text attached.
_NODE_TOOLS = frozenset({"grade_documents"})

_STALE_AFTER_SECONDS = 45


def tool_summary(name: str) -> str:
    return _TOOL_SUMMARIES.get(name, "Working on that…")


class ToolTracker:
    """Pair each running tool with a later done or failed event."""

    def __init__(self) -> None:
        self._n = 0
        self._open: dict[str, dict] = {}
        self.records: list[dict] = []

    def start(self, key: str, name: str) -> dict:
        self._n += 1
        payload = {
            "id": f"tool_{self._n}",
            "name": name,
            "phase": "running",
            "summary": tool_summary(name),
        }
        self._open[key] = payload
        self.records.append(payload)
        return dict(payload)

    def finish(self, key: str, phase: str) -> dict | None:
        payload = self._open.pop(key, None)
        if payload is None:
            return None
        payload["phase"] = phase
        return {
            "id": payload["id"],
            "name": payload["name"],
            "phase": phase,
            "summary": payload["summary"],
        }

    def close_remaining(self, phase: str) -> list[dict]:
        closed: list[dict] = []
        for key in list(self._open):
            frame = self.finish(key, phase)
            if frame:
                closed.append(frame)
        return closed


def _status_end(name: str, output: Any) -> dict | None:
    if not isinstance(output, dict):
        return None
    meta = output.get("decision_meta")
    if not isinstance(meta, dict):
        return None
    payload: dict[str, Any] = {"node": name}
    for key in ("route", "confidence", "source", "noul"):
        if meta.get(key) is not None:
            payload[key] = meta[key]
    if len(payload) == 1:
        return None
    return payload


def interpret_graph_event(event: Any, tools: ToolTracker) -> list[tuple[str, dict]]:
    """SSE pairs for one graph callback. Tool inputs and outputs are dropped."""
    kind = event.get("event")
    name = str(event.get("name") or "")
    key = str(event.get("run_id") or name)
    frames: list[tuple[str, dict]] = []
    if kind == "on_chain_start" and name in _STATUS_NODES:
        frames.append(("status", {"node": name}))
        if name in _NODE_TOOLS:
            frames.append(("tool", tools.start(f"node:{key}", name)))
    elif kind == "on_chain_end" and name in _STATUS_NODES:
        extra = _status_end(name, (event.get("data") or {}).get("output"))
        if extra:
            frames.append(("status", extra))
        if name in _NODE_TOOLS:
            closed = tools.finish(f"node:{key}", "done")
            if closed:
                frames.append(("tool", closed))
    elif kind == "on_chain_error" and name in _NODE_TOOLS:
        closed = tools.finish(f"node:{key}", "failed")
        if closed:
            frames.append(("tool", closed))
    elif kind == "on_tool_start":
        frames.append(("tool", tools.start(f"tool:{key}", name or "tool")))
    elif kind == "on_tool_end":
        closed = tools.finish(f"tool:{key}", "done")
        if closed:
            frames.append(("tool", closed))
    elif kind == "on_tool_error":
        closed = tools.finish(f"tool:{key}", "failed")
        if closed:
            frames.append(("tool", closed))
    return frames


def cursor_after(header: str | None, frames: list[dict]) -> int:
    """SSE resume cursor. A missing or unknown id replays from the start."""
    if header is None or not str(header).strip().isdigit():
        return 0
    value = int(str(header).strip())
    if value <= 0:
        return 0
    known = set()
    for frame in frames:
        raw = frame.get("id")
        if isinstance(raw, str) and raw.isdigit():
            known.add(int(raw))
        elif isinstance(raw, int):
            known.add(raw)
    if value not in known:
        return 0
    return value


def is_stale_run(active: dict | None, *, now: datetime | None = None) -> bool:
    """A running row with no recent heartbeat is no longer producing events."""
    if not isinstance(active, dict) or active.get("status") != "running":
        return False
    beat = active.get("beat")
    if not isinstance(beat, str) or not beat:
        return True
    try:
        seen = datetime.fromisoformat(beat)
    except ValueError:
        return True
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return (current - seen).total_seconds() > _STALE_AFTER_SECONDS


def partial_from_events(frames: list) -> tuple[str, list, list]:
    """Rebuild the visible turn from a stored SSE log. No tool inputs."""
    text_parts: list[str] = []
    final: str | None = None
    ui: list = []
    tools: dict[str, dict] = {}
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        raw_data = frame.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        event = frame.get("event")
        if event == "token":
            text_parts.append(str(data.get("text") or ""))
        elif event == "assistantMessage":
            final = str(data.get("content") or "")
            if isinstance(data.get("ui"), list):
                ui = [item for item in data["ui"] if isinstance(item, dict)]
            if isinstance(data.get("tools"), list):
                for item in data["tools"]:
                    if isinstance(item, dict) and item.get("id"):
                        tools[str(item["id"])] = {
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "phase": item.get("phase"),
                            "summary": item.get("summary"),
                        }
        elif event == "ui" and data.get("id"):
            ui.append(
                {
                    "id": data.get("id"),
                    "component": data.get("component"),
                    "props": data.get("props"),
                }
            )
        elif event == "tool" and data.get("id"):
            tools[str(data["id"])] = {
                "id": data.get("id"),
                "name": data.get("name"),
                "phase": data.get("phase"),
                "summary": data.get("summary"),
            }
    content = final if final is not None else "".join(text_parts).strip()
    return content, ui, list(tools.values())
