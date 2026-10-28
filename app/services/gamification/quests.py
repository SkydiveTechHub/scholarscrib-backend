"""Daily quests: what is assigned and how progress is read.

Pure. A day's quests are fixed when first assigned (see the service); progress
is always recomputed from that day's recorded activity, never stored.
"""

from dataclasses import dataclass

WARM_UP_QUESTIONS = 5
FOCUS_QUESTIONS = 10
REVISION_CARDS = 5


@dataclass(frozen=True)
class DayCounts:
    """What the student has recorded so far today (Lagos day)."""

    questions: int
    questions_by_subject: dict[str, int]
    cards: int
    practice_sets: int


def build_quests(
    *, focus_subject: tuple[str, str] | None, due_cards: int
) -> list[dict]:
    """Three quests the student can always finish.

    The focus quest names a subject only when the student has practised one
    recently; otherwise it is a generic count. The third quest is a revision
    review when cards are due, else a generic practice set. Nothing here claims
    a weakness.
    """
    quests = [
        {
            "id": "warm_up",
            "type": "warm_up",
            "title": "Warm-up",
            "description": f"Answer {WARM_UP_QUESTIONS} questions.",
            "target": WARM_UP_QUESTIONS,
            "subjectId": None,
            "href": "/practice",
        }
    ]
    if focus_subject is not None:
        subject_id, name = focus_subject
        quests.append(
            {
                "id": "focus",
                "type": "focus",
                "title": f"Focus on {name}",
                "description": f"Answer {FOCUS_QUESTIONS} {name} questions.",
                "target": FOCUS_QUESTIONS,
                "subjectId": subject_id,
                "href": "/practice",
            }
        )
    else:
        quests.append(
            {
                "id": "focus",
                "type": "focus",
                "title": "Focus session",
                "description": f"Answer {FOCUS_QUESTIONS} questions in any subject.",
                "target": FOCUS_QUESTIONS,
                "subjectId": None,
                "href": "/practice",
            }
        )
    if due_cards > 0:
        target = min(REVISION_CARDS, due_cards)
        quests.append(
            {
                "id": "revision",
                "type": "revision",
                "title": "Revision",
                "description": f"Review {target} due card{'' if target == 1 else 's'}.",
                "target": target,
                "subjectId": None,
                "href": "/flashcards",
            }
        )
    else:
        quests.append(
            {
                "id": "practice_set",
                "type": "practice_set",
                "title": "Finish a practice set",
                "description": "Complete one practice set.",
                "target": 1,
                "subjectId": None,
                "href": "/practice",
            }
        )
    return quests


def _raw_progress(quest: dict, counts: DayCounts) -> int:
    kind = quest["type"]
    if kind == "warm_up":
        return counts.questions
    if kind == "focus":
        subject = quest.get("subjectId")
        if subject is None:
            return counts.questions
        return counts.questions_by_subject.get(subject, 0)
    if kind == "revision":
        return counts.cards
    if kind == "practice_set":
        return counts.practice_sets
    return 0


def evaluate(quests: list[dict], counts: DayCounts) -> list[dict]:
    out = []
    for quest in quests:
        progress = min(max(_raw_progress(quest, counts), 0), quest["target"])
        out.append(
            {**quest, "progress": progress, "complete": progress >= quest["target"]}
        )
    return out
