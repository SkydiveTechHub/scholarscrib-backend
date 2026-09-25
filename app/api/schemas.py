from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.domain import PHONE_PATTERN, ClassLevel, NigerianState, Term, Track

type ExamType = Literal["WAEC", "JAMB", "NECO", "CUSTOM"]
type Board = Literal["WAEC", "JAMB", "NECO"]
type Difficulty = Literal["BASIC", "INTERMEDIATE", "ADVANCED"]
type QuestionType = Literal["OBJECTIVE", "THEORY", "FILL_IN_BLANK"]
type Tier = Literal["FREEMIUM", "STANDARD", "PREMIUM"]
type BillingPeriod = Literal["MONTHLY", "YEARLY"]
type ReviewRating = Literal["AGAIN", "HARD", "GOOD", "EASY"]
type PlanItemStatus = Literal["COMPLETED", "SKIPPED", "PENDING"]
type ProgressStatus = Literal["NOT_STARTED", "IN_PROGRESS", "COMPLETED"]
type MaterialType = Literal["PDF", "IMAGE", "VIDEO", "LINK"]
type Weekday = Literal[1, 2, 3, 4, 5, 6, 7]
type Phone = Annotated[str, Field(pattern=PHONE_PATTERN)]


class RegisterIn(BaseModel):
    role: Literal["STUDENT"] = "STUDENT"
    firstName: str = Field(min_length=2)
    lastName: str = Field(min_length=2)
    email: EmailStr
    password: str = Field(min_length=6)
    classLevel: ClassLevel
    track: Track
    state: NigerianState


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)
    deviceLabel: str | None = None


class ProfilePatch(BaseModel):
    firstName: str | None = Field(default=None, min_length=2)
    lastName: str | None = Field(default=None, min_length=2)
    phone: Phone | Literal[""] | None = None
    state: NigerianState | Literal[""] | None = None
    classLevel: ClassLevel | None = None
    track: Track | None = None


class CompleteProfileIn(BaseModel):
    classLevel: ClassLevel
    track: Track
    state: NigerianState


class PasswordIn(BaseModel):
    currentPassword: str = Field(min_length=1)
    newPassword: str = Field(min_length=6)


class DeviceIn(BaseModel):
    deviceId: str | None = None
    allOthers: bool | None = None

    @model_validator(mode="after")
    def one_of(self):
        if bool(self.deviceId) == bool(self.allOthers):
            raise ValueError("Send deviceId or allOthers")
        return self


class PreferencePatch(BaseModel):
    studyReminders: bool | None = None
    streakReminders: bool | None = None
    announcements: bool | None = None

    @model_validator(mode="after")
    def one_field(self):
        if (
            self.studyReminders is None
            and self.streakReminders is None
            and self.announcements is None
        ):
            raise ValueError("Send at least one preference")
        return self


class GenerateQuizIn(BaseModel):
    subjectId: str | None = None
    subjectSlug: str | None = None
    topicIds: list[str] | None = None
    topicSlug: str | None = None
    difficulty: Difficulty | None = None
    examType: ExamType | None = None
    examYear: int | None = Field(default=None, ge=2001, le=2100)
    title: str | None = None
    count: int = Field(default=10, ge=5, le=60)
    untimed: bool = False

    @model_validator(mode="after")
    def subject_present(self):
        if not self.subjectId and not self.subjectSlug:
            raise ValueError("subjectId or subjectSlug is required")
        return self


class AnswerIn(BaseModel):
    questionId: str
    selectedAnswer: str | None = None
    timeSpentSeconds: int = Field(default=0, ge=0, le=86400)
    flaggedForReview: bool | None = None


class PracticeExitIn(BaseModel):
    subjectSlug: str | None = None
    topicSlug: str | None = None


class SubmitIn(BaseModel):
    attemptId: str
    answers: list[AnswerIn] = Field(default_factory=list, max_length=200)
    awayEvents: int | None = Field(default=None, ge=0, le=10000)
    practiceExit: PracticeExitIn | None = None

    @model_validator(mode="after")
    def unique_questions(self):
        ids = [answer.questionId for answer in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("Each question can be answered once")
        return self


class ScopePoint(BaseModel):
    classLevel: ClassLevel
    term: Term


class ScopedMockIn(BaseModel):
    examType: Board
    subjectId: str
    from_: ScopePoint = Field(alias="from")
    to: ScopePoint
    count: int = Field(default=40, ge=5, le=80)

    model_config = {"populate_by_name": True}


class JambIn(BaseModel):
    subjectIds: list[str] = Field(min_length=3, max_length=3)
    examYear: int = Field(ge=1978, le=2100)


class StudyPlanIn(BaseModel):
    subjectIds: list[str] = Field(min_length=1, max_length=20)
    studyDays: list[Weekday] = Field(min_length=1, max_length=7)
    weekdayMinutes: int = Field(ge=0, le=480)
    weekendMinutes: int = Field(ge=0, le=600)
    targetExam: ExamType | None = None
    targetDate: date | None = None
    forceExamMode: bool = False

    @model_validator(mode="after")
    def exam_pair(self):
        if bool(self.targetExam) != bool(self.targetDate):
            raise ValueError("An exam target needs both an exam and a date")
        return self


class CheckoutIn(BaseModel):
    tier: Tier
    period: BillingPeriod


class PushKeysIn(BaseModel):
    p256dh: str | None = None
    auth: str | None = None


class PushIn(BaseModel):
    endpoint: str
    keys: PushKeysIn


class DeckIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    subjectId: str | None = None


class LessonRef(BaseModel):
    lessonId: str


class EnrollIn(BaseModel):
    enrolled: bool


class ItemStatusIn(BaseModel):
    status: PlanItemStatus


class PositionIn(BaseModel):
    subjectId: str
    topicId: str | None = None


class PositionsIn(BaseModel):
    positions: list[PositionIn] = Field(max_length=20)


class ProgressIn(BaseModel):
    status: ProgressStatus | None = None
    completionPercent: int | None = Field(default=None, ge=0, le=100)
    checkpointData: dict | None = None
    masteryScore: int | None = None
    timeSpentMinutes: int | None = None


class EndpointIn(BaseModel):
    endpoint: str


class AdminCreateIn(BaseModel):
    identifier: str = Field(min_length=3)
    password: str = Field(min_length=12)


class ActiveIn(BaseModel):
    isActive: bool


class ReviewIn(BaseModel):
    flashcardId: str
    rating: ReviewRating
    responseTimeMs: int | None = None
    objectiveCorrect: bool | None = None


class PretestAnswerIn(BaseModel):
    questionId: str
    selectedAnswer: str | None = None
    timeSpentSeconds: int | None = None


class PretestIn(BaseModel):
    attemptId: str | None = None
    answers: list[PretestAnswerIn] | None = None


class StudentProfileIn(BaseModel):
    firstName: str
    lastName: str
    email: str | None = None
    phone: str | None = None
    classLevel: ClassLevel | None = None
    track: Track | None = None
    state: NigerianState | None = None


class StudentStatusIn(BaseModel):
    isActive: bool
    reason: str | None = None


class StudentTierIn(BaseModel):
    tier: Tier
    period: BillingPeriod = "MONTHLY"
    note: str | None = None


class QuestionCreateIn(BaseModel):
    subjectId: str
    topicId: str | None = None
    examType: ExamType
    examYear: int | None = None
    questionNumber: int | None = None
    questionText: str
    questionImageUrl: str | None = None
    questionType: QuestionType = "OBJECTIVE"
    options: dict | None = None
    correctAnswer: str
    explanation: str
    explanationImageUrl: str | None = None
    difficulty: Difficulty = "INTERMEDIATE"
    marks: int = 1
    timeEstimateSeconds: int = 90


class QuestionPatchIn(BaseModel):
    subjectId: str | None = None
    topicId: str | None = None
    examType: ExamType | None = None
    examYear: int | None = None
    questionNumber: int | None = None
    questionText: str | None = None
    questionImageUrl: str | None = None
    questionType: QuestionType | None = None
    options: dict | None = None
    correctAnswer: str | None = None
    explanation: str | None = None
    explanationImageUrl: str | None = None
    difficulty: Difficulty | None = None
    marks: int | None = None
    timeEstimateSeconds: int | None = None


class ImportQuestionIn(BaseModel):
    subjectCode: str | None = None
    topicSlug: str | None = None
    examType: str | None = None
    examYear: int | None = None
    questionNumber: int | None = None
    questionText: str | None = None
    questionType: str | None = None
    options: dict | None = None
    correctAnswer: str | None = None
    explanation: str | None = None
    difficulty: str | None = None
    marks: int | None = None
    timeEstimateSeconds: int | None = None


class ImportQuestionsIn(BaseModel):
    questions: list[ImportQuestionIn] | None = None
    skipDuplicates: bool = True


class DeleteQuestionsIn(BaseModel):
    ids: list[str] = Field(default_factory=list)


class SignMaterialIn(BaseModel):
    type: Literal["PDF", "IMAGE"]


class MaterialCreateIn(BaseModel):
    subjectId: str
    title: str
    description: str | None = None
    resourceType: MaterialType
    url: str
    author: str | None = None
    isFree: bool = True


class MaterialPatchIn(BaseModel):
    title: str | None = None
    description: str | None = None
    resourceType: MaterialType | None = None
    url: str | None = None
    author: str | None = None
    isFree: bool | None = None


class LessonImportIn(BaseModel):
    topicId: str | None = None
    markdown: str | None = None
    confirm: bool | None = None


class AudienceIn(BaseModel):
    model_config = {"extra": "allow"}

    examTargets: list[ExamType] | None = None
    classLevels: list[ClassLevel] | None = None
    tracks: list[Track] | None = None
    tiers: list[Tier] | None = None
    userIds: list[str] | None = None


class AnnouncementCreateIn(BaseModel):
    title: str | None = None
    body: str | None = None
    url: str | None = None
    audience: AudienceIn | None = None
    expiresInDays: int = 7


class AnnouncementPreviewIn(BaseModel):
    audience: AudienceIn | None = None


class AnnouncementTestIn(BaseModel):
    title: str | None = None
    body: str | None = None
    url: str | None = None
    contact: str | None = None


class AcademicTermIn(BaseModel):
    session: str
    term: Term
    startsOn: str
    endsOn: str


class ProviderBackfillIn(BaseModel):
    subjectSlug: str
    examType: Board
    examYear: int
    reset: bool = False
    clearBlock: bool = False
