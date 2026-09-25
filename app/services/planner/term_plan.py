from dataclasses import dataclass, field

from app.services.learning.graph import KnowledgeGraph
from app.services.learning.mastery import TopicStateMap
from app.services.planner.days import DayKey, add_days, days_between
from app.services.planner.layout import FixedItem, RevisionDue, layout_window
from app.services.planner.mode import WINDOW_DAYS, PlanMode, compute_runway_start
from app.services.planner.outline import project_outline
from app.services.planner.slots import Availability, build_slots
from app.services.planner.topics import (
    PlanTopic,
    select_exam_topics,
    select_term_topics,
)


@dataclass
class PlannerSubject:
    id: str
    name: str
    topics: list[PlanTopic]


@dataclass
class PlannerInput:
    today: DayKey
    plan_start: DayKey
    mode: PlanMode
    class_level: str
    target_date: DayKey | None
    term_context: dict
    availability: Availability
    subjects: list[PlannerSubject]
    graph: KnowledgeGraph
    state: TopicStateMap
    pretest_passed: set[str] = field(default_factory=set)
    positions: dict[str, str] = field(default_factory=dict)
    revision_due: list[RevisionDue] = field(default_factory=list)
    carry_over: list = field(default_factory=list)
    fixed: list[FixedItem] = field(default_factory=list)


def plan_window(planner: PlannerInput) -> dict:
    target_date = planner.target_date
    exam_bound = planner.mode != "TERM" and target_date is not None
    if target_date is not None and planner.mode != "TERM":
        days = max(0, min(WINDOW_DAYS, days_between(planner.today, target_date) + 1))
        runway_start = compute_runway_start(planner.plan_start, target_date)
    else:
        days = WINDOW_DAYS
        runway_start = None
    planned_through = add_days(planner.today, max(1, days) - 1)
    selections = [
        select_term_topics(
            subject_id=subject.id,
            class_level=planner.class_level,
            term_context=planner.term_context,
            topics=subject.topics,
            graph=planner.graph,
            state=planner.state,
            pretest_passed=planner.pretest_passed,
            position_topic_id=planner.positions.get(subject.id),
            carry_over=[
                item for item in planner.carry_over if item.subject_id == subject.id
            ],
            carry_over_only=planner.mode == "EXAM",
        )
        for subject in planner.subjects
    ]
    exam_candidates = (
        []
        if planner.mode == "TERM"
        else select_exam_topics(
            [topic for subject in planner.subjects for topic in subject.topics],
            planner.class_level,
            planner.state,
        )
    )
    items, overload = layout_window(
        mode=planner.mode,
        slots=build_slots(planner.today, days, planner.availability),
        target_date=planner.target_date,
        runway_start=runway_start,
        selections=selections,
        exam_candidates=exam_candidates,
        subject_ids=[subject.id for subject in planner.subjects],
        subject_names={subject.id: subject.name for subject in planner.subjects},
        graph=planner.graph,
        state=planner.state,
        pretest_passed=planner.pretest_passed,
        revision_due=planner.revision_due,
        fixed=planner.fixed,
    )
    if exam_bound:
        until = planner.target_date
    elif planner.term_context["kind"] == "in_term":
        until = planner.term_context["current"].ends_on
    else:
        until = None
    outline = project_outline(
        today=planner.today,
        start=add_days(planned_through, 1),
        until=until,
        mode=planner.mode,
        runway_start=runway_start,
        term_context=planner.term_context,
        selections=selections,
    )
    return {
        "items": items,
        "outline": outline,
        "overload": overload,
        "plannedThrough": planned_through,
        "runwayStart": runway_start,
    }


def signal_for_assessment(assessment_type: str, has_topics: bool) -> str | None:
    if assessment_type in {"MOCK_EXAM", "CBT_PRACTICE"}:
        return "MOCK_EXAM"
    if assessment_type == "PAST_PAPER":
        return "PAST_QUESTIONS"
    if has_topics:
        return "PRACTICE"
    return None


ACTIVITY_FOR_SIGNAL = {
    "LESSON": "LESSON",
    "PRACTICE": "PRACTICE",
    "REVISION": "REVISION",
    "PAST_QUESTIONS": "PAST_QUESTIONS",
    "MOCK_EXAM": "MOCK_EXAM",
}
