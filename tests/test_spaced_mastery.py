from datetime import UTC, datetime, timedelta

from app.database.models import TopicMastery
from app.services.learning.evidence import MASTERY_MIN_DAYS, SCORING_VERSION
from app.services.learning.mastery import (
    LearningEventView,
    TopicAggregate,
    TopicState,
    fold_events,
    graph_colour,
    is_mastered,
    state_from_aggregate,
)
from app.services.learning.mastery_store import _aggregate

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


def answer(seq, when, correct=True, kind="QUESTION_ANSWERED", score=None):
    return LearningEventView(
        seq=seq,
        topic_id="t",
        kind=kind,
        correct=correct,
        score=score,
        difficulty="INTERMEDIATE",
        occurred_at=when,
    )


def fresh():
    return TopicAggregate(student_id="s", topic_id="t")


def many(when_list):
    return [answer(i + 1, when) for i, when in enumerate(when_list)]


def test_constant_is_two():
    assert MASTERY_MIN_DAYS == 2


def test_seven_strong_answers_on_one_day_are_not_mastered():
    when = NOW - timedelta(hours=1)
    aggregate = fold_events(fresh(), many([when] * 12), NOW)
    state = state_from_aggregate(aggregate, NOW)
    assert state.evidence_days == 1
    assert state.mastery >= 70 and state.observations >= 7
    assert not is_mastered(state)
    assert graph_colour(state) == "STARTED"


def test_the_same_answers_across_two_days_are_mastered():
    day1 = NOW - timedelta(days=1)
    day2 = NOW - timedelta(hours=1)
    aggregate = fold_events(fresh(), many([day1] * 6 + [day2] * 6), NOW)
    state = state_from_aggregate(aggregate, NOW)
    assert state.evidence_days == 2
    assert is_mastered(state)


def test_the_boundary_is_the_lagos_midnight_not_utc():
    # Lagos is UTC+1. 22:30 UTC and 23:30 UTC are 23:30 and 00:30 Lagos.
    a = datetime(2026, 10, 25, 22, 30, tzinfo=UTC)
    b = datetime(2026, 10, 25, 23, 30, tzinfo=UTC)
    aggregate = fold_events(fresh(), many([a, b]), NOW)
    assert aggregate.evidence_days == 2
    # 10:00 UTC and 20:00 UTC are the same Lagos day.
    c = datetime(2026, 10, 24, 10, tzinfo=UTC)
    d = datetime(2026, 10, 24, 20, tzinfo=UTC)
    aggregate = fold_events(fresh(), many([c, d]), NOW)
    assert aggregate.evidence_days == 1


def test_a_wrong_answer_still_counts_as_a_day():
    aggregate = fold_events(
        fresh(), [answer(1, NOW - timedelta(days=2), correct=False)], NOW
    )
    assert aggregate.evidence_days == 1


def test_unscored_lesson_abandoned_and_pretest_events_add_no_day():
    when = NOW - timedelta(days=1)
    events = [
        answer(1, when, kind="LESSON_BLOCK_COMPLETED", score=None),
        answer(2, when, kind="QUIZ_ABANDONED"),
        answer(3, when, kind="PRETEST_PASSED"),
    ]
    aggregate = fold_events(fresh(), events, NOW)
    assert aggregate.evidence_days == 0


def test_a_scored_lesson_event_counts_as_a_day():
    when = NOW - timedelta(days=1)
    events = [answer(1, when, kind="LESSON_COMPLETED", score=0.9)]
    assert fold_events(fresh(), events, NOW).evidence_days == 1


def test_a_stale_version_refolds_and_rebuilds_the_day_count():
    aggregate = fresh()
    aggregate.scoring_version = SCORING_VERSION - 1
    aggregate.evidence_days = 9
    aggregate.last_evidence_day = "2000-01-01"
    events = many([NOW - timedelta(days=3), NOW - timedelta(days=1)])
    fold_events(aggregate, events, NOW)
    assert aggregate.scoring_version == SCORING_VERSION
    assert aggregate.evidence_days == 2


def test_a_bare_topic_state_has_no_days():
    assert TopicState(topic_id="t").evidence_days == 0


def test_aggregate_reads_the_stored_day_fields():
    row = TopicMastery(
        student_id="s",
        topic_id="t",
        evidence_days=3,
        last_evidence_day="2026-10-26",
    )
    # unset numeric columns have no Python default until flushed
    for name in (
        "acc_outcome",
        "acc_mass",
        "acc_observations",
        "lesson_outcome",
        "lesson_mass",
        "lesson_observations",
        "srs_outcome",
        "srs_mass",
        "srs_observations",
        "abandon_count",
        "cursor_seq",
        "scoring_version",
    ):
        setattr(row, name, 0)
    aggregate = _aggregate("s", row)
    assert aggregate.evidence_days == 3
    assert aggregate.last_evidence_day == "2026-10-26"
