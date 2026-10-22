"""Tutor tools: reads run immediately; study-plan writes wait for approval."""

from app.services.rag.tools.reads import register_read_tools
from app.services.rag.tools.registry import action_tools
from app.services.rag.tools.study_plan import register_study_plan_tools

_ready = False


def ensure_tools() -> None:
    global _ready
    if _ready:
        return
    register_read_tools()
    register_study_plan_tools()
    _ready = True


def tutor_action_tools():
    ensure_tools()
    return action_tools()
