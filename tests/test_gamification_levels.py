import pytest

from app.services.gamification.levels import LEVELS, level_progress


def test_a_new_student_with_no_xp_is_level_one():
    p = level_progress(0)
    assert (p.level, p.title) == (1, "Starter Scholar")
    assert p.xp_into_level == 0
    assert p.xp_to_next == 100
    assert p.is_max is False


def test_one_xp_short_of_level_two_stays_at_level_one():
    p = level_progress(99)
    assert p.level == 1
    assert p.xp_to_next == 1


def test_the_exact_threshold_reaches_the_next_level():
    assert level_progress(100).level == 2
    assert level_progress(250).level == 3
    assert level_progress(450).level == 4


def test_progress_within_a_level_is_relative_to_its_start():
    p = level_progress(300)
    assert p.level == 3
    assert p.xp_into_level == 50
    assert p.xp_to_next == 150
    assert p.level_span == 200


def test_the_top_level_has_no_next_step():
    top = LEVELS[-1]
    p = level_progress(top.threshold + 5000)
    assert p.level == top.level
    assert p.is_max is True
    assert p.xp_to_next == 0
    assert p.title == "ScholarsCrib Elite"


def test_negative_xp_is_treated_as_zero():
    assert level_progress(-40).level == 1


def test_thresholds_start_at_zero_and_strictly_increase():
    assert LEVELS[0].threshold == 0
    thresholds = [lv.threshold for lv in LEVELS]
    assert thresholds == sorted(set(thresholds))
    assert [lv.level for lv in LEVELS] == list(range(1, len(LEVELS) + 1))


@pytest.mark.parametrize("level", LEVELS)
def test_every_threshold_maps_back_to_its_own_level(level):
    assert level_progress(level.threshold).level == level.level
