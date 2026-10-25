"""Shared LangGraph state slices used by tutor and admin graphs.

Product TypedDicts (`TutorState`, `AdminAstraState`) include these HITL
keys so both action subgraphs speak the same interrupt/resume protocol.
"""

from __future__ import annotations

from typing import Any, TypedDict


class ActionHitlState(TypedDict, total=False):
    """Fields shared by student and admin action/HITL loops.

    Flow: tool emits a proposal → ``pending_action`` is set → graph
    ``interrupt``s → client resumes with ``action_decision`` → execute
    writes ``action_result`` and clears pending.
    """

    pending_action: dict[str, Any] | None
    action_decision: dict[str, Any] | None
    action_result: dict[str, Any] | None
