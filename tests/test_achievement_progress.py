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


def test_flashcard_reviews_are_a_counted_criterion():
    assert progress_for("cards_reviewed", 30, 50) == 30
    assert progress_for("cards_reviewed", 80, 50) == 50
    assert target_for("cards_reviewed", 50) == 50


def test_every_known_criterion_has_a_category():
    from app.services.learning.achievement_progress import CATEGORY_FOR

    for criteria in (
        "questions_answered",
        "perfect_score",
        "streak_days",
        "lessons_completed",
        "subject_mastery",
        "mock_score_70",
        "cards_reviewed",
    ):
        assert criteria in CATEGORY_FOR


def test_categories_follow_the_product_groupings():
    from app.services.learning.achievement_progress import category_for

    assert category_for("streak_days") == "consistency"
    assert category_for("questions_answered") == "practice"
    assert category_for("cards_reviewed") == "revision"
    assert category_for("subject_mastery") == "mastery"
    assert category_for("mock_score_70") == "exam_readiness"


def test_an_unknown_criterion_falls_back_to_practice():
    from app.services.learning.achievement_progress import category_for

    assert category_for("something_new") == "practice"


async def test_card_reviews_are_measured_from_review_events(monkeypatch):
    from app.services.learning import service as learning

    async def fake_count(session, student_id, kind):
        assert kind == "CARD_REVIEWED"
        return 42

    monkeypatch.setattr(learning.learning_events_repository, "count_kind", fake_count)
    measure = learning.AchievementMeasure(None, "stu1")
    assert await measure.current("cards_reviewed") == 42


def test_new_comeback_criteria_are_categorised_and_shaped():
    from app.services.learning.achievement_progress import (
        ALL_OR_NOTHING,
        COUNTED,
        category_for,
    )

    assert "questions_corrected" in COUNTED
    assert "mock_improvement" in ALL_OR_NOTHING
    assert category_for("questions_corrected") == "improvement"
    assert category_for("mock_improvement") == "exam_readiness"
    assert progress_for("questions_corrected", 4, 10) == 4
    assert progress_for("mock_improvement", 1, 1) == 1


async def test_corrections_are_measured_from_the_xp_ledger(monkeypatch):
    from app.services.learning import service as learning

    async def fake_count(session, student_id, event):
        assert event == "answer_corrected"
        return 6

    monkeypatch.setattr(learning.xp_repository, "count_event", fake_count)
    assert (
        await learning.AchievementMeasure(None, "s").current("questions_corrected") == 6
    )


async def test_mock_improvement_is_measured_from_mock_history(monkeypatch):
    from app.services.gamification.improvement import Attempt
    from app.services.learning import service as learning

    history = [Attempt(50, 40), Attempt(52, 40), Attempt(70, 40)]

    async def fake_mocks(session, student_id):
        return history

    monkeypatch.setattr(learning.xp_repository, "mock_history", fake_mocks)
    assert await learning.AchievementMeasure(None, "s").current("mock_improvement") == 1
