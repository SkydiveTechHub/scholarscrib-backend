"""Extract student-visible text from model message content."""

from __future__ import annotations

import re
from typing import Any

_THINKING_BLOCK_TYPES = frozenset(
    {"thinking", "reasoning", "redacted_thinking", "reasoning_content"}
)
_THINKING_TAG_RE = re.compile(
    r"<(thinking|reasoning|think)>[\s\S]*?</\1>",
    re.IGNORECASE,
)


def visible_assistant_text(content: Any, *, strip: bool = True) -> str:
    """Return only final answer text (skip thinking / reasoning blocks).

    Pass ``strip=False`` for a streamed chunk: a token's leading space or
    newline is part of the answer ("Great" + " question"), and stripping it
    glues words together and flattens markdown structure.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return _strip_thinking_tags(content, strip=strip)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            text = _block_visible_text(block)
            if text:
                parts.append(text)
        return _strip_thinking_tags("".join(parts), strip=strip)
    return _strip_thinking_tags(str(content), strip=strip)


def _block_visible_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        btype = block.get("type")
        if btype in _THINKING_BLOCK_TYPES:
            return ""
        if btype in ("text", "output_text") or (btype is None and "text" in block):
            return str(block.get("text") or "")
        return ""
    btype = getattr(block, "type", None)
    if btype in _THINKING_BLOCK_TYPES:
        return ""
    if btype in ("text", "output_text", None):
        text = getattr(block, "text", None)
        if text:
            return str(text)
    return ""


def _strip_thinking_tags(text: str, *, strip: bool = True) -> str:
    cleaned = _THINKING_TAG_RE.sub("", text or "")
    return cleaned.strip() if strip else cleaned
