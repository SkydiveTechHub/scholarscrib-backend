# ruff: noqa: E501
from app.services.gamification.quests import DayCounts, build_quests, evaluate

NONE = DayCounts(questions=0, questions_by_subject={}, cards=0, practice_sets=0)


def _types(quests):
    return [q["type"] for q in quests]


def test_a_new_student_gets_three_safe_generic_quests():
    quests = build_quests(focus_subject=None, due_cards=0)
    assert _types(quests) == ["warm_up", "focus", "practice_set"]
    focus = quests[1]
    assert focus["subjectId"] is None
    assert focus["target"] == 10


def test_a_recent_subject_makes_the_focus_quest_specific():
    quests = build_quests(focus_subject=("s1", "Physics"), due_cards=0)
    focus = quests[1]
    assert focus["subjectId"] == "s1"
    assert "Physics" in focus["title"]


def test_due_flashcards_become_the_revision_quest_capped_at_five():
    quests = build_quests(focus_subject=None, due_cards=40)
    assert _types(quests) == ["warm_up", "focus", "revision"]
    assert quests[2]["target"] == 5
    assert quests[2]["href"] == "/flashcards"


def test_a_revision_quest_never_asks_for_more_cards_than_are_due():
    quests = build_quests(focus_subject=None, due_cards=2)
    assert quests[2]["target"] == 2


def test_every_quest_has_an_action_and_a_positive_target():
    for q in build_quests(focus_subject=("s1", "Physics"), due_cards=9):
        assert q["href"].startswith("/")
        assert q["target"] > 0
        assert q["title"] and q["description"]


def test_nothing_done_means_zero_progress_and_not_complete():
    out = evaluate(build_quests(focus_subject=None, due_cards=0), NONE)
    assert [q["progress"] for q in out] == [0, 0, 0]
    assert not any(q["complete"] for q in out)


def test_progress_is_capped_at_the_target():
    counts = DayCounts(questions=99, questions_by_subject={}, cards=0, practice_sets=0)
    out = evaluate(build_quests(focus_subject=None, due_cards=0), counts)
    assert out[0]["progress"] == 5 and out[0]["complete"]


def test_a_subject_quest_counts_only_that_subject():
    counts = DayCounts(
        questions=12,
        questions_by_subject={"s1": 3, "s2": 9},
        cards=0,
        practice_sets=0,
    )
    focus = evaluate(
        build_quests(focus_subject=("s1", "Physics"), due_cards=0), counts
    )[1]
    assert focus["progress"] == 3 and not focus["complete"]


def test_revision_counts_card_reviews_and_practice_counts_sets():
    counts = DayCounts(questions=0, questions_by_subject={}, cards=5, practice_sets=1)
    rev = evaluate(build_quests(focus_subject=None, due_cards=9), counts)
    assert rev[2]["complete"]
    prac = evaluate(build_quests(focus_subject=None, due_cards=0), counts)
    assert prac[2]["complete"]
