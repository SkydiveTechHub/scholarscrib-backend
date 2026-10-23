"""Load every admin Astra tool into the admin registry once."""

from __future__ import annotations

from app.services.admin_astra.tools.content import register_content_tools
from app.services.admin_astra.tools.operations import register_operation_tools
from app.services.admin_astra.tools.questions import register_question_tools
from app.services.admin_astra.tools.registry import (
    admin_tool_names as _admin_tool_names,
)
from app.services.admin_astra.tools.registry import (
    admin_tool_spec,
)
from app.services.admin_astra.tools.registry import (
    tools_for as _tools_for,
)
from app.services.admin_astra.tools.students import register_student_tools

_ready = False


def ensure_admin_tools() -> None:
    global _ready
    if _ready:
        return
    register_student_tools()
    register_question_tools()
    register_content_tools()
    register_operation_tools()
    _ready = True


def tools_for(domains: list[str], *, is_owner: bool):
    ensure_admin_tools()
    return _tools_for(domains, is_owner=is_owner)


def admin_tool_names() -> set[str]:
    ensure_admin_tools()
    return _admin_tool_names()


__all__ = [
    "admin_tool_names",
    "admin_tool_spec",
    "ensure_admin_tools",
    "tools_for",
]
