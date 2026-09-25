from datetime import UTC, datetime, timedelta

from app.core.errors import RateLimited
from app.core.rate_limit import hit, reset_memory
from app.domain import can
from app.services.assessments.grading import score_jamb_paper, waec_grade
from app.services.auth.rules import cron_decision, current_streak, is_session_revoked
from app.services.billing.rules import (
    add_months_utc,
    paystack_signature,
    settlement_outcome,
    signatures_match,
)
from app.services.learning.evidence import (
    channel_score,
    question_outcome,
    recency_weight,
)
from app.services.learning.graph import build_graph
from app.services.planner.days import add_days, days_between, iso_weekday
from app.services.planner.mode import (
    compute_runway_start,
    exam_share,
    resolve_plan_mode,
)
from app.services.planner.slots import Availability, build_slots, day_budget
from app.services.planner.term_context import (
    TermRange,
    resolve_term_context,
    term_header_label,
)
from app.services.planner.term_plan import PlannerInput, PlannerSubject, plan_window
from app.services.planner.topics import (
    PlanTopic,
    calendar_topic_id,
    select_exam_topics,
    select_term_topics,
)
from app.services.provider.rules import (
    cache_key,
    fingerprint,
    next_circuit,
    should_saturate,
)
from app.services.srs.scheduler import INITIAL_EASE, CardSchedule, review_card


def test_waec_boundaries():
    assert waec_grade(75)[0] == "A1"
    assert waec_grade(74.9)[0] == "B2"
    assert waec_grade(39)[0] == "F9"
    assert waec_grade(50)[2] is True
    assert waec_grade(45)[2] is False


def test_jamb_score_is_sum_of_subject_percentages():
    paper = score_jamb_paper(
        [
            ("eng", "English", 30, 60),
            ("phy", "Physics", 20, 40),
            ("che", "Chemistry", 40, 40),
            ("bio", "Biology", 0, 40),
        ]
    )
    assert paper.score == 200.0
    assert paper.percentage == 50.0
    assert paper.band == "Good"


def test_evidence_prior_blocks_a_perfect_score():
    score, confidence = channel_score(1.0, 1.0)
    assert abs(score - 2.8 / 5) < 1e-9
    assert abs(confidence - 1 / 5) < 1e-9
    assert question_outcome("ADVANCED", False) == 0.35
    assert question_outcome("BASIC", False) == 0.0
    assert abs(recency_weight(45) - 0.5) < 1e-9


def test_entitlements_and_session_revocation():
    assert can("FREEMIUM", "flashcards") is False
    assert can("STANDARD", "flashcards") is True
    assert can("PREMIUM", "advancedAnalytics") is True
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    assert is_session_revoked(False, None, 10) is True
    assert is_session_revoked(True, None, 10) is False
    assert is_session_revoked(True, moment, int(moment.timestamp())) is True
    assert is_session_revoked(True, moment, int(moment.timestamp()) + 5) is False


def test_cron_fails_open_and_rejects_a_wrong_bearer():
    assert cron_decision("GET", "x" * 16, "x" * 16) == 405
    assert cron_decision("POST", None, None) == 204
    assert cron_decision("POST", "short", None) == 204
    assert cron_decision("POST", "a" * 16, "wrong-bearer-value") == 401
    assert cron_decision("POST", "a" * 16, "a" * 16) is None


def test_billing_month_clamp_and_signature():
    start = datetime(2026, 1, 31, tzinfo=UTC)
    assert add_months_utc(start, 1).day == 28
    body = b'{"event":"charge.success"}'
    signature = paystack_signature("secret", body)
    assert signatures_match("secret", body, signature)
    assert signatures_match("secret", body + b" ", signature) is False
    assert (
        settlement_outcome(
            row_status="PENDING",
            row_amount=250000,
            row_currency="NGN",
            transaction_status="success",
            transaction_amount=250000,
            transaction_currency="NGN",
        )
        == "activate"
    )
    assert (
        settlement_outcome(
            row_status="ACTIVE",
            row_amount=1,
            row_currency="NGN",
            transaction_status="success",
            transaction_amount=1,
            transaction_currency="NGN",
        )
        == "already-applied"
    )
    assert (
        settlement_outcome(
            row_status="PENDING",
            row_amount=10,
            row_currency="NGN",
            transaction_status="success",
            transaction_amount=9,
            transaction_currency="NGN",
        )
        == "amount-mismatch"
    )


def test_provider_rules():
    assert (
        cache_key(" English|Language ", "WAEC", 2019) == "english%7Clanguage|WAEC|2019"
    )
    assert should_saturate(1, 49, 49) is True
    assert should_saturate(1, 50, 9) is True
    assert should_saturate(12, 50, 20) is True
    assert should_saturate(3, 50, 20) is False
    assert next_circuit(402, "", "OK") == "EXHAUSTED"
    assert next_circuit(404, "", "OK") == "OK"
    assert next_circuit(403, "out of credit", "OK") == "EXHAUSTED"
    assert next_circuit(401, "", "OK") == "BLOCKED"
    left = fingerprint("Hello", {"B": "two", "A": "one"})
    right = fingerprint("hello", {"A": "one", "B": "two"})
    assert left == right


def test_spaced_repetition_new_easy_graduates():
    now = datetime(2026, 9, 22, tzinfo=UTC)
    card = review_card(CardSchedule(), "EASY", now)
    assert card.state == "REVIEW"
    assert card.repetitions == 1
    assert card.due_at == now + timedelta(days=2)
    again = review_card(card, "AGAIN", now)
    assert again.state == "RELEARNING"
    assert again.lapses == 1
    assert again.stability == 1.0
    assert again.ease_factor < INITIAL_EASE


def test_exam_share_and_slots():
    assert exam_share(150) == 0.1
    assert exam_share(120) == 0.1
    assert exam_share(42) == 0.5
    assert abs(exam_share(90) - 0.253846) < 0.001
    assert resolve_plan_mode("SS2", "2026-10-01", True) == "TERM"
    assert resolve_plan_mode("SS3", "2026-10-01", True) == "EXAM"
    assert resolve_plan_mode("SS3", "2026-10-01", False) == "BLENDED"
    availability = Availability([1, 2, 3, 4, 6], 45, 120)
    assert day_budget("2026-09-14", availability) == 45
    assert day_budget("2026-09-19", availability) == 120
    monday = build_slots("2026-09-14", 1, availability)
    assert [(slot.minutes, slot.short) for slot in monday] == [(30, False), (15, True)]
    assert iso_weekday("2026-09-14") == 1
    assert days_between("2026-08-01", "2026-09-23") == 53
    assert compute_runway_start("2026-08-01", "2026-09-23") == "2026-09-10"


def test_streak_requires_the_end_day():
    assert current_streak({"2026-09-20", "2026-09-21"}, "2026-09-22") == 0
    assert current_streak({"2026-09-20", "2026-09-21", "2026-09-22"}, "2026-09-22") == 3


def test_rate_limit_allows_the_opening_request():
    reset_memory()
    hit("test-window", 1, 60)
    try:
        hit("test-window", 1, 60)
        raised = False
    except RateLimited:
        raised = True
    assert raised
    reset_memory()
    hit("zero", 0, 60)


def _topic(topic_id: str, **overrides) -> PlanTopic:
    data = dict(
        id=topic_id,
        subject_id="maths",
        title=f"Topic {topic_id}",
        slug=topic_id,
        order_index=0,
        estimated_minutes=45,
        waec_weight=1,
        jamb_weight=1,
        prerequisite_topic_id=None,
        class_level="SS1",
        term="FIRST",
    )
    data.update(overrides)
    return PlanTopic(**data)


def test_plan_window_stays_inside_the_class():
    ss1 = [_topic(f"a{n}", order_index=n) for n in range(1, 7)]
    later = [_topic("b1", class_level="SS2", order_index=1)]
    first = TermRange("2026/2027", "FIRST", "2026-09-07", "2026-11-29")
    context = resolve_term_context("2026-09-14", [first])
    output = plan_window(
        PlannerInput(
            today="2026-09-14",
            plan_start="2026-09-14",
            mode="TERM",
            class_level="SS1",
            target_date=None,
            term_context=context,
            availability=Availability([1, 2, 3, 4, 6], 30, 60),
            subjects=[PlannerSubject("maths", "Mathematics", [*ss1, *later])],
            graph=build_graph([], []),
            state={},
        )
    )
    assert output["plannedThrough"] == "2026-09-27"
    ids = {item.topic_id for item in output["items"]}
    assert "b1" not in ids
    assert all(
        item.activity_type not in {"PAST_QUESTIONS", "MOCK_EXAM"}
        for item in output["items"]
    )
    assert output["runwayStart"] is None
    assert term_header_label(context).startswith("1st term")


def test_term_topic_selection_mid_term():
    topics = [_topic(f"t{n}", order_index=n) for n in range(1, 7)]
    context = {
        "kind": "in_term",
        "source": "configured",
        "current": TermRange("2026/2027", "FIRST", "2026-09-07", "2026-12-15"),
        "weekOfTerm": 6,
        "totalWeeks": 12,
        "weeksLeft": 6,
    }
    selection = select_term_topics(
        subject_id="maths",
        class_level="SS1",
        term_context=context,
        topics=topics,
        graph=build_graph([], []),
        state={},
        pretest_passed=set(),
        position_topic_id=None,
        carry_over=[],
    )
    reasons = [f"{item.reason}:{item.topic.id}" for item in selection.candidates]
    assert reasons[0] == "CURRENT:t3"
    assert calendar_topic_id(topics, "SS1", context) == "t3"
    ranked = select_exam_topics(
        [
            _topic("a", waec_weight=1, jamb_weight=1),
            _topic("b", waec_weight=3, jamb_weight=2),
            _topic("later", class_level="SS3", waec_weight=9),
        ],
        "SS2",
        {},
    )
    assert [item.topic.id for item in ranked] == ["b", "a"]
    assert add_days("2026-09-14", 13) == "2026-09-27"
