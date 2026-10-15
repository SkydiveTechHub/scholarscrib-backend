from datetime import UTC, datetime, timedelta

from app.services.srs.analytics import ReviewSnapshot, review_health

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def _review(**overrides):
    data = dict(
        state="REVIEW",
        difficulty=5.0,
        stability=10.0,
        interval_days=10.0,
        lapses=0,
        due_at=NOW + timedelta(days=3),
        last_reviewed_at=NOW - timedelta(days=1),
    )
    data.update(overrides)
    return ReviewSnapshot(**data)


def test_a_healthy_card_is_not_flagged():
    assert review_health(_review(), (3, 3), NOW) is None


def test_a_card_whose_recall_has_decayed_is_fading():
    # Reviewed 45 days ago with 10 days of stability: recall is ~0.70, not yet due
    # long enough to be overdue.
    review = _review(
        last_reviewed_at=NOW - timedelta(days=45), due_at=NOW + timedelta(days=1)
    )
    assert review_health(review, (3, 3), NOW) == "fading"


def test_overdue_beats_fading_and_leech_beats_both():
    stale = _review(
        last_reviewed_at=NOW - timedelta(days=60), due_at=NOW - timedelta(days=50)
    )
    assert review_health(stale, (3, 3), NOW) == "overdue"
    assert review_health(_review(lapses=4), (3, 3), NOW) == "leech"
    assert review_health(_review(), (10, 2), NOW) == "leech"


def test_a_card_with_no_memory_has_no_health():
    review = _review(state="LEARNING", stability=0.0, last_reviewed_at=None)
    assert review_health(review, (0, 0), NOW) is None
