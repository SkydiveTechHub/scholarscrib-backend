import pytest

from app.services.gamification.rules import (
    MIN_PRACTICE_QUESTIONS,
    PRACTICE_AWARDS_PER_DAY,
    XP_AMOUNTS,
    idempotency_key,
    practice_xp_eligible,
)


def test_initial_amounts_match_the_product_proposal():
    assert XP_AMOUNTS["lesson_completed"] == 30
    assert XP_AMOUNTS["practice_set_completed"] == 20


def test_a_short_attempt_does_not_count_as_a_practice_set():
    assert not practice_xp_eligible(MIN_PRACTICE_QUESTIONS - 1, awarded_today=0)


def test_an_attempt_of_the_minimum_size_counts():
    assert practice_xp_eligible(MIN_PRACTICE_QUESTIONS, awarded_today=0)


def test_the_daily_cap_stops_further_practice_awards():
    assert practice_xp_eligible(20, awarded_today=PRACTICE_AWARDS_PER_DAY - 1)
    assert not practice_xp_eligible(20, awarded_today=PRACTICE_AWARDS_PER_DAY)


def test_keys_are_stable_for_the_same_source():
    a = idempotency_key("practice_set_completed", "stu1", "att1")
    b = idempotency_key("practice_set_completed", "stu1", "att1")
    assert a == b


def test_keys_differ_by_event_student_and_source():
    base = idempotency_key("practice_set_completed", "stu1", "att1")
    assert base != idempotency_key("lesson_completed", "stu1", "att1")
    assert base != idempotency_key("practice_set_completed", "stu2", "att1")
    assert base != idempotency_key("practice_set_completed", "stu1", "att2")


def test_unknown_events_have_no_key():
    with pytest.raises(KeyError):
        idempotency_key("bogus", "stu1", "x")


from app.services.gamification.rules import events_for_attempt  # noqa: E402


def test_a_passed_lesson_exit_earns_lesson_xp_only():
    assert events_for_attempt(
        completed=True, exit_passed=True, answered=8, practice_awarded_today=0
    ) == ["lesson_completed"]


def test_a_failed_lesson_exit_earns_nothing():
    assert (
        events_for_attempt(
            completed=True, exit_passed=False, answered=8, practice_awarded_today=0
        )
        == []
    )


def test_ordinary_practice_earns_practice_xp():
    assert events_for_attempt(
        completed=True, exit_passed=None, answered=10, practice_awarded_today=0
    ) == ["practice_set_completed"]


def test_an_incomplete_attempt_earns_nothing():
    assert (
        events_for_attempt(
            completed=False, exit_passed=None, answered=10, practice_awarded_today=0
        )
        == []
    )


def test_a_tiny_or_capped_practice_attempt_earns_nothing():
    assert (
        events_for_attempt(
            completed=True, exit_passed=None, answered=2, practice_awarded_today=0
        )
        == []
    )
    assert (
        events_for_attempt(
            completed=True,
            exit_passed=None,
            answered=10,
            practice_awarded_today=PRACTICE_AWARDS_PER_DAY,
        )
        == []
    )
