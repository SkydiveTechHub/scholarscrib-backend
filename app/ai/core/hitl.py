"""Human-in-the-loop proposal payload shared by tools and the graph."""

from __future__ import annotations

import json
from typing import Any

PROPOSAL_KEY = "__actionProposal__"


def encode_proposal(proposal: dict[str, Any]) -> str:
    return json.dumps({PROPOSAL_KEY: proposal}, default=str)


def decode_proposal(content: str) -> dict[str, Any] | None:
    try:
        data = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    proposal = data.get(PROPOSAL_KEY)
    if not isinstance(proposal, dict) or not proposal.get("tool"):
        return None
    return proposal
