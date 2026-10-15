from app.services.planner.mode import scope_ordinal
from app.services.planner.term_context import (
    TermRange,
    default_term,
    resolve_term_context,
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
    ctx = {"kind": "holiday", "previous": TermRange("s", "THIRD", "a", "b"), "next": None}
    assert default_term(ctx) == "THIRD"
    assert default_term({"kind": "holiday", "previous": None, "next": None}) == "FIRST"
