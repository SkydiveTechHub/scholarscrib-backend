"""Student-facing study cards emitted beside the tutor's markdown reply."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = "```ui"
_FENCE_RE = re.compile(r"```ui[ \t]*\n(.*?)(?:\n```|$)", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]*>")
_FORBIDDEN = frozenset(
    {
        "answer",
        "answers",
        "answerkey",
        "correct",
        "correctanswer",
        "explanation",
        "markscheme",
        "markingscheme",
        "solution",
    }
)
_MAX_PROBLEM = 4000
_MAX_STEP = 500


def _plain(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = _TAG_RE.sub("", value).strip()
    if not text or "<" in text or ">" in text:
        return None
    return text[:limit]


def _plain_list(value: Any, limit: int, item_limit: int) -> list[str] | None:
    if not isinstance(value, list) or len(value) > limit or not value:
        return None
    items: list[str] = []
    for entry in value:
        text = _plain(entry, item_limit)
        if text is None:
            return None
        items.append(text)
    return items


def _forbidden(props: dict) -> bool:
    return any(str(key).lower() in _FORBIDDEN for key in props)


def validate_ui_block(raw: Any) -> dict | None:
    """Return a client block, or None when the component is not allowed."""
    if not isinstance(raw, dict):
        return None
    component = raw.get("component")
    props = raw.get("props")
    if not isinstance(component, str) or not isinstance(props, dict):
        return None
    if _forbidden(props):
        return None
    if component == "worked_example":
        problem = _plain(props.get("problem"), _MAX_PROBLEM)
        steps = _plain_list(props.get("steps"), 8, _MAX_STEP)
        if not problem or not steps:
            return None
        return {
            "component": "worked_example",
            "props": {"problem": problem, "steps": steps},
        }
    if component == "practice_question":
        prompt = _plain(props.get("prompt"), _MAX_PROBLEM)
        if not prompt:
            return None
        card: dict[str, Any] = {"prompt": prompt}
        if props.get("choices") not in (None, []):
            choices = _plain_list(props.get("choices"), 4, _MAX_STEP)
            if not choices:
                return None
            card["choices"] = choices
        return {"component": "practice_question", "props": card}
    return None


def accept_ui_blocks(raw_blocks: list[Any]) -> list[dict]:
    accepted: list[dict] = []
    for raw in raw_blocks:
        block = validate_ui_block(raw)
        if block is None:
            continue
        accepted.append({"id": f"ui_{len(accepted) + 1}", **block})
    return accepted


def split_ui(content: str) -> tuple[str, list[dict]]:
    """Remove ```ui fences from the reply and keep the valid cards."""
    found: list[Any] = []
    for match in _FENCE_RE.finditer(content or ""):
        try:
            found.append(json.loads(match.group(1).strip()))
        except json.JSONDecodeError:
            continue
    visible = _FENCE_RE.sub("", content or "").strip()
    return visible, accept_ui_blocks(found)


def _held_suffix(text: str, needle: str) -> int:
    """Length of the suffix of ``text`` that is a prefix of ``needle``."""
    upper = min(len(text), len(needle) - 1)
    for size in range(upper, 0, -1):
        if needle.startswith(text[-size:]):
            return size
    return 0


class UiStreamFilter:
    """Hold back a trailing ```ui fence so tokens stay markdown."""

    def __init__(self) -> None:
        self.held = ""
        self.capturing = False

    def push(self, text: str) -> str:
        if not text or self.capturing:
            return ""
        self.held += text
        index = self.held.find(_FENCE)
        if index >= 0:
            emit = self.held[:index].rstrip()
            self.held = ""
            self.capturing = True
            return emit
        keep = _held_suffix(self.held, _FENCE)
        if keep:
            emit = self.held[:-keep]
            self.held = self.held[-keep:]
            return emit
        emit = self.held
        self.held = ""
        return emit

    def flush_tail(self) -> str:
        if self.capturing:
            return ""
        tail = self.held
        self.held = ""
        return tail
