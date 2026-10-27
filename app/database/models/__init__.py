"""ScholarsCrib tables, grouped by domain."""

from app.database.models.admin import Admin, AdminAudit
from app.database.models.admin_chat import AdminChatMessage, AdminChatThread
from app.database.models.assessment import (
    Assessment,
    AssessmentAttempt,
    AssessmentQuestion,
    QuestionResponse,
)
from app.database.models.billing import PaystackEvent, Subscription
from app.database.models.chat import (
    ChatFeedback,
    ChatMessage,
    ChatThread,
    RagIndexJob,
)
from app.database.models.coverage import CoverageQuestion
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
    DailySession,
    LearningEvent,
    PerformanceMetric,
    PredictionLog,
    StudentAchievement,
    StudentProgress,
    TopicMastery,
)
from app.database.models.notification import (
    Announcement,
    AnnouncementDelivery,
    AnnouncementDismissal,
    InAppNotification,
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
    "AdminChatMessage",
    "AdminChatThread",
    "Announcement",
    "AnnouncementDelivery",
    "AnnouncementDismissal",
    "Assessment",
    "AssessmentAttempt",
    "AssessmentQuestion",
    "AuthSession",
    "ChatFeedback",
    "ChatMessage",
    "ChatThread",
    "CoverageQuestion",
    "CurriculumLevel",
    "DailySession",
    "Flashcard",
    "FlashcardDeck",
    "FlashcardEnrollment",
    "FlashcardReview",
    "FlashcardReviewLog",
    "InAppNotification",
    "JambCombination",
    "LearningEvent",
    "PredictionLog",
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
    "RagIndexJob",
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
