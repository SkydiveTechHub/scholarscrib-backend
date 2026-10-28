from app.services.gamification.improvement import (
    Attempt,
    compare,
    ever_improved,
    message_for,
)


def _a(pct, questions=20):
    return Attempt(percentage=pct, questions=questions)


def test_no_history_is_not_enough_to_say():
    assert compare([_a(60)])["status"] == "insufficient"
    assert compare([])["status"] == "insufficient"


def test_a_single_earlier_attempt_is_too_thin_a_baseline():
    assert compare([_a(50), _a(80)])["status"] == "insufficient"


def test_a_clear_gain_over_the_baseline_average_is_an_improvement():
    out = compare([_a(50), _a(54), _a(52), _a(68)])
    assert out["status"] == "improved"
    assert out["from"] == 52 and out["to"] == 68


def test_a_small_gain_is_steady_not_celebrated():
    assert compare([_a(50), _a(54), _a(52), _a(56)])["status"] == "steady"


def test_a_tie_is_steady():
    assert compare([_a(60), _a(60), _a(60), _a(60)])["status"] == "steady"


def test_a_drop_is_reported_as_lower_without_drama():
    out = compare([_a(70), _a(72), _a(71), _a(55)])
    assert out["status"] == "lower"


def test_tiny_attempts_are_ignored_as_noise():
    attempts = [_a(10, 3), _a(100, 2), _a(50), _a(52), _a(70)]
    # Only the three 20-question attempts count: baseline of two, then a gain.
    assert compare(attempts)["status"] == "improved"


def test_only_the_last_three_baseline_attempts_are_averaged():
    out = compare([_a(10), _a(50), _a(50), _a(50), _a(56)])
    assert out["from"] == 50
    assert out["status"] == "steady"


def test_messages_are_neutral_and_specific():
    improved = message_for({"status": "improved", "from": 52, "to": 68})
    assert improved == "You improved from 52% to 68% across comparable practice."
    assert message_for({"status": "steady", "from": 60, "to": 61}) is None
    assert message_for({"status": "lower", "from": 70, "to": 55}) is None
    assert message_for({"status": "insufficient"}) is None


def test_ever_improved_remembers_a_past_gain_that_later_slipped():
    history = [_a(50), _a(52), _a(70), _a(40)]
    assert compare(history)["status"] == "lower"
    assert ever_improved(history) is True


def test_ever_improved_is_false_without_a_real_gain():
    assert ever_improved([_a(50), _a(52), _a(54), _a(55)]) is False
