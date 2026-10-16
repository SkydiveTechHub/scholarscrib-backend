from dataclasses import dataclass

from app.services.learning.evidence import GATE
from app.services.learning.graph import KnowledgeGraph, incoming_edges
from app.services.learning.mastery import TopicStateMap, is_mastered
from app.services.planner.days import DayKey
from app.services.planner.mode import at_or_below_class, scope_ordinal


@dataclass
class PlanTopic:
    id: str
    subject_id: str
    title: str
    slug: str
    order_index: int
    estimated_minutes: int
    waec_weight: float
    jamb_weight: float
    prerequisite_topic_id: str | None
    class_level: str
    term: str


@dataclass
class TopicCandidate:
    topic: PlanTopic
    reason: str
    carried_from: DayKey | None = None
    unlocks: str | None = None


@dataclass
class CarryOver:
    topic_id: str
    subject_id: str
    missed_on: DayKey


@dataclass
class SubjectSelection:
    subject_id: str
    term_topics: list[PlanTopic]
    class_index: int
    behind_by: int
    candidates: list[TopicCandidate]


def _mastery(state: TopicStateMap, topic_id: str) -> int:
    found = state.get(topic_id)
    return 0 if found is None else found.mastery


def _is_mastered(state: TopicStateMap, topic_id: str) -> bool:
    found = state.get(topic_id)
    return found is not None and is_mastered(found)


def _by_order(topic: PlanTopic) -> tuple:
    return (topic.order_index, topic.id)


def _scope(topics: list[PlanTopic], class_level: str, term: str) -> list[PlanTopic]:
    return sorted(
        [
            topic
            for topic in topics
            if topic.class_level == class_level and topic.term == term
        ],
        key=_by_order,
    )


def _before_scope(topic: PlanTopic, class_level: str, term: str) -> bool:
    return scope_ordinal(topic.class_level, topic.term) < scope_ordinal(
        class_level, term
    )


def _locate(
    term_topics: list[PlanTopic],
    term: str,
    ctx: dict,
    position_topic_id: str | None,
    follow_calendar: bool,
) -> dict:
    """Where the class is inside this scope's topics."""
    if not term_topics:
        return {"termTopics": [], "classIndex": -1, "preview": []}
    count = len(term_topics)
    index = next(
        (i for i, topic in enumerate(term_topics) if topic.id == position_topic_id),
        -1,
    )
    if index < 0:
        if follow_calendar and ctx["kind"] == "in_term" and ctx["current"].term == term:
            index = min(
                count - 1,
                int(((ctx["weekOfTerm"] - 1) / ctx["totalWeeks"]) * count),
            )
        elif (
            follow_calendar
            and ctx["kind"] == "holiday"
            and ctx.get("previous")
            and ctx["previous"].term == term
        ):
            index = count - 1
        else:
            index = 0
    return {
        "termTopics": term_topics,
        "classIndex": index,
        "preview": term_topics[index + 1 : index + 2],
    }


def _gaps_for(
    target: PlanTopic,
    graph: KnowledgeGraph,
    state: TopicStateMap,
    pretest_passed: set[str],
    by_id: dict[str, PlanTopic],
    term_ids: set[str],
) -> list[PlanTopic]:
    out: list[PlanTopic] = []
    visited: set[str] = set()

    def visit(topic_id: str) -> None:
        for edge in incoming_edges(graph, topic_id):
            if edge.kind != "PREREQUISITE" or edge.source in visited:
                continue
            visited.add(edge.source)
            prereq = by_id.get(edge.source)
            if prereq is None or prereq.id in term_ids:
                continue
            if _mastery(state, prereq.id) >= GATE or prereq.id in pretest_passed:
                continue
            visit(prereq.id)
            out.append(prereq)

    visit(target.id)
    return out


def select_term_topics(
    *,
    subject_id: str,
    class_level: str,
    term: str,
    follow_calendar: bool,
    term_context: dict,
    topics: list[PlanTopic],
    graph: KnowledgeGraph,
    state: TopicStateMap,
    pretest_passed: set[str],
    position_topic_id: str | None,
    carry_over: list[CarryOver],
    carry_over_only: bool = False,
) -> SubjectSelection:
    term_topics_all = _scope(topics, class_level, term)
    earlier = [t for t in topics if _before_scope(t, class_level, term)]
    by_id = {topic.id: topic for topic in [*earlier, *term_topics_all]}

    def unmastered(topic: PlanTopic) -> bool:
        return not _is_mastered(state, topic.id)

    located = _locate(
        term_topics_all, term, term_context, position_topic_id, follow_calendar
    )
    term_topics: list[PlanTopic] = located["termTopics"]
    class_index = located["classIndex"]
    preview: list[PlanTopic] = located["preview"]
    candidates: list[TopicCandidate] = []
    seen: set[str] = set()

    def add(
        topic: PlanTopic,
        reason: str,
        carried_from: DayKey | None = None,
        unlocks: str | None = None,
    ) -> None:
        if topic.id in seen:
            return
        seen.add(topic.id)
        candidates.append(TopicCandidate(topic, reason, carried_from, unlocks))

    for item in carry_over:
        topic = by_id.get(item.topic_id)
        if topic and item.subject_id == subject_id and unmastered(topic):
            add(topic, "CARRY_OVER", carried_from=item.missed_on)

    if not carry_over_only:
        current = term_topics[class_index] if class_index >= 0 else None
        current_needed = current if current and unmastered(current) else None
        catch_up = [
            topic for topic in term_topics[: max(0, class_index)] if unmastered(topic)
        ]
        term_ids = {topic.id for topic in term_topics}
        seeds = ([current_needed] if current_needed else []) + catch_up
        for topic in seeds:
            for gap in _gaps_for(topic, graph, state, pretest_passed, by_id, term_ids):
                add(gap, "GAP_FILL", unlocks=topic.title)
        if current_needed:
            add(current_needed, "CURRENT")
        for topic in catch_up:
            add(topic, "CATCH_UP")
        for topic in preview:
            if unmastered(topic):
                add(topic, "PREVIEW")

    first_unmastered = next(
        (index for index, topic in enumerate(term_topics) if unmastered(topic)), -1
    )
    if class_index < 0 or first_unmastered < 0:
        behind_by = 0
    else:
        behind_by = max(0, class_index - first_unmastered)
    return SubjectSelection(subject_id, term_topics, class_index, behind_by, candidates)


def select_exam_topics(
    topics: list[PlanTopic], class_level: str, state: TopicStateMap
) -> list[TopicCandidate]:
    def weight(topic: PlanTopic) -> float:
        return topic.waec_weight + topic.jamb_weight

    ranked = [
        topic
        for topic in topics
        if at_or_below_class(topic.class_level, class_level)
        and not _is_mastered(state, topic.id)
    ]
    ranked.sort(
        key=lambda topic: (
            -weight(topic),
            _mastery(state, topic.id),
            topic.order_index,
            topic.id,
        )
    )
    return [TopicCandidate(topic, "EXAM") for topic in ranked]


def calendar_topic_id(
    topics: list[PlanTopic],
    class_level: str,
    term: str,
    follow_calendar: bool,
    ctx: dict,
) -> str | None:
    term_topics = _scope(topics, class_level, term)
    located = _locate(term_topics, term, ctx, None, follow_calendar)
    if located["classIndex"] < 0:
        return None
    return located["termTopics"][located["classIndex"]].id
