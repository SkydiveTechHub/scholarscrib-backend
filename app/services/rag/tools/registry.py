"""Registry for tutor tools. Mutating tools propose; reads run immediately."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel


@dataclass(frozen=True)
class ToolSpec:
    name: str
    mutating: bool
    feature: str | None
    tool: BaseTool
    summary_fn: Callable[[dict[str, Any]], str]
    destructive: bool = False


_SPECS: dict[str, ToolSpec] = {}


def register_tool(spec: ToolSpec) -> ToolSpec:
    _SPECS[spec.name] = spec
    return spec


def tool_spec(name: str) -> ToolSpec | None:
    return _SPECS.get(name)


def action_tools() -> list[BaseTool]:
    return [spec.tool for spec in _SPECS.values()]


def proposal_from_args(
    name: str, args: dict[str, Any], *, summary: str | None = None
) -> dict[str, Any]:
    spec = tool_spec(name)
    if spec is None or not spec.mutating:
        raise KeyError(name)
    text = summary or spec.summary_fn(args)
    return {
        "tool": name,
        "args": args,
        "summary": text,
        "destructive": spec.destructive,
        "feature": spec.feature,
    }


def dump_model(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
