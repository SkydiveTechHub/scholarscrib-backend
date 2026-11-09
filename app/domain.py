"""Shared product constants that must stay database-free.

Plan prices and entitlement *values* live in BillingPlan / BillingPlanEntitlement.
This module keeps stable enums, rank helpers, and seed-aligned fallbacks for tests
and cold starts before the catalog is seeded.
"""

from typing import Literal, get_args

type NigerianState = Literal[
    "Abia",
    "Adamawa",
    "Akwa Ibom",
    "Anambra",
    "Bauchi",
    "Bayelsa",
    "Benue",
    "Borno",
    "Cross River",
    "Delta",
    "Ebonyi",
    "Edo",
    "Ekiti",
    "Enugu",
    "FCT Abuja",
    "Gombe",
    "Imo",
    "Jigawa",
    "Kaduna",
    "Kano",
    "Katsina",
    "Kebbi",
    "Kogi",
    "Kwara",
    "Lagos",
    "Nasarawa",
    "Niger",
    "Ogun",
    "Ondo",
    "Osun",
    "Oyo",
    "Plateau",
    "Rivers",
    "Sokoto",
    "Taraba",
    "Yobe",
    "Zamfara",
]
NIGERIAN_STATES = get_args(NigerianState.__value__)

type ClassLevel = Literal["SS1", "SS2", "SS3"]
type Track = Literal["SCIENCE", "ARTS", "COMMERCIAL"]
type Term = Literal["FIRST", "SECOND", "THIRD"]

CLASS_LEVELS = get_args(ClassLevel.__value__)
TERMS = get_args(Term.__value__)
TERM_LABELS = {"FIRST": "1st term", "SECOND": "2nd term", "THIRD": "3rd term"}
TRACKS = get_args(Track.__value__)
PHONE_PATTERN = r"^(\+234|0)[789]\d{9}$"

TIER_RANK = {"FREEMIUM": 0, "STANDARD": 1, "PREMIUM": 2}
TIER_DISPLAY = {"FREEMIUM": "Free", "STANDARD": "Standard", "PREMIUM": "Premium"}

# Fallback when the DB catalog is empty (tests / pre-seed). Matches seed matrix.
# Beta opens are intentionally empty — gates follow paid plan ranks.
PAID_ENTITLEMENTS = {
    "flashcards": "STANDARD",
    "studyPlanner": "STANDARD",
    "premiumLibrary": "PREMIUM",
    "advancedAnalytics": "PREMIUM",
    "aiTutor": "FREEMIUM",  # all plans have tutor; daily cap differs
}
BETA_OPEN_FEATURES: frozenset[str] = frozenset()
ENTITLEMENTS = dict(PAID_ENTITLEMENTS)

PRICES_KOBO = {
    ("FREEMIUM", "MONTHLY"): 0,
    ("STANDARD", "MONTHLY"): 300_000,
    ("PREMIUM", "MONTHLY"): 1_000_000,
}

AI_TUTOR_DAILY_FALLBACK = {"FREEMIUM": 2, "STANDARD": 20, "PREMIUM": 60}
PAST_QUESTIONS_DETAIL_FALLBACK = {
    "FREEMIUM": "answers",
    "STANDARD": "explanation",
    "PREMIUM": "ai_explanation",
}
PERFORMANCE_SCOPE_FALLBACK = {
    "FREEMIUM": "overview",
    "STANDARD": "overview",
    "PREMIUM": "by_subject",
}

DEVICE_LIMIT = 2
DEVICE_TOUCH_SECONDS = 15 * 60
PROFILE_TTL_MS = 60_000

EXAM_SITTINGS = {"WAEC": (5, 3), "NECO": (6, 7), "JAMB": (4, 12)}
EXAM_YEAR_FLOOR = 2000

JAMB_ENGLISH_CODE = "ENG"
JAMB_ENGLISH_QUESTIONS = 60
JAMB_SUBJECT_QUESTIONS = 40
JAMB_OTHER_SUBJECTS = 3
JAMB_TOTAL_QUESTIONS = 180
JAMB_DURATION_MINUTES = 120
JAMB_MARKS_PER_SUBJECT = 100
JAMB_TOTAL_MARKS = 400

MINUTES_PER_QUESTION = 1.5
SUBMIT_GRACE_SECONDS = 120
UNTIMED_STALE_HOURS = 24
SEEN_QUESTION_DAYS = 30

GRADE_BOUNDARIES = (
    (75, "A1", "Excellent", True),
    (70, "B2", "Very Good", True),
    (65, "B3", "Good", True),
    (60, "C4", "Credit", True),
    (55, "C5", "Credit", True),
    (50, "C6", "Credit", True),
    (45, "D7", "Pass", False),
    (40, "E8", "Pass", False),
    (0, "F9", "Fail", False),
)


def can(tier: str, feature: str) -> bool:
    """Sync fallback. Prefer catalog.can_gate when a DB session is available."""
    required = ENTITLEMENTS.get(feature)
    if required is None:
        return True
    return TIER_RANK.get(tier, 0) >= TIER_RANK.get(required, 0)


def entitlement_denial(feature: str) -> dict:
    required = ENTITLEMENTS.get(feature, "PREMIUM")
    return {
        "error": f"This feature is part of {TIER_DISPLAY.get(required, required)}.",
        "requiredTier": required,
        "feature": feature,
    }


def is_purchasable(tier: str) -> bool:
    return tier in {"STANDARD", "PREMIUM"}


def price_kobo(tier: str, period: str = "MONTHLY") -> int:
    if period != "MONTHLY":
        period = "MONTHLY"
    return PRICES_KOBO.get((tier, period), 0)


# Prefer app.services.billing.catalog for billing callers; these re-export
# the catalog SoT so non-billing code cannot drift.
def plan_code_for_tier(tier: str) -> str:
    from app.services.billing.catalog import plan_code_for_tier as _impl

    return _impl(tier)


def tier_for_plan_code(code: str) -> str:
    from app.services.billing.catalog import tier_for_plan_code as _impl

    return _impl(code)
