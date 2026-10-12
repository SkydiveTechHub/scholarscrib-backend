from datetime import UTC, datetime, timedelta

from app.core.timeutil import lagos_day_start
from app.services.learning.mastery import (
    TopicState,
    evidence_level,
    mean_retrievability,
    topic_retention,
)

NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def make_state(
    topic_id,
    mastery=0,
    confidence=0.0,
    observations=0,
    retention=None,
    last_days=None,
    available=True,
    acc=None,
):
    return TopicState(
        topic_id=topic_id,
        mastery=mastery,
        confidence=confidence,
        retention=retention,
        observations=observations,
        last_effort_at=None if last_days is None else NOW - timedelta(days=last_days),
        available=available,
        acc_observations=observations if acc is None else acc,
    )


def test_lagos_day_start_is_previous_utc_evening():
    # Lagos is UTC+1 with no DST: its midnight is 23:00 UTC the day before.
    assert lagos_day_start("2026-10-12") == datetime(2026, 10, 11, 23, 0, tzinfo=UTC)


def test_evidence_level_bands():
    assert evidence_level(make_state("a")) == "NONE"
    assert evidence_level(make_state("a", observations=2, confidence=0.2)) == "THIN"
    assert evidence_level(make_state("a", observations=8, confidence=0.6)) == "ENOUGH"


def test_topic_retention_prefers_cards_with_enough_reviewed_cards():
    state = make_state("a", observations=5, confidence=0.5, retention=0.7)
    assert topic_retention(state, (0.5, 3)) == (0.5, "CARDS")
    assert topic_retention(state, (0.5, 2)) == (0.7, "ESTIMATE")
    assert topic_retention(state, None) == (0.7, "ESTIMATE")
    assert topic_retention(make_state("b"), None) == (None, "ESTIMATE")


def test_mean_retrievability_uses_scheduler_curve_and_skips_unreviewed():
    rows = [
        ("t1", 10.0, NOW - timedelta(days=10)),  # (1 + 19/81)^-0.5 == 0.9
        ("t1", 0.0, NOW - timedelta(days=1)),  # zero stability: skipped
        ("t1", 5.0, None),  # never reviewed: skipped
        (None, 5.0, NOW),  # no topic: skipped
    ]
    result = mean_retrievability(rows, NOW)
    assert set(result) == {"t1"}
    mean, count = result["t1"]
    assert count == 1
    assert abs(mean - 0.9) < 1e-9
