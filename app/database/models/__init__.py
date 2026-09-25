"""ScholarsCrib tables, grouped by domain."""

from app.database.models.admin import Admin, AdminAudit
from app.database.models.assessment import (
    Assessment,
    AssessmentAttempt,
    AssessmentQuestion,
    QuestionResponse,
)
from app.database.models.billing import PaystackEvent, Subscription
from app.database.models.curriculum import (
    CurriculumLevel,
    Lesson,
    LessonResource,
    Subject,
    SubjectResource,
    Subtopic,
    Topic,
    TopicEdge,
)
from app.database.models.flashcard import (
    Flashcard,
    FlashcardDeck,
    FlashcardEnrollment,
    FlashcardReview,
    FlashcardReviewLog,
)
from app.database.models.identity import (
    Account,
    AuthSession,
    School,
    User,
    UserDevice,
    VerificationToken,
)
from app.database.models.jamb import JambCombination
from app.database.models.learning import (
    Achievement,
    LearningEvent,
    PerformanceMetric,
    StudentAchievement,
    StudentProgress,
    TopicMastery,
)
from app.database.models.notification import (
    Announcement,
    AnnouncementDelivery,
    AnnouncementDismissal,
    NotificationPreference,
    PushSubscription,
    ReminderLog,
)
from app.database.models.planner import (
    AcademicTerm,
    StudyPlan,
    StudyPlanItem,
    StudyPlanPosition,
)
from app.database.models.provider import (
    ProviderCatalogue,
    ProviderFetch,
    ProviderQuestion,
    ProviderState,
)
from app.database.models.question import Question

__all__ = [
    "AcademicTerm",
    "Account",
    "Achievement",
    "Admin",
    "AdminAudit",
    "Announcement",
    "AnnouncementDelivery",
    "AnnouncementDismissal",
    "Assessment",
    "AssessmentAttempt",
    "AssessmentQuestion",
    "AuthSession",
    "CurriculumLevel",
    "Flashcard",
    "FlashcardDeck",
    "FlashcardEnrollment",
    "FlashcardReview",
    "FlashcardReviewLog",
    "JambCombination",
    "LearningEvent",
    "Lesson",
    "LessonResource",
    "NotificationPreference",
    "PaystackEvent",
    "PerformanceMetric",
    "ProviderCatalogue",
    "ProviderFetch",
    "ProviderQuestion",
    "ProviderState",
    "PushSubscription",
    "Question",
    "QuestionResponse",
    "ReminderLog",
    "School",
    "StudentAchievement",
    "StudentProgress",
    "StudyPlan",
    "StudyPlanItem",
    "StudyPlanPosition",
    "Subject",
    "SubjectResource",
    "Subscription",
    "Subtopic",
    "Topic",
    "TopicEdge",
    "TopicMastery",
    "User",
    "UserDevice",
    "VerificationToken",
]
