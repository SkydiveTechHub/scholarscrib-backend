from app.services.planner.days import DayKey, add_days, days_between, monday_of
from app.services.planner.topics import SubjectSelection

MAX_OUTLINE_WEEKS = 20


def project_outline(
    *,
    today: DayKey,
    start: DayKey,
    until: DayKey | None,
    mode: str,
    runway_start: DayKey | None,
    term_context: dict,
    selections: list[SubjectSelection],
) -> list[dict]:
    if not until or start > until:
        return []
    weeks: list[dict] = []
    week_start = monday_of(start)
    while week_start <= until and len(weeks) < MAX_OUTLINE_WEEKS:
        if runway_start and week_start >= monday_of(runway_start):
            weeks.append(
                {
                    "weekStart": week_start,
                    "label": "Exam runway — mocks and past questions",
                    "topics": [],
                }
            )
        elif mode == "EXAM":
            weeks.append(
                {"weekStart": week_start, "label": "Exam revision", "topics": []}
            )
        elif (
            term_context["kind"] != "in_term"
            or week_start > term_context["current"].ends_on
        ):
            weeks.append(
                {
                    "weekStart": week_start,
                    "label": "Next term — topics follow the school calendar",
                    "topics": [],
                }
            )
        else:
            weeks_ahead = days_between(monday_of(today), week_start) // 7
            topics = []
            for selection in selections:
                count = len(selection.term_topics)
                if count == 0 or selection.class_index < 0:
                    continue
                pace = count / term_context["totalWeeks"]
                index = min(
                    count - 1, selection.class_index + math_floor(weeks_ahead * pace)
                )
                topics.append(
                    {
                        "subjectId": selection.subject_id,
                        "title": selection.term_topics[index].title,
                    }
                )
            weeks.append({"weekStart": week_start, "label": None, "topics": topics})
        week_start = add_days(week_start, 7)
    return weeks


def math_floor(value: float) -> int:
    import math

    return math.floor(value)
