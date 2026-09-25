"""Shared product constants that must stay database-free."""

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
ENTITLEMENTS = {
    "flashcards": "STANDARD",
    "studyPlanner": "STANDARD",
    "premiumLibrary": "PREMIUM",
    "advancedAnalytics": "PREMIUM",
}
PRICES_KOBO = {
    ("FREEMIUM", "MONTHLY"): 0,
    ("FREEMIUM", "YEARLY"): 0,
    ("STANDARD", "MONTHLY"): 250_000,
    ("STANDARD", "YEARLY"): 2_400_000,
    ("PREMIUM", "MONTHLY"): 500_000,
    ("PREMIUM", "YEARLY"): 5_000_000,
}

DEVICE_LIMIT = 2
DEVICE_TOUCH_SECONDS = 15 * 60
PROFILE_TTL_MS = 60_000

# Exam sittings (month, day) in West Africa Time.
# Years until sitting: SS3=0, SS2=1, SS1=2.
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
    required = ENTITLEMENTS[feature]
    return TIER_RANK[tier] >= TIER_RANK[required]


def entitlement_denial(feature: str) -> dict:
    required = ENTITLEMENTS[feature]
    return {
        "error": f"This feature is part of {TIER_DISPLAY[required]}.",
        "requiredTier": required,
        "feature": feature,
    }


def is_purchasable(tier: str) -> bool:
    return tier != "FREEMIUM"


def price_kobo(tier: str, period: str) -> int:
    return PRICES_KOBO[(tier, period)]
