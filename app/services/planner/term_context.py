from dataclasses import dataclass

from app.domain import TERM_LABELS
from app.services.planner.days import DayKey, add_days, days_between, monday_of

COVERAGE_DAYS = 60


@dataclass
class TermRange:
    session: str
    term: str
    starts_on: DayKey
    ends_on: DayKey


def fallback_terms(today: DayKey) -> list[TermRange]:
    year = int(today[:4])
    ranges: list[TermRange] = []
    for session_year in (year - 1, year):
        session = f"{session_year}/{session_year + 1}"
        ranges.extend(
            [
                TermRange(
                    session, "FIRST", f"{session_year}-09-08", f"{session_year}-12-15"
                ),
                TermRange(
                    session,
                    "SECOND",
                    f"{session_year + 1}-01-06",
                    f"{session_year + 1}-04-10",
                ),
                TermRange(
                    session,
                    "THIRD",
                    f"{session_year + 1}-04-27",
                    f"{session_year + 1}-07-24",
                ),
            ]
        )
    return ranges


def _near(term: TermRange, today: DayKey, days: int) -> bool:
    return (
        days_between(today, term.starts_on) <= days
        and days_between(term.ends_on, today) <= days
    )


def resolve_term_context(today: DayKey, configured: list[TermRange]) -> dict:
    covered = any(_near(term, today, COVERAGE_DAYS) for term in configured)
    source = "configured" if covered else "fallback"
    terms = sorted(
        configured if covered else fallback_terms(today),
        key=lambda term: term.starts_on,
    )
    current = next(
        (term for term in terms if term.starts_on <= today <= term.ends_on), None
    )
    if current:
        first_monday = monday_of(current.starts_on)
        total_weeks = days_between(first_monday, current.ends_on) // 7 + 1
        week_of_term = days_between(first_monday, today) // 7 + 1
        return {
            "kind": "in_term",
            "source": source,
            "current": current,
            "weekOfTerm": week_of_term,
            "totalWeeks": total_weeks,
            "weeksLeft": total_weeks - week_of_term,
        }
    previous = next((term for term in reversed(terms) if term.ends_on < today), None)
    nxt = next((term for term in terms if term.starts_on > today), None)
    return {"kind": "holiday", "source": source, "previous": previous, "next": nxt}


def _term_name(term: TermRange) -> str:
    return f"{term.session} {TERM_LABELS[term.term]}"


def validate_term_ranges(ranges: list[TermRange]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for term in ranges:
        parts = term.session.split("/")
        if (
            len(parts) != 2
            or not all(part.isdigit() and len(part) == 4 for part in parts)
            or int(parts[1]) != int(parts[0]) + 1
        ):
            errors.append(f"{term.session}: a session looks like 2026/2027.")
        if term.ends_on <= term.starts_on:
            errors.append(
                f"{_term_name(term)}: the end date must be after the start date."
            )
        key = f"{term.session}:{term.term}"
        if key in seen:
            errors.append(f"{_term_name(term)} is already set.")
        seen.add(key)
    ordered = sorted(ranges, key=lambda term: term.starts_on)
    for index in range(1, len(ordered)):
        if ordered[index].starts_on <= ordered[index - 1].ends_on:
            earlier = _term_name(ordered[index - 1])
            errors.append(f"{_term_name(ordered[index])} overlaps {earlier}.")
    return errors


def term_header_label(ctx: dict) -> str:
    if ctx["kind"] == "in_term":
        current = ctx["current"]
        return (
            f"{TERM_LABELS[current.term]} · "
            f"Week {ctx['weekOfTerm']} of {ctx['totalWeeks']}"
        )
    previous = ctx.get("previous")
    if previous:
        return f"Holiday — revising {TERM_LABELS[previous.term]}"
    return "Holiday"


def has_term_coverage(
    configured: list[TermRange], today: DayKey, ahead_days: int = 30
) -> bool:
    until = add_days(today, ahead_days)
    return any(term.starts_on <= until and term.ends_on >= today for term in configured)
