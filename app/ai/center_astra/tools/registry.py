"""Center Astra tool registry. Separate from student and admin registries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from app.ai.center_astra.context import CenterAstraContext

DOMAINS = (
    "results",
    "assignments",
    "recommendations",
    "roster",
)

Runner = Callable[[CenterAstraContext, BaseModel], Awaitable[Any]]


@dataclass(frozen=True)
class CenterAstraToolSpec:
    name: str
    domain: str
    mutating: bool
    owner_only: bool
    destructive: bool
    tool: BaseTool
    args_model: type[BaseModel]
    execute: Runner | None = None


_SPECS: dict[str, CenterAstraToolSpec] = {}


def register_center_astra_tool(spec: CenterAstraToolSpec) -> CenterAstraToolSpec:
    if spec.domain not in DOMAINS:
        raise ValueError(f"Unknown center Astra domain: {spec.domain}")
    _SPECS[spec.name] = spec
    return spec


def center_astra_tool_spec(name: str) -> CenterAstraToolSpec | None:
    return _SPECS.get(name)


def center_astra_tool_names() -> set[str]:
    return set(_SPECS)


def tools_for(domains: list[str], *, is_owner: bool) -> list[BaseTool]:
    chosen = {domain for domain in domains if domain in DOMAINS} or {"results"}
    tools: list[BaseTool] = []
    for spec in _SPECS.values():
        if spec.domain not in chosen:
            continue
        if spec.owner_only and not is_owner:
            continue
        tools.append(spec.tool)
    return tools
