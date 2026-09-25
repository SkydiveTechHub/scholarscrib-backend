from dataclasses import dataclass

from app.services.learning.evidence import GATE, TARGET
from app.services.learning.graph import KnowledgeGraph, incoming_edges
from app.services.learning.mastery import TopicStateMap
from app.services.planner.days import DayKey
from app.services.planner.mode import at_or_below_class


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


def _locate_by_calendar(allowed: list[PlanTopic], class_level: str, ctx: dict) -> dict:
    if ctx["kind"] == "in_term":
        term_topics = _scope(allowed, class_level, ctx["current"].term)
        if not term_topics:
            return {"termTopics": term_topics, "classIndex": -1, "preview": []}
        class_index = min(
            len(term_topics) - 1,
            int(((ctx["weekOfTerm"] - 1) / ctx["totalWeeks"]) * len(term_topics)),
        )
        return {
            "termTopics": term_topics,
            "classIndex": class_index,
            "preview": term_topics[class_index + 1 : class_index + 2],
        }
    previous = ctx.get("previous")
    nxt = ctx.get("next")
    term_topics = _scope(allowed, class_level, previous.term) if previous else []
    preview = []
    if nxt and nxt.term != "FIRST":
        preview = _scope(allowed, class_level, nxt.term)[:2]
    return {
        "termTopics": term_topics,
        "classIndex": len(term_topics) - 1,
        "preview": preview,
    }


def _locate_class(
    allowed: list[PlanTopic], class_level: str, ctx: dict, position_topic_id: str | None
) -> dict:
    position = (
        next((topic for topic in allowed if topic.id == position_topic_id), None)
        if position_topic_id
        else None
    )
    if position:
        term_topics = _scope(allowed, position.class_level, position.term)
        class_index = next(
            (
                index
                for index, topic in enumerate(term_topics)
                if topic.id == position.id
            ),
            -1,
        )
        return {
            "termTopics": term_topics,
            "classIndex": class_index,
            "preview": term_topics[class_index + 1 : class_index + 2]
            if class_index >= 0
            else [],
        }
    return _locate_by_calendar(allowed, class_level, ctx)


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
    term_context: dict,
    topics: list[PlanTopic],
    graph: KnowledgeGraph,
    state: TopicStateMap,
    pretest_passed: set[str],
    position_topic_id: str | None,
    carry_over: list[CarryOver],
    carry_over_only: bool = False,
) -> SubjectSelection:
    allowed = [
        topic for topic in topics if at_or_below_class(topic.class_level, class_level)
    ]
    by_id = {topic.id: topic for topic in allowed}

    def unmastered(topic: PlanTopic) -> bool:
        return _mastery(state, topic.id) < TARGET

    located = _locate_class(allowed, class_level, term_context, position_topic_id)
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
        and _mastery(state, topic.id) < TARGET
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
    topics: list[PlanTopic], class_level: str, ctx: dict
) -> str | None:
    allowed = [
        topic for topic in topics if at_or_below_class(topic.class_level, class_level)
    ]
    located = _locate_by_calendar(allowed, class_level, ctx)
    if located["classIndex"] < 0:
        return None
    return located["termTopics"][located["classIndex"]].id
