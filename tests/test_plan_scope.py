from app.services.learning.graph import GraphEdge, GraphNode, build_graph
from app.services.planner.mode import scope_ordinal
from app.services.planner.term_context import (
    TermRange,
    default_term,
    resolve_term_context,
)
from app.services.planner.topics import (
    PlanTopic,
    calendar_topic_id,
    select_term_topics,
)


def test_scope_ordinal_orders_the_nine_slots():
    assert scope_ordinal("SS1", "FIRST") == 0
    assert scope_ordinal("SS1", "THIRD") == 2
    assert scope_ordinal("SS2", "FIRST") == 3
    assert scope_ordinal("SS3", "THIRD") == 8
    assert scope_ordinal("SS1", "THIRD") < scope_ordinal("SS2", "FIRST")


def _terms():
    return [
        TermRange("2026/2027", "FIRST", "2026-09-07", "2026-12-15"),
        TermRange("2026/2027", "SECOND", "2027-01-06", "2027-04-10"),
    ]


def test_default_term_is_the_live_term_in_term():
    ctx = resolve_term_context("2026-10-15", _terms())
    assert default_term(ctx) == "FIRST"


def test_default_term_is_the_upcoming_term_in_a_holiday():
    ctx = resolve_term_context("2026-12-20", _terms())
    assert ctx["kind"] == "holiday"
    assert default_term(ctx) == "SECOND"


def test_default_term_falls_back_to_the_previous_then_first():
    previous = TermRange("s", "THIRD", "a", "b")
    ctx = {"kind": "holiday", "previous": previous, "next": None}
    assert default_term(ctx) == "THIRD"
    assert default_term({"kind": "holiday", "previous": None, "next": None}) == "FIRST"

def _topic(topic_id, class_level="SS1", term="FIRST", order=1):
    return PlanTopic(
        id=topic_id,
        subject_id="maths",
        title=topic_id,
        slug=topic_id,
        order_index=order,
        estimated_minutes=30,
        waec_weight=1,
        jamb_weight=1,
        prerequisite_topic_id=None,
        class_level=class_level,
        term=term,
    )


def _graph(topics, edges=()):
    nodes = [
        GraphNode(t.id, t.subject_id, t.title, t.slug, t.order_index) for t in topics
    ]
    graph_edges = [GraphEdge(f"e{i}", s, t) for i, (s, t) in enumerate(edges)]
    return build_graph(nodes, graph_edges)


def _in_term(term, week, total=12):
    return {
        "kind": "in_term",
        "source": "configured",
        "current": TermRange("2026/2027", term, "2026-09-07", "2026-12-15"),
        "weekOfTerm": week,
        "totalWeeks": total,
        "weeksLeft": total - week,
    }


def _select(
    topics, edges=(), *, class_level="SS1", term="FIRST", follow=True, ctx=None,
    position=None,
):
    return select_term_topics(
        subject_id="maths",
        class_level=class_level,
        term=term,
        follow_calendar=follow,
        term_context=ctx or _in_term("FIRST", 1),
        topics=topics,
        graph=_graph(topics, edges),
        state={},
        pretest_passed=set(),
        position_topic_id=position,
        carry_over=[],
    )


def test_selection_never_leaves_the_scope():
    topics = [
        _topic("a1", order=1),
        _topic("a2", order=2),
        _topic("later-term", term="SECOND"),
        _topic("later-class", class_level="SS2"),
    ]
    # a later-term topic is a prerequisite of a current one: it must be ignored
    selection = _select(topics, edges=[("later-term", "a1")])
    ids = {c.topic.id for c in selection.candidates}
    assert ids <= {"a1", "a2"}
    assert "later-term" not in ids and "later-class" not in ids


def test_gap_fill_reaches_back_to_an_earlier_term():
    topics = [_topic("old", term="FIRST"), _topic("now", term="SECOND")]
    selection = _select(
        topics,
        edges=[("old", "now")],
        term="SECOND",
        ctx=_in_term("SECOND", 1),
    )
    reasons = [(c.reason, c.topic.id) for c in selection.candidates]
    assert ("GAP_FILL", "old") in reasons
    assert ("CURRENT", "now") in reasons


def test_empty_scope_gives_no_candidates():
    selection = _select([_topic("other", term="SECOND")], term="FIRST")
    assert selection.candidates == []
    assert selection.class_index == -1


def test_live_term_follows_the_calendar_only_for_the_home_class():
    topics = [_topic(f"t{n}", order=n) for n in range(1, 7)]
    ctx = _in_term("FIRST", 6)
    assert calendar_topic_id(topics, "SS1", "FIRST", True, ctx) == "t3"
    # a plan for another class, or another term, starts at the first topic
    assert calendar_topic_id(topics, "SS1", "FIRST", False, ctx) == "t1"
    other = [_topic(f"s{n}", term="SECOND", order=n) for n in range(1, 4)]
    assert calendar_topic_id(other, "SS1", "SECOND", True, ctx) == "s1"


def test_holiday_revises_the_end_of_the_previous_term_only():
    topics = [_topic(f"t{n}", order=n) for n in range(1, 4)]
    holiday = {
        "kind": "holiday",
        "source": "configured",
        "previous": TermRange("2026/2027", "FIRST", "2026-09-07", "2026-12-15"),
        "next": TermRange("2026/2027", "SECOND", "2027-01-06", "2027-04-10"),
    }
    assert calendar_topic_id(topics, "SS1", "FIRST", True, holiday) == "t3"
    second = [
        _topic("s1", term="SECOND", order=1),
        _topic("s2", term="SECOND", order=2),
    ]
    assert calendar_topic_id(second, "SS1", "SECOND", True, holiday) == "s1"


def test_a_position_outside_the_scope_is_ignored():
    topics = [
        _topic("a1", order=1),
        _topic("a2", order=2),
        _topic("b1", term="SECOND"),
    ]
    selection = _select(topics, position="b1")
    assert selection.term_topics
    assert all(t.term == "FIRST" for t in selection.term_topics)
    assert selection.class_index == 0
