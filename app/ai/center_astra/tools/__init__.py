"""Load every center Astra tool into the registry once."""

from __future__ import annotations

from app.ai.center_astra.tools.mutations import register_mutation_tools
from app.ai.center_astra.tools.reads import register_read_tools
from app.ai.center_astra.tools.registry import (
    center_astra_tool_names as _center_astra_tool_names,
)
from app.ai.center_astra.tools.registry import (
    center_astra_tool_spec,
)
from app.ai.center_astra.tools.registry import (
    tools_for as _tools_for,
)

_ready = False


def ensure_center_astra_tools() -> None:
    global _ready
    if _ready:
        return
    register_read_tools()
    register_mutation_tools()
    _ready = True


def tools_for(domains: list[str], *, is_owner: bool):
    ensure_center_astra_tools()
    return _tools_for(domains, is_owner=is_owner)


def center_astra_tool_names() -> set[str]:
    ensure_center_astra_tools()
    return _center_astra_tool_names()


__all__ = [
    "center_astra_tool_names",
    "center_astra_tool_spec",
    "ensure_center_astra_tools",
    "tools_for",
]
