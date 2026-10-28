from app.services.gamification.streaks import (
    CARD_REVIEWS_FOR_STUDY_DAY,
    calendar_days,
    display_streak,
    longest_streak,
    return_state,
    study_days,
)

TODAY = "2026-10-28"


def test_a_completed_attempt_makes_a_study_day():
    assert study_days({"2026-10-27"}, {}) == {"2026-10-27"}


def test_a_few_card_flips_do_not_make_a_study_day():
    counts = {"2026-10-27": CARD_REVIEWS_FOR_STUDY_DAY - 1}
    assert study_days(set(), counts) == set()


def test_a_real_flashcard_review_makes_a_study_day():
    counts = {"2026-10-27": CARD_REVIEWS_FOR_STUDY_DAY}
    assert study_days(set(), counts) == {"2026-10-27"}


def test_streak_counts_back_from_today_when_today_is_done():
    days = {"2026-10-28", "2026-10-27", "2026-10-26"}
    assert display_streak(days, TODAY) == 3


def test_a_streak_stays_alive_until_the_day_is_over():
    days = {"2026-10-27", "2026-10-26"}
    assert display_streak(days, TODAY) == 2


def test_a_missed_day_resets_the_streak_to_zero():
    assert display_streak({"2026-10-26"}, TODAY) == 0


def test_no_study_days_means_no_streak():
    assert display_streak(set(), TODAY) == 0


def test_longest_streak_finds_the_best_run_anywhere():
    days = {"2026-10-01", "2026-10-02", "2026-10-03", "2026-10-10", "2026-10-11"}
    assert longest_streak(days) == 3
    assert longest_streak(set()) == 0


def test_the_calendar_covers_only_the_window_and_is_sorted():
    days = {"2026-10-28", "2026-01-01", "2026-10-20"}
    out = calendar_days(days, TODAY, weeks=2)
    assert out == ["2026-10-20", "2026-10-28"]


def test_studied_today_is_reported_with_no_gap():
    s = return_state({"2026-10-28"}, TODAY)
    assert s == {"studiedToday": True, "daysSinceLastStudy": 0}


def test_a_gap_is_reported_in_days():
    s = return_state({"2026-10-24"}, TODAY)
    assert s == {"studiedToday": False, "daysSinceLastStudy": 4}


def test_a_brand_new_student_has_no_gap_to_report():
    assert return_state(set(), TODAY) == {
        "studiedToday": False,
        "daysSinceLastStudy": None,
    }
