import hashlib
import time
from urllib.parse import urlparse

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    QuestionCreateIn,
    SignMaterialIn,
    StudentProfileIn,
    StudentStatusIn,
    StudentTierIn,
)
from app.core.config import settings
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.logger import logger
from app.core.security import hash_password
from app.core.timeutil import utcnow
from app.database.models import (
    AcademicTerm,
    Admin,
    AdminAudit,
    AssessmentAttempt,
    FlashcardReview,
    PushSubscription,
    Question,
)
from app.database.repositories.admin import admins_repository, audits_repository
from app.database.repositories.assessment import attempts_repository
from app.database.repositories.curriculum import subjects_repository, topics_repository
from app.database.repositories.flashcard import reviews_repository
from app.database.repositories.identity import (
    devices_repository,
    schools_repository,
    users_repository,
)
from app.database.repositories.learning import learning_events_repository
from app.database.repositories.notification import push_subscriptions_repository
from app.database.repositories.question import questions_repository
from app.services.auth import ADMIN_IDENTIFIER
from app.services.billing import (
    GrantCompSubscriptionService,
    RevokeSubscriptionService,
)
from app.services.catalogue import bust_catalogue
from app.services.learning.mastery_store import GetTopicMasteryService
from app.services.planner.term_context import TermRange, validate_term_ranges
from app.services.provider.rules import objective_ok


class RecordAuditService:
    def __init__(
        self,
        session: AsyncSession,
        actor_id: str,
        action: str,
        entity: str,
        summary: str,
        entity_id: str | None = None,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.action = action
        self.entity = entity
        self.summary = summary
        self.entity_id = entity_id

    async def process(self) -> None:
        try:
            async with self.session.begin_nested():
                await audits_repository.add(
                    self.session,
                    AdminAudit(
                        id=cuid(),
                        actor_id=self.actor_id,
                        action=self.action,
                        entity=self.entity,
                        entity_id=self.entity_id,
                        summary=self.summary,
                    ),
                    flush=True,
                )
        except Exception:
            logger.exception("audit write failed")


class CreateAdminService:
    def __init__(
        self,
        session: AsyncSession,
        actor_id: str,
        identifier: str,
        password: str,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.identifier = identifier
        self.password = password

    async def process(self) -> dict:
        email, username = self._parse_identifier()
        self._validate_password()
        admin = await self._create(email, username)
        await RecordAuditService(
            self.session,
            self.actor_id,
            "admin.create",
            "Admin",
            f"Created admin {email or username}",
            admin.id,
        ).process()
        return {"admin": admin_row(admin)}

    def _parse_identifier(self) -> tuple[str | None, str | None]:
        ident = self.identifier.strip().lower()
        email = ident if "@" in ident else None
        username = None if email else ident
        if email is None and not ADMIN_IDENTIFIER.match(ident):
            raise ApiError(
                400,
                "Username must be 3–32 characters: letters, numbers, dots, "
                "underscores, or hyphens",
            )
        return email, username

    def _validate_password(self) -> None:
        if len(self.password) < 12:
            raise ApiError(400, "Password must be at least 12 characters")

    async def _create(self, email: str | None, username: str | None) -> Admin:
        admin = Admin(
            id=cuid(),
            email=email,
            username=username,
            password_hash=hash_password(self.password),
            is_owner=False,
            created_by_id=self.actor_id,
        )
        try:
            await admins_repository.add(self.session, admin, flush=True)
        except IntegrityError as exc:
            raise ApiError(409, "That email or username is already taken") from exc
        return admin

    async def _create_superuser(self, email: str | None, username: str | None) -> Admin:
        admin = Admin(
            id=cuid(),
            email=email,
            username=username,
            password_hash=hash_password(self.password),
            is_owner=True,
        )
        try:
            await admins_repository.add(self.session, admin, flush=False)
        except IntegrityError as exc:
            raise ApiError(409, "That email or username is already taken") from exc
        return admin


class SetAdminStatusService:
    def __init__(
        self,
        session: AsyncSession,
        actor_id: str,
        admin_id: str,
        is_active: bool,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.admin_id = admin_id
        self.is_active = is_active

    async def process(self) -> dict:
        admin = await self._load()
        admin.is_active = self.is_active
        await RecordAuditService(
            self.session,
            self.actor_id,
            "admin.reactivate" if self.is_active else "admin.deactivate",
            "Admin",
            f"{'Reactivated' if self.is_active else 'Deactivated'} admin",
            admin.id,
        ).process()
        return {"ok": True}

    async def _load(self) -> Admin:
        admin = await admins_repository.by_id(self.session, self.admin_id)
        if admin is None:
            raise ApiError(404, "Admin not found")
        if admin.is_owner and not self.is_active:
            raise ApiError(403, "The owner account cannot be deactivated")
        return admin


class UpdateStudentService:
    def __init__(
        self,
        session: AsyncSession,
        actor_id: str,
        user_id: str,
        body: StudentProfileIn,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.user_id = user_id
        self.body = body

    async def process(self) -> dict:
        user = await self._load()
        changed = self._apply(user)
        await self._flush()
        await RecordAuditService(
            self.session,
            self.actor_id,
            "student.update",
            "User",
            "Updated " + ", ".join(changed or ["profile"]),
            user.id,
        ).process()
        return {"ok": True}

    async def _load(self):
        user = await users_repository.by_id(self.session, self.user_id)
        if user is None:
            raise ApiError(404, "Student not found")
        return user

    def _apply(self, user) -> list[str]:
        changed = []
        mapping = {
            "firstName": "first_name",
            "lastName": "last_name",
            "email": "email",
            "phone": "phone",
            "classLevel": "class_level",
            "track": "track",
            "state": "state",
        }
        fields = self.body.model_fields_set
        for key, attr in mapping.items():
            if key not in fields:
                continue
            value = getattr(self.body, key)
            if getattr(user, attr) == value:
                continue
            if key in {"phone", "email"} and value == "":
                value = None
            setattr(user, attr, value)
            changed.append(key)
        return changed

    async def _flush(self) -> None:
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ApiError(409, "That email or phone is already in use") from exc


class DeleteStudentService:
    def __init__(self, session: AsyncSession, actor_id: str, user_id: str) -> None:
        self.session = session
        self.actor_id = actor_id
        self.user_id = user_id

    async def process(self) -> dict:
        user = await users_repository.by_id(self.session, self.user_id)
        if user is None:
            raise ApiError(404, "Student not found")
        await RecordAuditService(
            self.session,
            self.actor_id,
            "student.delete",
            "User",
            f"Deleted student {user.email}",
            user.id,
        ).process()
        await users_repository.remove(self.session, user)
        return {"ok": True}


class SetStudentStatusService:
    def __init__(
        self,
        session: AsyncSession,
        actor_id: str,
        user_id: str,
        body: StudentStatusIn,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.user_id = user_id
        self.body = body

    async def process(self) -> dict:
        user = await self._load()
        self._apply(user)
        await RecordAuditService(
            self.session,
            self.actor_id,
            "student.reactivate" if self.body.isActive else "student.suspend",
            "User",
            "Reactivated student"
            if self.body.isActive
            else f"Suspended student: {self.body.reason}",
            user.id,
        ).process()
        return {"ok": True}

    async def _load(self):
        user = await users_repository.by_id(self.session, self.user_id)
        if user is None:
            raise ApiError(404, "Student not found")
        if not self.body.isActive and (
            self.body.reason is None or not 3 <= len(self.body.reason) <= 500
        ):
            raise ApiError(400, "A suspension reason of 3–500 characters is required")
        return user

    def _apply(self, user) -> None:
        user.is_active = self.body.isActive
        user.suspended_at = None if self.body.isActive else utcnow()
        user.suspended_reason = None if self.body.isActive else self.body.reason


class SetStudentTierService:
    def __init__(
        self,
        session: AsyncSession,
        actor_id: str,
        user_id: str,
        body: StudentTierIn,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.user_id = user_id
        self.body = body

    async def process(self) -> dict:
        user = await users_repository.by_id(self.session, self.user_id)
        if user is None:
            raise ApiError(404, "Student not found")
        await self._apply_tier()
        await RecordAuditService(
            self.session,
            self.actor_id,
            "student.tier",
            "User",
            f"Set tier to {self.body.tier}",
            user.id,
        ).process()
        return {"ok": True}

    async def _apply_tier(self) -> None:
        if self.body.tier == "FREEMIUM":
            await RevokeSubscriptionService(self.session, self.user_id).process()
        else:
            await GrantCompSubscriptionService(
                self.session,
                self.user_id,
                self.body.tier,
                self.body.period,
                self.actor_id,
                self.body.note,
            ).process()


class ForceSignOutService:
    def __init__(self, session: AsyncSession, actor_id: str, user_id: str) -> None:
        self.session = session
        self.actor_id = actor_id
        self.user_id = user_id

    async def process(self) -> dict:
        user = await users_repository.by_id(self.session, self.user_id)
        if user is None:
            raise ApiError(404, "Student not found")
        user.sessions_valid_from = utcnow()
        await push_subscriptions_repository.delete_where(
            self.session, PushSubscription.user_id == self.user_id
        )
        await RecordAuditService(
            self.session,
            self.actor_id,
            "student.force_signout",
            "User",
            "Forced sign-out",
            user.id,
        ).process()
        return {"ok": True}


class ListAdminQuestionsService:
    def __init__(
        self,
        session: AsyncSession,
        page: int,
        page_size: int,
        filters: dict,
    ) -> None:
        self.session = session
        self.page = page
        self.page_size = page_size
        self.filters = filters

    async def process(self) -> dict:
        rows, total = await questions_repository.list_filtered(
            self.session, self.page, self.page_size, self.filters
        )
        return {
            "questions": [question_row(row) for row in rows],
            "pagination": {
                "page": self.page,
                "pageSize": self.page_size,
                "total": total,
                "totalPages": max(1, math_ceil(total / self.page_size)),
            },
        }


class CreateQuestionService:
    def __init__(
        self, session: AsyncSession, actor_id: str, body: QuestionCreateIn
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.body = body

    async def process(self) -> dict:
        await self._validate()
        question = await self._create()
        await RecordAuditService(
            self.session,
            self.actor_id,
            "question.create",
            "Question",
            "Created question",
            question.id,
        ).process()
        bust_catalogue()
        return {"id": question.id}

    async def _validate(self) -> None:
        subject = await subjects_repository.by_id(self.session, self.body.subjectId)
        if subject is None:
            raise ApiError(400, "Unknown subject")
        if self.body.topicId:
            topic = await topics_repository.by_id(self.session, self.body.topicId)
            if topic is None or topic.subject_id != subject.id:
                raise ApiError(400, "Topic does not belong to that subject")
        validate_question(self.body)

    async def _create(self) -> Question:
        question = Question(
            id=cuid(),
            subject_id=self.body.subjectId,
            topic_id=self.body.topicId,
            exam_type=self.body.examType,
            exam_year=self.body.examYear,
            question_number=self.body.questionNumber,
            question_text=self.body.questionText,
            question_image_url=self.body.questionImageUrl,
            question_type=self.body.questionType or "OBJECTIVE",
            options=self.body.options,
            correct_answer=str(self.body.correctAnswer).upper(),
            explanation=self.body.explanation,
            explanation_image_url=self.body.explanationImageUrl,
            difficulty=self.body.difficulty or "INTERMEDIATE",
            marks=self.body.marks or 1,
            time_estimate_seconds=self.body.timeEstimateSeconds or 90,
        )
        await questions_repository.add(self.session, question, flush=True)
        return question


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()


class SearchStudentsService:
    def __init__(
        self,
        session: AsyncSession,
        query: str | None,
        page: int,
        page_size: int,
        *,
        class_level: str | None = None,
        track: str | None = None,
        tier: str | None = None,
        status: str | None = None,
        state: str | None = None,
    ) -> None:
        self.session = session
        self.query = query
        self.page = page
        self.page_size = page_size
        self.class_level = class_level
        self.track = track
        self.tier = tier
        self.status = status
        self.state = state

    async def process(self) -> dict:
        rows, total = await users_repository.search_students(
            self.session,
            self.query,
            self.page,
            self.page_size,
            class_level=self.class_level,
            track=self.track,
            tier=self.tier,
            status=self.status,
            state=self.state,
        )
        last_active = await learning_events_repository.last_active(
            self.session, [user.id for user in rows]
        )
        return {
            "students": [
                {
                    "id": user.id,
                    "email": user.email,
                    "phone": user.phone,
                    "firstName": user.first_name,
                    "lastName": user.last_name,
                    "classLevel": user.class_level,
                    "track": user.track,
                    "tier": user.tier,
                    "isActive": user.is_active,
                    "state": user.state,
                    "createdAt": _iso(user.created_at),
                    "lastActiveAt": _iso(last_active.get(user.id)),
                }
                for user in rows
            ],
            "pagination": {
                "page": self.page,
                "pageSize": self.page_size,
                "total": total,
            },
        }


class GetStudentDetailService:
    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def process(self) -> dict:
        user = await users_repository.by_id(self.session, self.user_id)
        if user is None:
            raise ApiError(404, "Student not found")
        device_rows = await devices_repository.for_user(self.session, self.user_id)
        school = (
            await schools_repository.by_id(self.session, user.school_id)
            if user.school_id
            else None
        )
        last_active = await learning_events_repository.last_active(
            self.session, [user.id]
        )
        states = await GetTopicMasteryService(self.session, user.id).process()
        return {
            "id": user.id,
            "email": user.email,
            "phone": user.phone,
            "firstName": user.first_name,
            "lastName": user.last_name,
            "classLevel": user.class_level,
            "track": user.track,
            "state": user.state,
            "schoolId": user.school_id,
            "schoolName": school.name if school else None,
            "tier": user.tier,
            "tierUpdatedAt": _iso(user.tier_updated_at),
            "isActive": user.is_active,
            "suspendedAt": _iso(user.suspended_at),
            "suspendedReason": user.suspended_reason,
            "createdAt": _iso(user.created_at),
            "lastActiveAt": _iso(last_active.get(user.id)),
            "attemptCount": await attempts_repository.count(
                self.session, AssessmentAttempt.student_id == user.id
            ),
            "masteredTopicCount": sum(
                1 for state in states.values() if state.level == "STRONG"
            ),
            "flashcardReviewCount": await reviews_repository.count(
                self.session, FlashcardReview.student_id == user.id
            ),
            "devices": [
                {
                    "id": device.id,
                    "label": device.label,
                    "revokedAt": device.revoked_at,
                }
                for device in device_rows
            ],
        }


class SignMaterialUploadService:
    def __init__(self, body: SignMaterialIn) -> None:
        self.material_type = body.type or ""

    async def process(self) -> dict:
        if self.material_type not in {"PDF", "IMAGE"}:
            raise ApiError(400, "Only PDF and image uploads are signed")
        if not settings.cloudinary_enabled:
            raise ApiError(503, "Cloudinary is not configured")
        folder = (
            "prepwell/materials/pdf"
            if self.material_type == "PDF"
            else "prepwell/materials/image"
        )
        formats = "pdf" if self.material_type == "PDF" else "jpg,jpeg,png,webp"
        timestamp = int(time.time())
        payload = (
            f"allowed_formats={formats}&folder={folder}"
            f"&timestamp={timestamp}{settings.cloudinary_api_secret}"
        )
        signature = hashlib.sha1(payload.encode()).hexdigest()
        return {
            "cloudName": settings.cloudinary_cloud_name,
            "apiKey": settings.cloudinary_api_key,
            "timestamp": timestamp,
            "signature": signature,
            "folder": folder,
            "allowedFormats": formats,
            "maxBytes": 50_000_000 if self.material_type == "PDF" else 5_000_000,
            "resourceType": "raw" if self.material_type == "PDF" else "image",
        }


def admin_row(admin: Admin) -> dict:
    return {
        "id": admin.id,
        "email": admin.email,
        "username": admin.username,
        "isOwner": admin.is_owner,
        "isActive": admin.is_active,
        "lastLoginAt": admin.last_login_at.isoformat() if admin.last_login_at else None,
        "createdAt": admin.created_at.isoformat() if admin.created_at else None,
    }


def question_row(question: Question) -> dict:
    return {
        "id": question.id,
        "subjectId": question.subject_id,
        "topicId": question.topic_id,
        "examType": question.exam_type,
        "examYear": question.exam_year,
        "questionText": question.question_text,
        "questionType": question.question_type,
        "options": question.options,
        "correctAnswer": question.correct_answer,
        "explanation": question.explanation,
        "difficulty": question.difficulty,
        "marks": question.marks,
    }


def validate_question(data: QuestionCreateIn) -> None:
    if (data.questionType or "OBJECTIVE") == "OBJECTIVE" and not objective_ok(
        data.options, data.correctAnswer
    ):
        raise ApiError(
            400,
            "Objective questions need at least 4 options and a matching answer",
        )


def math_ceil(value: float) -> int:
    import math

    return math.ceil(value)


def validate_resource_url(resource_type: str, url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ApiError(400, "URLs must use https")
    if resource_type == "VIDEO":
        host = (parsed.hostname or "").removeprefix("www.")
        if host not in {"youtube.com", "youtu.be", "vimeo.com"}:
            raise ApiError(400, "Videos must be hosted on YouTube or Vimeo")


def validate_terms(
    rows: list[AcademicTerm],
    candidate: TermRange,
    ignore_id: str | None = None,
) -> None:
    ranges = [
        TermRange(
            row.session,
            row.term,
            row.starts_on.isoformat()[:10],
            row.ends_on.isoformat()[:10],
        )
        for row in rows
        if row.id != ignore_id
    ]
    ranges.append(candidate)
    errors = validate_term_ranges(ranges)
    if errors:
        raise ApiError(400, " ".join(errors))
