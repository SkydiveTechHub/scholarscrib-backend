"""Shared product constants that must stay database-free.

Plan prices and entitlement *values* live in BillingPlan / BillingPlanEntitlement.
This module keeps stable enums, rank helpers, and seed-aligned fallbacks for tests
and cold starts before the catalog is seeded.
"""

from typing import Literal, TypedDict, get_args

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
# Center "levels served" (JSONB). Broader than curriculum ClassLevel — includes
# post-SS3 resitters who still use the platform (PRD §5).
type CenterLevel = Literal["SS1", "SS2", "SS3", "POST_SS3"]
type Track = Literal["SCIENCE", "ARTS", "COMMERCIAL"]
type Term = Literal["FIRST", "SECOND", "THIRD"]

CLASS_LEVELS = get_args(ClassLevel.__value__)
CENTER_LEVELS = get_args(CenterLevel.__value__)
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

# Tutor Centers (R-10 / D1 / D2). Free blocks accepting the 5th active student.
CENTER_FREE_ACTIVE_STUDENT_LIMIT = 5
CENTER_PRO_YEARLY_KOBO = 1_000_000  # ₦10,000 — Pro
CENTER_TEAM_YEARLY_KOBO = 1_500_000  # ₦15,000
# Facilitator seats by paid plan. Free is solo (owner-only).
CENTER_PRO_MAX_FACILITATORS = 3
CENTER_TEAM_MAX_FACILITATORS = 5
# Back-compat alias = Team cap (prefer center_max_facilitators(plan)).
CENTER_MAX_FACILITATORS = CENTER_TEAM_MAX_FACILITATORS
CENTER_MAX_STAFF = 1 + CENTER_TEAM_MAX_FACILITATORS


def center_max_facilitators(plan: str | None) -> int:
    """Facilitator seat cap for a center plan code (FREE / PRO / TEAM)."""
    code = (plan or "FREE").upper()
    if code == "TEAM":
        return CENTER_TEAM_MAX_FACILITATORS
    if code == "PRO":
        return CENTER_PRO_MAX_FACILITATORS
    return 0


def center_max_staff(plan: str | None) -> int:
    """Owner + facilitator seats for a center plan."""
    return 1 + center_max_facilitators(plan)


def center_allows_facilitators(plan: str | None) -> bool:
    return center_max_facilitators(plan) > 0


# Tutor Astra AI token pool (D10 / AI-5). Packs are one-off Paystack charges.
CENTER_ASTRA_TOKENS_PER_TURN = 1
CENTER_ASTRA_STARTER_TOKENS = 20  # credited once when the center is created


class CenterTokenPack(TypedDict):
    code: str
    label: str
    tokens: int
    amount_kobo: int


# Pack pricing (casual ~₦13/turn API cost). Net after gateway fee:
# 30 → ₦1,000 (~₦595 profit); 140 → ₦4,000 (~₦2,020); 400 → ₦10,000 (~₦4,550).
CENTER_TOKEN_PACKS: tuple[CenterTokenPack, ...] = (
    {
        "code": "astra_pack_30",
        "label": "30 tokens",
        "tokens": 30,
        "amount_kobo": 100_000,  # ₦1,000 (~₦33.33/turn)
    },
    {
        "code": "astra_pack_140",
        "label": "140 tokens",
        "tokens": 140,
        "amount_kobo": 400_000,  # ₦4,000 (~₦28.57/turn)
    },
    {
        "code": "astra_pack_400",
        "label": "400 tokens",
        "tokens": 400,
        "amount_kobo": 1_000_000,  # ₦10,000 (~₦25.00/turn)
    },
)

# Referral wallet (M7 / D11 / D12): 5% of referred student's paid term.
REFERRAL_COMMISSION_BPS = 500  # 5.00%
CENTER_WALLET_MIN_WITHDRAW_KOBO = 100_000  # ₦1,000 minimum withdraw request

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
