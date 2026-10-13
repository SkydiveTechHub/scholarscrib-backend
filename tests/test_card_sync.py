from app.services.srs.sync import ExistingCard, plan_card_sync


def _incoming(key: str, front: str = "", back: str = "b") -> dict:
    return {
        "sourceKey": key,
        "cardType": "DEFINITION",
        "payload": {"front": front or f"front {key}", "back": back},
    }


def test_an_unchanged_lesson_touches_nothing_but_content():
    existing = [
        ExistingCard("c1", "k1", "front k1"),
        ExistingCard("c2", "k2", "front k2"),
    ]
    plan = plan_card_sync(existing, [_incoming("k1"), _incoming("k2")])
    assert set(plan.update) == {"c1", "c2"}
    assert plan.insert == []
    assert plan.delete == []
    assert plan.adopt == {}


def test_a_reworded_card_keeps_its_identity():
    # The wording changed, the block did not: the card, and so every student's
    # review history on it, must survive.
    existing = [ExistingCard("c1", "k1", "old wording")]
    plan = plan_card_sync(existing, [_incoming("k1", front="new wording")])
    assert plan.update["c1"]["payload"]["front"] == "new wording"
    assert plan.insert == []
    assert plan.delete == []


def test_a_new_block_adds_a_card_and_removes_nothing():
    existing = [ExistingCard("c1", "k1", "front k1")]
    plan = plan_card_sync(existing, [_incoming("k1"), _incoming("k2")])
    assert [card["sourceKey"] for card in plan.insert] == ["k2"]
    assert plan.delete == []


def test_only_a_card_whose_block_is_gone_is_removed():
    existing = [
        ExistingCard("c1", "k1", "front k1"),
        ExistingCard("c2", "k2", "front k2"),
    ]
    plan = plan_card_sync(existing, [_incoming("k1")])
    assert plan.delete == ["c2"]
    assert "c1" in plan.update


def test_an_empty_regeneration_would_remove_every_keyed_card():
    # The caller refuses to sync an empty card list; this documents why.
    existing = [ExistingCard("c1", "k1", "front k1")]
    assert plan_card_sync(existing, []).delete == ["c1"]


def test_a_card_from_before_keys_existed_adopts_its_key_by_front():
    existing = [ExistingCard("legacy", None, "What is a cell?")]
    plan = plan_card_sync(existing, [_incoming("k1", front="What is a cell?")])
    assert plan.adopt == {"legacy": "k1"}
    assert "legacy" in plan.update
    assert plan.insert == []
    assert plan.delete == []


def test_a_legacy_card_is_claimed_by_at_most_one_block():
    existing = [ExistingCard("legacy", None, "same front")]
    plan = plan_card_sync(
        existing,
        [_incoming("k1", front="same front"), _incoming("k2", front="same front")],
    )
    assert plan.adopt == {"legacy": "k1"}
    assert [card["sourceKey"] for card in plan.insert] == ["k2"]


def test_an_unmatched_legacy_card_is_left_alone():
    existing = [ExistingCard("legacy", None, "reworded long ago")]
    plan = plan_card_sync(existing, [_incoming("k1", front="something else")])
    assert plan.delete == []
    assert "legacy" not in plan.update
    assert [card["sourceKey"] for card in plan.insert] == ["k1"]


def test_a_keyed_card_wins_over_a_legacy_card_with_the_same_front():
    existing = [
        ExistingCard("keyed", "k1", "front"),
        ExistingCard("legacy", None, "front"),
    ]
    plan = plan_card_sync(existing, [_incoming("k1", front="front")])
    assert "keyed" in plan.update
    assert plan.adopt == {}
