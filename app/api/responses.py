"""JSON response models for the student, admin, and cron routes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ApiOut(BaseModel):
    model_config = ConfigDict(extra="allow")


class OkOut(ApiOut):
    ok: bool = True


class MessageOut(ApiOut):
    message: str


class IdOut(ApiOut):
    id: str


class RegisterUserOut(ApiOut):
    id: str
    email: str | None = None
    firstName: str | None = None
    lastName: str | None = None
    classLevel: str | None = None
    track: str | None = None


class RegisterOut(ApiOut):
    message: str
    user: RegisterUserOut


class SessionUserOut(ApiOut):
    id: str
    email: str | None = None
    firstName: str | None = None
    lastName: str | None = None
    classLevel: str | None = None
    track: str | None = None
    state: str | None = None
    tier: str | None = None
    image: str | None = None
    deviceId: str | None = None
    role: str | None = None
    hasPassword: bool | None = None


class TokenOut(ApiOut):
    accessToken: str
    user: SessionUserOut


class SessionOut(ApiOut):
    user: SessionUserOut


class ProfileUpdateOut(ApiOut):
    message: str
    user: dict


class SettingsProfileOut(ApiOut):
    id: str
    firstName: str | None = None
    lastName: str | None = None
    email: str | None = None
    phone: str | None = None
    state: str | None = None
    classLevel: str | None = None
    track: str | None = None
    image: str | None = None
    tier: str
    tierExpiresAt: str | None = None
    hasPassword: bool
    devices: list[dict]
    notificationPreferences: PreferencesOut


class CompleteProfileOut(ApiOut):
    message: str


class RevokedOut(ApiOut):
    revoked: int


class PreferencesOut(ApiOut):
    studyReminders: bool
    streakReminders: bool
    announcements: bool


class SubjectCountOut(ApiOut):
    topics: int = 0
    questions: int = 0


class SubjectOut(ApiOut):
    id: str
    name: str
    slug: str
    code: str | None = None
    isWaec: bool | None = None
    isJamb: bool | None = None
    isNeco: bool | None = None
    trackCategory: str | None = None
    count: SubjectCountOut | None = Field(default=None, alias="_count")


class SubjectsOut(ApiOut):
    subjects: list[SubjectOut]


class TopicSummaryOut(ApiOut):
    subjectId: str
    subjectName: str
    topicId: str
    topicTitle: str
    questionCount: int


class QuestionOut(ApiOut):
    id: str
    subjectId: str | None = None
    topicId: str | None = None
    examType: str | None = None
    examYear: int | None = None
    questionNumber: int | None = None
    questionText: str
    questionType: str | None = None
    options: dict | list | None = None
    difficulty: str | None = None
    marks: int | None = None


class QuestionPageOut(ApiOut):
    questions: list[QuestionOut]
    pagination: dict


class QuizOut(ApiOut):
    assessmentId: str
    attemptId: str
    title: str
    source: str
    totalQuestions: int
    timeLimitMinutes: int | None = None
    questions: list[QuestionOut]
    resumed: bool | None = None
    deadlineAt: str | None = None


class AttemptResultOut(ApiOut):
    attemptId: str
    assessmentTitle: str | None = None
    assessmentType: str | None = None
    examYear: int | None = None
    score: float | None = None
    totalMarks: float | None = None
    percentage: float | None = None
    grade: str | None = None
    gradeRemark: str | None = None
    isCredit: bool | None = None
    totalQuestions: int | None = None
    correctCount: int | None = None
    results: list[dict] = []
    topicBreakdown: list[dict] = []


class BoardOut(ApiOut):
    board: str
    ready: bool
    qualifying: int
    started: int
    required: int
    reason: str | None = None


class BoardsOut(ApiOut):
    boards: dict[str, BoardOut]


class MockOptionsOut(ApiOut):
    examType: str
    subjects: list[dict]


class JambSpecOut(ApiOut):
    englishQuestions: int
    subjectQuestions: int
    totalQuestions: int
    durationMinutes: int
    totalMarks: int


class JambOptionsOut(ApiOut):
    spec: JambSpecOut
    english: dict | None = None
    englishYears: list[int]
    subjects: list[dict]


class JambPrepareOut(ApiOut):
    outcome: str
    examYear: int
    ready: bool
    message: str


class DeckRefOut(ApiOut):
    id: str
    title: str
    source: str | None = None


class DecksOut(ApiOut):
    decks: list[dict]


class DeckCreatedOut(ApiOut):
    deck: DeckRefOut


class FlashcardStatsOut(ApiOut):
    stats: dict


class RecommendationsOut(ApiOut):
    recommendations: list[dict]


class PreviewOut(ApiOut):
    lessonId: str
    title: str
    cards: list[dict]
    cardCount: int


class GeneratedDeckOut(ApiOut):
    deck: DeckRefOut
    counts: dict
    cardCount: int


class ReviewOut(ApiOut):
    outcome: str
    review: dict
    topicId: str | None = None


class EnrollOut(ApiOut):
    deckId: str
    enrolled: bool


class DeletedDeckOut(ApiOut):
    deckId: str


class StudyQueueOut(ApiOut):
    deck: DeckRefOut
    queue: list[dict]
    dueCount: int
    newCount: int


class PlanPageOut(ApiOut):
    today: str
    classLevel: str | None = None
    termLabel: str | None = None
    termSource: str | None = None
    daysToExam: int | None = None
    defaults: dict | None = None
    subjects: list[dict] = []
    plan: dict | None = None


class PlanCreatedOut(ApiOut):
    planId: str


class ItemStatusOut(ApiOut):
    status: str


class ProgressOut(ApiOut):
    progress: dict


class LibraryOut(ApiOut):
    resources: list[dict]


class AchievementsOut(ApiOut):
    achievements: list[dict]
    earned: int


class AwardOut(ApiOut):
    checked: bool
    newlyEarned: list[str]
    count: int


class AvatarOut(ApiOut):
    message: str
    image: str


class PapersOut(ApiOut):
    papers: list[dict]


class PretestOut(ApiOut):
    passed: bool | None = None
    alreadyPassed: bool | None = None
    percentage: float | None = None
    correctCount: int | None = None
    totalQuestions: int | None = None
    threshold: int | None = None
    assessmentId: str | None = None
    attemptId: str | None = None
    title: str | None = None
    questions: list[QuestionOut] | None = None


class AnnouncementsOut(ApiOut):
    announcements: list[dict]


class DashboardOut(ApiOut):
    firstName: str | None = None
    streak: int
    tier: str
    keepLearning: dict | None = None
    gaps: list[dict] = []
    todayItems: list[dict] = []
    recentAttempts: list[dict] = []
    achievements: dict


class PerformanceOut(ApiOut):
    attempts: list[dict]
    subjects: list[dict]
    advanced: bool
    subject: dict | None = None


class ClassroomSubjectsOut(ApiOut):
    subjects: list[dict]


class SubjectPageOut(ApiOut):
    subject: dict
    topics: list[dict]


class TopicPageOut(ApiOut):
    subject: dict
    topic: dict
    colour: str
    mastery: float | int
    available: bool
    alreadyPassed: bool
    questionCount: int
    canonicalLessonId: str | None = None
    createsAttempt: bool | None = None
    lesson: dict | None = None


class PracticeResultOut(ApiOut):
    result: dict | None = None


class CheckoutOut(ApiOut):
    authorizationUrl: str


class WebhookOut(ApiOut):
    received: bool
    duplicate: bool | None = None
    outcome: str | None = None


class AdminRowOut(ApiOut):
    id: str
    email: str | None = None
    username: str | None = None
    isOwner: bool
    isActive: bool
    lastLoginAt: str | None = None


class AdminTokenOut(ApiOut):
    accessToken: str
    admin: AdminRowOut


class AdminCreatedOut(ApiOut):
    admin: AdminRowOut


class AdminSessionOut(ApiOut):
    admin: dict


class AdminsOut(ApiOut):
    admins: list[AdminRowOut]


class StudentsPageOut(ApiOut):
    students: list[dict]
    pagination: dict | None = None


class StudentDetailOut(ApiOut):
    id: str
    email: str | None = None
    phone: str | None = None
    firstName: str | None = None
    lastName: str | None = None
    classLevel: str | None = None
    track: str | None = None
    tier: str | None = None


class AdminQuestionsOut(ApiOut):
    questions: list[dict]
    pagination: dict


class UsageOut(ApiOut):
    responseCount: int
    assessmentCount: int
    deletable: bool


class DeleteQuestionsOut(ApiOut):
    deleted: list[str]
    refused: list
    notFound: list[str]


class ImportQuestionsOut(ApiOut):
    message: str
    imported: int
    skipped: int
    errors: list


class LessonTopicOut(ApiOut):
    topicTitle: str
    lesson: dict | None = None


class LessonImportOut(ApiOut):
    message: str
    lessonId: str
    blockCount: int
    warnings: list


class LessonTreeOut(ApiOut):
    subjects: list[dict]


class MaterialOut(ApiOut):
    id: str
    subjectId: str
    title: str
    description: str | None = None
    resourceType: str
    url: str | None = None


class SignUploadOut(ApiOut):
    cloudName: str | None = None
    apiKey: str | None = None
    timestamp: int
    signature: str
    folder: str
    allowedFormats: str | list | None = None


class TermOut(ApiOut):
    id: str
    session: str
    term: str
    startsOn: str
    endsOn: str


class AnnouncementOut(ApiOut):
    id: str
    title: str
    body: str
    url: str | None = None
    audience: dict | None = None
    status: str | None = None


class AnnouncementCreatedOut(ApiOut):
    id: str
    recipientCount: int


class AudiencePreviewOut(ApiOut):
    students: int
    subscribedStudents: int
    devices: int


class AnnouncementTestOut(ApiOut):
    devices: int
    sent: int
    student: str | None = None


class AuditOut(ApiOut):
    entries: list[dict]
    pagination: dict | None = None


class StatsOut(ApiOut):
    students: int
    questions: int
    attempts: int


class BackfillOut(ApiOut):
    ledger: dict
    wasReset: bool
    blockCleared: bool


class CronDrainOut(ApiOut):
    claimed: int
    sent: int
    failed: int
    gone: int
    retrying: int


class CronReminderOut(ApiOut):
    processed: int
    notified: int
    sent: int
    skipped: int
    done: bool
