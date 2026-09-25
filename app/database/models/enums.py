"""Postgres enum types shared by the ScholarsCrib tables."""

from sqlalchemy import Enum


def pg_enum(name: str, *values: str) -> Enum:
    return Enum(*values, name=name, native_enum=True, create_constraint=True)


Role = pg_enum("Role", "STUDENT", "TEACHER", "ADMIN")
ClassLevel = pg_enum("ClassLevel", "SS1", "SS2", "SS3")
Track = pg_enum("Track", "SCIENCE", "ARTS", "COMMERCIAL")
TrackCategory = pg_enum(
    "TrackCategory", "CORE", "SCIENCE", "ARTS", "COMMERCIAL", "VOCATIONAL"
)
SubscriptionTier = pg_enum("SubscriptionTier", "FREEMIUM", "STANDARD", "PREMIUM")
BillingPeriod = pg_enum("BillingPeriod", "MONTHLY", "YEARLY")
SubscriptionSource = pg_enum("SubscriptionSource", "PAYSTACK", "COMP")
SubscriptionStatus = pg_enum(
    "SubscriptionStatus", "PENDING", "ACTIVE", "FAILED", "ABANDONED", "REVOKED"
)
Term = pg_enum("Term", "FIRST", "SECOND", "THIRD")
ExamType = pg_enum("ExamType", "WAEC", "JAMB", "NECO", "CUSTOM")
QuestionType = pg_enum("QuestionType", "OBJECTIVE", "THEORY", "FILL_IN_BLANK")
Difficulty = pg_enum("Difficulty", "BASIC", "INTERMEDIATE", "ADVANCED")
QuestionProvider = pg_enum("QuestionProvider", "SDASH")
ProviderFetchStatus = pg_enum("ProviderFetchStatus", "PENDING", "SATURATED", "FAILED")
ProviderQuestionStatus = pg_enum(
    "ProviderQuestionStatus", "PENDING", "PROMOTED", "REJECTED"
)
ProviderCircuitState = pg_enum("ProviderCircuitState", "OK", "EXHAUSTED", "BLOCKED")
AssessmentType = pg_enum(
    "AssessmentType",
    "TOPIC_QUIZ",
    "SUBJECT_TEST",
    "PAST_PAPER",
    "MOCK_EXAM",
    "CBT_PRACTICE",
    "CUSTOM",
)
ProgressStatus = pg_enum("ProgressStatus", "NOT_STARTED", "IN_PROGRESS", "COMPLETED")
AttemptStatus = pg_enum(
    "AttemptStatus", "IN_PROGRESS", "COMPLETED", "ABANDONED", "TIMED_OUT"
)
PlanItemActivity = pg_enum(
    "PlanItemActivity", "LESSON", "PRACTICE", "REVISION", "MOCK_EXAM", "PAST_QUESTIONS"
)
PlanItemStatus = pg_enum("PlanItemStatus", "PENDING", "COMPLETED", "SKIPPED", "MISSED")
PlanCompletionSource = pg_enum("PlanCompletionSource", "AUTO", "MANUAL")
MasteryLevel = pg_enum("MasteryLevel", "WEAK", "DEVELOPING", "COMPETENT", "STRONG")
FlashcardType = pg_enum(
    "FlashcardType",
    "DEFINITION",
    "FORMULA",
    "IMAGE",
    "DIAGRAM",
    "FILL_IN_BLANK",
    "COMPARE_CONTRAST",
    "TRUE_FALSE",
    "SCENARIO",
    "PROCESS",
)
FlashcardSource = pg_enum("FlashcardSource", "AUTHORED", "LESSON", "AI")
FlashcardState = pg_enum("FlashcardState", "NEW", "LEARNING", "REVIEW", "RELEARNING")
ReviewRating = pg_enum("ReviewRating", "AGAIN", "HARD", "GOOD", "EASY")
EdgeKind = pg_enum("EdgeKind", "PREREQUISITE", "STRONG_RELATED", "RELATED")
LearningEventKind = pg_enum(
    "LearningEventKind",
    "QUESTION_ANSWERED",
    "QUIZ_ABANDONED",
    "LESSON_BLOCK_COMPLETED",
    "LESSON_COMPLETED",
    "CARD_REVIEWED",
    "PRETEST_PASSED",
)
MaterialType = pg_enum("MaterialType", "PDF", "IMAGE", "VIDEO", "LINK")
AnnouncementStatus = pg_enum(
    "AnnouncementStatus", "QUEUED", "SENDING", "SENT", "CANCELLED"
)
DeliveryStatus = pg_enum(
    "DeliveryStatus", "PENDING", "SENT", "FAILED", "GONE", "CANCELLED"
)
SchoolType = pg_enum("SchoolType", "PUBLIC", "PRIVATE")
