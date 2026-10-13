from datetime import UTC, datetime, timedelta

from app.services.learning.checkpoint import sanitise_checkpoint
from app.services.practice_exit import (
    EXIT_COOLDOWN,
    best_recent,
    record_attempt,
    retry_available_at,
)

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def _record(percentage: float, passed: bool, at: datetime) -> dict:
    return {
        "attemptId": f"a-{percentage}",
        "percentage": percentage,
        "passed": passed,
        "at": at.isoformat(),
    }


def test_a_client_can_only_write_progress_keys():
    forged = {
        "visited": ["c1"],
        "checks": {"k1": {"attempts": 1, "correct": True}},
        "practice": [{"attemptId": "x", "percentage": 100, "passed": True}],
        "exit": {"attemptId": "x"},
    }
    assert sanitise_checkpoint(forged) == {
        "visited": ["c1"],
        "checks": {"k1": {"attempts": 1, "correct": True}},
    }


def test_no_wait_before_the_first_attempt():
    assert retry_available_at([], NOW) is None


def test_a_fail_starts_a_wait_from_when_it_was_submitted():
    records = [_record(40, False, NOW - timedelta(minutes=10))]
    assert (
        retry_available_at(records, NOW) == NOW - timedelta(minutes=10) + EXIT_COOLDOWN
    )


def test_the_wait_ends_after_the_cooldown():
    records = [_record(40, False, NOW - EXIT_COOLDOWN - timedelta(seconds=1))]
    assert retry_available_at(records, NOW) is None


def test_a_pass_never_waits():
    records = [_record(80, True, NOW - timedelta(minutes=1))]
    assert retry_available_at(records, NOW) is None


def test_only_the_latest_attempt_sets_the_wait():
    records = [
        _record(40, False, NOW - timedelta(minutes=5)),
        _record(90, True, NOW - timedelta(minutes=1)),
    ]
    assert retry_available_at(records, NOW) is None


def test_a_garbled_timestamp_does_not_lock_a_student_out():
    records = [{"attemptId": "a", "percentage": 10, "passed": False, "at": "soon"}]
    assert retry_available_at(records, NOW) is None


def test_a_bad_retake_does_not_erase_a_demonstrated_pass():
    records = [_record(85, True, NOW), _record(40, False, NOW), _record(30, False, NOW)]
    assert best_recent(records) == 85


def test_mastery_looks_only_at_the_last_three_attempts():
    records = [
        _record(95, True, NOW),
        _record(50, False, NOW),
        _record(55, False, NOW),
        _record(60, False, NOW),
    ]
    assert best_recent(records) == 60


def test_recording_an_attempt_clears_the_open_marker_and_keeps_progress():
    checkpoint = {
        "visited": ["c1"],
        "checks": {},
        "exit": {"attemptId": "a1", "startedAt": NOW.isoformat()},
    }
    updated = record_attempt(
        checkpoint, attempt_id="a1", percentage=71.43, passed=True, now=NOW
    )
    assert "exit" not in updated
    assert updated["visited"] == ["c1"]
    assert updated["practice"] == [
        {
            "attemptId": "a1",
            "percentage": 71.4,
            "passed": True,
            "at": NOW.isoformat(),
        }
    ]
    # The input is left alone, so a failed write cannot half-apply.
    assert "exit" in checkpoint


def test_history_is_bounded():
    checkpoint: dict = {}
    for index in range(25):
        checkpoint = record_attempt(
            checkpoint,
            attempt_id=f"a{index}",
            percentage=50,
            passed=False,
            now=NOW,
        )
    assert len(checkpoint["practice"]) == 20
    assert checkpoint["practice"][-1]["attemptId"] == "a24"
