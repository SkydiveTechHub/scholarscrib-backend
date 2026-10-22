"""Visible assistant text strips thinking / reasoning content."""

from app.services.rag.message_text import visible_assistant_text


def test_plain_string():
    assert visible_assistant_text("Osmosis is water movement.") == (
        "Osmosis is water movement."
    )


def test_strips_thinking_tags():
    raw = "<thinking>plan steps</thinking>\nOsmosis is diffusion of water."
    assert visible_assistant_text(raw) == "Osmosis is diffusion of water."


def test_skips_thinking_blocks():
    content = [
        {"type": "thinking", "thinking": "internal"},
        {"type": "text", "text": "Final answer."},
    ]
    assert visible_assistant_text(content) == "Final answer."
