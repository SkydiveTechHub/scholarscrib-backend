from app.services.learning.achievement_progress import progress_for, target_for


def test_count_criteria_target_is_the_catalogue_value():
    assert target_for("questions_answered", 100) == 100
    assert target_for("streak_days", 7) == 7


def test_all_or_nothing_criteria_have_a_target_of_one():
    assert target_for("perfect_score", 0) == 1
    assert target_for("mock_score_70", 70) == 1


def test_a_zero_catalogue_value_never_gives_a_zero_target():
    assert target_for("lessons_completed", 0) == 1


def test_progress_is_the_raw_count_below_the_target():
    assert progress_for("questions_answered", 37, 100) == 37


def test_progress_is_capped_at_the_target():
    assert progress_for("questions_answered", 250, 100) == 100


def test_all_or_nothing_progress_is_zero_or_one():
    assert progress_for("perfect_score", 0, 1) == 0
    assert progress_for("perfect_score", 4, 1) == 1
    assert progress_for("mock_score_70", 2, 70) == 1


def test_unknown_criteria_report_no_progress():
    assert progress_for("made_up", 9, 5) == 0
