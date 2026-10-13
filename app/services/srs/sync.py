"""How a lesson's regenerated cards are merged into its shared deck.

A deck built from a lesson belongs to the whole cohort: every student's review
history hangs off its cards. A card is therefore only ever removed when the
lesson block it came from is gone, and a card that still has a block keeps its
identity (and with it every student's memory of it) however its wording changes.
"""

from dataclasses import dataclass, field
from typing import NamedTuple


class ExistingCard(NamedTuple):
    id: str
    source_key: str | None
    front: str


@dataclass
class CardSyncPlan:
    # card id -> the regenerated card whose content it should take
    update: dict[str, dict] = field(default_factory=dict)
    # card id -> the source key it adopts (cards made before keys existed)
    adopt: dict[str, str] = field(default_factory=dict)
    # regenerated cards with no existing counterpart
    insert: list[dict] = field(default_factory=list)
    # card ids whose lesson block no longer exists
    delete: list[str] = field(default_factory=list)


def plan_card_sync(existing: list[ExistingCard], incoming: list[dict]) -> CardSyncPlan:
    plan = CardSyncPlan()
    by_key = {card.source_key: card for card in existing if card.source_key}
    # Cards made before source keys existed carry none. Matching one by its front
    # lets it adopt the key, so a rebuild does not reset the memory state of every
    # card that predates keys. Each is claimed at most once.
    legacy_by_front: dict[str, ExistingCard] = {}
    for card in existing:
        if not card.source_key and card.front:
            legacy_by_front.setdefault(card.front, card)

    incoming_keys = set()
    for card in incoming:
        key = card["sourceKey"]
        incoming_keys.add(key)
        current = by_key.get(key)
        if current is not None:
            plan.update[current.id] = card
            continue
        legacy = legacy_by_front.pop(card["payload"]["front"], None)
        if legacy is not None:
            plan.adopt[legacy.id] = key
            plan.update[legacy.id] = card
            continue
        plan.insert.append(card)

    plan.delete = [
        card.id
        for card in existing
        if card.source_key and card.source_key not in incoming_keys
    ]
    return plan
