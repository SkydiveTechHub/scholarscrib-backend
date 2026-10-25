"""Admin Astra tool registry. Separate from the student tutor registry."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from app.ai.admin.context import AdminToolContext

DOMAINS = (
    "students",
    "questions",
    "lessons",
    "materials",
    "announcements",
    "terms",
    "catalogue",
    "dashboard",
    "audit",
    "provider",
    "rag",
    "team",
)

Runner = Callable[[AdminToolContext, BaseModel], Awaitable[Any]]


@dataclass(frozen=True)
class AdminToolSpec:
    name: str
    domain: str
    mutating: bool
    owner_only: bool
    destructive: bool
    tool: BaseTool
    args_model: type[BaseModel]
    execute: Runner | None = None


_SPECS: dict[str, AdminToolSpec] = {}


def register_admin_tool(spec: AdminToolSpec) -> AdminToolSpec:
    if spec.domain not in DOMAINS:
        raise ValueError(f"Unknown admin Astra domain: {spec.domain}")
    _SPECS[spec.name] = spec
    return spec


def admin_tool_spec(name: str) -> AdminToolSpec | None:
    return _SPECS.get(name)


def admin_tool_names() -> set[str]:
    return set(_SPECS)


def tools_for(domains: list[str], *, is_owner: bool) -> list[BaseTool]:
    chosen = {domain for domain in domains if domain in DOMAINS} or {"dashboard"}
    tools: list[BaseTool] = []
    for spec in _SPECS.values():
        if spec.domain not in chosen:
            continue
        if spec.owner_only and not is_owner:
            continue
        tools.append(spec.tool)
    return tools
