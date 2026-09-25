"""Admin mutations and announcement reads."""

from datetime import date, timedelta
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AcademicTermIn,
    AnnouncementCreateIn,
    AnnouncementTestIn,
    AudienceIn,
    ImportQuestionIn,
    ImportQuestionsIn,
    LessonImportIn,
    MaterialCreateIn,
    MaterialPatchIn,
    QuestionPatchIn,
)
from app.core.config import settings
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.timeutil import utcnow
from app.database.models import (
    AcademicTerm,
    Announcement,
    AnnouncementDelivery,
    AnnouncementDismissal,
    Lesson,
    Question,
    SubjectResource,
    Subtopic,
    Topic,
    User,
)
from app.database.repositories.admin import audits_repository
from app.database.repositories.assessment import attempts_repository
from app.database.repositories.curriculum import (
    lessons_repository,
    subject_resources_repository,
    subjects_repository,
    subtopics_repository,
    topics_repository,
)
from app.database.repositories.identity import users_repository
from app.database.repositories.notification import (
    announcement_deliveries_repository,
    announcement_dismissals_repository,
    announcements_repository,
    push_subscriptions_repository,
)
from app.database.repositories.planner import (
    academic_terms_repository,
    plans_repository,
)
from app.database.repositories.question import questions_repository
from app.services.admin import (
    RecordAuditService,
    validate_resource_url,
    validate_terms,
)
from app.services.catalogue import bust_catalogue
from app.services.lessons.markdown import validate_lesson_markdown
from app.services.planner.term_context import TermRange
from app.services.provider.rules import objective_ok
from app.services.push import send_notification

_QUESTION_FIELDS = {
    "subjectId": "subject_id",
    "topicId": "topic_id",
    "examType": "exam_type",
    "examYear": "exam_year",
    "questionNumber": "question_number",
    "questionText": "question_text",
    "questionImageUrl": "question_image_url",
    "questionType": "question_type",
    "options": "options",
    "correctAnswer": "correct_answer",
    "explanation": "explanation",
    "explanationImageUrl": "explanation_image_url",
    "difficulty": "difficulty",
    "marks": "marks",
    "timeEstimateSeconds": "time_estimate_seconds",
}

_AUDIENCE_KEYS = {
    "examTargets": {"WAEC", "JAMB", "NECO", "CUSTOM"},
    "classLevels": {"SS1", "SS2", "SS3"},
    "tracks": {"SCIENCE", "ARTS", "COMMERCIAL"},
    "tiers": {"FREEMIUM", "STANDARD", "PREMIUM"},
}


class UpdateQuestionService:
    def __init__(
        self,
        session: AsyncSession,
        actor_id: str,
        question_id: str,
        body: QuestionPatchIn,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.question_id = question_id
        self.body = body

    async def process(self) -> dict:
        question = await self._load()
        present = self._apply(question)
        await self._validate(question)
        await self.session.flush()
        await RecordAuditService(
            self.session,
            self.actor_id,
            "question.update",
            "Question",
            ", ".join(present),
            question.id,
        ).process()
        bust_catalogue()
        return {"id": question.id}

    async def _load(self) -> Question:
        question = await questions_repository.by_id(self.session, self.question_id)
        if question is None:
            raise ApiError(404, "Question not found")
        return question

    def _apply(self, question: Question) -> list[str]:
        present = [key for key in _QUESTION_FIELDS if key in self.body.model_fields_set]
        if not present:
            raise ApiError(400, "At least one field is required")
        for key in present:
            value = getattr(self.body, key)
            if key == "correctAnswer" and value is not None:
                value = str(value).upper()
            setattr(question, _QUESTION_FIELDS[key], value)
        return present

    async def _validate(self, question: Question) -> None:
        if question.topic_id:
            topic = await topics_repository.by_id(self.session, question.topic_id)
            if topic is None or topic.subject_id != question.subject_id:
                raise ApiError(400, "Topic does not belong to that subject")
        subject = await subjects_repository.by_id(self.session, question.subject_id)
        if subject is None:
            raise ApiError(400, "Unknown subject")
        if question.question_type == "OBJECTIVE" and not objective_ok(
            question.options, question.correct_answer
        ):
            raise ApiError(
                400,
                "Objective questions need at least 4 options and a matching answer",
            )


class GetQuestionUsageService:
    def __init__(self, session: AsyncSession, question_id: str) -> None:
        self.session = session
        self.question_id = question_id

    async def process(self) -> dict:
        question = await questions_repository.by_id(self.session, self.question_id)
        if question is None:
            raise ApiError(404, "Question not found")
        responses, assessments = await questions_repository.usage_counts(
            self.session, self.question_id
        )
        return {
            "responseCount": responses,
            "assessmentCount": assessments,
            "deletable": responses == 0 and assessments == 0,
        }


class DeleteQuestionsService:
    def __init__(self, session: AsyncSession, actor_id: str, ids: list[str]) -> None:
        self.session = session
        self.actor_id = actor_id
        self.ids = ids

    async def process(self) -> dict:
        if not self.ids or len(self.ids) > 100:
            raise ApiError(400, "Provide between 1 and 100 question ids")
        deleted, refused, not_found = await self._delete_each()
        if deleted:
            await RecordAuditService(
                self.session,
                self.actor_id,
                "question.delete",
                "Question",
                f"Deleted {len(deleted)}",
                None,
            ).process()
            bust_catalogue()
        await self.session.flush()
        return {
            "deleted": deleted,
            "refused": refused,
            "notFound": not_found,
        }

    async def _delete_each(
        self,
    ) -> tuple[list[str], list[dict], list[str]]:
        deleted: list[str] = []
        refused = []
        not_found = []
        for question_id in self.ids:
            question = await questions_repository.by_id(self.session, question_id)
            if question is None:
                not_found.append(question_id)
                continue
            responses, assessments = await questions_repository.usage_counts(
                self.session, question_id
            )
            if responses or assessments:
                refused.append(
                    {
                        "id": question_id,
                        "responseCount": responses,
                        "assessmentCount": assessments,
                    }
                )
                continue
            await questions_repository.remove(self.session, question)
            deleted.append(question_id)
        return deleted, refused, not_found


class ImportQuestionsService:
    def __init__(
        self, session: AsyncSession, actor_id: str, body: ImportQuestionsIn
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.body = body

    async def process(self) -> dict:
        rows = self.body.questions
        if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
            raise ApiError(400, "questions must contain 1 to 500 rows")
        skip = self.body.skipDuplicates
        imported, skipped, errors = await self._import_all(rows, skip)
        if imported:
            await RecordAuditService(
                self.session,
                self.actor_id,
                "question.import",
                "Question",
                f"Imported {imported}",
                None,
            ).process()
            bust_catalogue()
        return {
            "message": (
                f"Imported {imported}, skipped {skipped}, {len(errors)} errors"
            ),
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
        }

    async def _import_all(
        self, rows: list[ImportQuestionIn], skip: bool
    ) -> tuple[int, int, list[dict]]:
        imported = 0
        skipped = 0
        errors = []
        for index, row in enumerate(rows):
            try:
                if await self._import_row(row, skip):
                    imported += 1
                else:
                    skipped += 1
            except ApiError as exc:
                errors.append({"index": index, "reason": exc.payload["error"]})
        return imported, skipped, errors

    async def _import_row(self, row: ImportQuestionIn, skip: bool) -> bool:
        code = row.subjectCode
        if not isinstance(code, str):
            raise ApiError(400, "Unknown subject code")
        subject = await subjects_repository.by_code(self.session, code)
        if subject is None:
            raise ApiError(400, "Unknown subject code")
        topic_id = None
        if row.topicSlug:
            topic = await topics_repository.by_slug(
                self.session, subject.id, row.topicSlug
            )
            if topic is None:
                raise ApiError(400, "Topic does not belong to that subject")
            topic_id = topic.id
        text = row.questionText or ""
        if len(text) < 5 or len(row.explanation or "") < 5:
            raise ApiError(
                400,
                "Question text and explanation must be at least 5 characters",
            )
        exam_type = row.examType
        if exam_type not in {"WAEC", "JAMB", "NECO", "CUSTOM"}:
            raise ApiError(400, "Unknown exam type")
        if (
            not objective_ok(row.options, row.correctAnswer)
            and (row.questionType or "OBJECTIVE") == "OBJECTIVE"
        ):
            raise ApiError(
                400,
                "Objective questions need at least 4 options and a matching answer",
            )
        if row.correctAnswer is None:
            raise ApiError(400, "correctAnswer is required")
        if skip:
            existing = await questions_repository.duplicate_id(
                self.session,
                subject_id=subject.id,
                exam_type=exam_type,
                exam_year=row.examYear,
                question_text=text,
            )
            if existing:
                return False
        await questions_repository.add(
            self.session,
            Question(
                id=cuid(),
                subject_id=subject.id,
                topic_id=topic_id,
                exam_type=exam_type,
                exam_year=row.examYear,
                question_number=row.questionNumber,
                question_text=text,
                question_type=row.questionType or "OBJECTIVE",
                options=row.options,
                correct_answer=str(row.correctAnswer).upper(),
                explanation=row.explanation,
                difficulty=row.difficulty or "INTERMEDIATE",
                marks=row.marks or 1,
                time_estimate_seconds=row.timeEstimateSeconds or 90,
            ),
            flush=True,
        )
        return True


class GetLessonForTopicService:
    def __init__(self, session: AsyncSession, topic_id: str) -> None:
        self.session = session
        self.topic_id = topic_id

    async def process(self) -> dict:
        topic = await topics_repository.by_id(self.session, self.topic_id)
        if topic is None:
            raise ApiError(404, "Unknown topic")
        lesson = await lessons_repository.latest_for_topic(self.session, self.topic_id)
        if lesson is None:
            return {"topicTitle": topic.title, "lesson": None}
        payload = {
            "title": lesson.title,
            "blockCount": len(lesson.blocks or []),
            "authored": lesson.created_by not in (None, "system"),
            "updatedAt": (lesson.updated_at.isoformat() if lesson.updated_at else None),
        }
        if lesson.created_by != "system":
            payload["markdown"] = lesson.content
        return {"topicTitle": topic.title, "lesson": payload}


class ImportLessonService:
    def __init__(
        self, session: AsyncSession, actor_id: str, body: LessonImportIn
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.body = body

    async def process(self) -> dict:
        markdown = self._validate_input()
        topic = await self._load_topic()
        parsed = self._parse(markdown)
        lesson = await self._save(topic, markdown, parsed)
        await RecordAuditService(
            self.session,
            self.actor_id,
            "lesson.import",
            "Lesson",
            lesson.title,
            lesson.id,
        ).process()
        bust_catalogue()
        return {
            "message": "Lesson saved",
            "lessonId": lesson.id,
            "blockCount": len(parsed.blocks),
            "warnings": parsed.warnings,
        }

    def _validate_input(self) -> str:
        if self.body.confirm is not True:
            raise ApiError(400, "confirm must be true")
        markdown = self.body.markdown or ""
        if not 1 <= len(markdown) <= 200_000:
            raise ApiError(400, "markdown must be 1 to 200000 characters")
        return markdown

    async def _load_topic(self) -> Topic:
        topic = await topics_repository.by_id(self.session, self.body.topicId)
        if topic is None:
            raise ApiError(404, "Unknown topic")
        return topic

    def _parse(self, markdown: str):
        parsed = validate_lesson_markdown(markdown)
        if parsed.errors:
            raise ApiError(
                400,
                "Lesson markdown is invalid",
                issues=[
                    {"line": issue.line, "message": issue.message}
                    for issue in parsed.errors
                ],
            )
        if not parsed.blocks:
            raise ApiError(400, "The note produced no lesson blocks")
        return parsed

    async def _save(self, topic: Topic, markdown: str, parsed) -> Lesson:
        subtopic = await subtopics_repository.by_topic_and_title(
            self.session, topic.id, "Core Concepts"
        )
        if subtopic is None:
            subtopic = Subtopic(
                id=cuid(),
                topic_id=topic.id,
                title="Core Concepts",
                order_index=0,
            )
            await subtopics_repository.add(self.session, subtopic, flush=True)
        lesson = await lessons_repository.latest_for_topic(self.session, topic.id)
        if lesson is None:
            lesson = Lesson(
                id=cuid(),
                subtopic_id=subtopic.id,
                title=parsed.title or topic.title,
            )
            await lessons_repository.add(self.session, lesson)
        lesson.title = parsed.title or lesson.title
        lesson.content = markdown
        lesson.blocks = parsed.blocks
        lesson.created_by = self.actor_id
        lesson.updated_at = utcnow()
        await self.session.flush()
        return lesson


class ListMaterialsService:
    def __init__(self, session: AsyncSession, subject_id: str | None) -> None:
        self.session = session
        self.subject_id = subject_id

    async def process(self) -> list[dict]:
        if not self.subject_id:
            raise ApiError(400, "subjectId is required")
        rows = await subject_resources_repository.for_subject(
            self.session, self.subject_id
        )
        return [_material(row) for row in rows]


class CreateMaterialService:
    def __init__(
        self, session: AsyncSession, actor_id: str, body: MaterialCreateIn
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.body = body

    async def process(self) -> dict:
        _check_material(self.body, partial=False)
        subject = await subjects_repository.by_id(self.session, self.body.subjectId)
        if subject is None:
            raise ApiError(400, "Unknown subject")
        validate_resource_url(self.body.resourceType, self.body.url)
        row = await self._create(subject.id)
        await RecordAuditService(
            self.session,
            self.actor_id,
            "material.create",
            "SubjectResource",
            row.title,
            row.id,
        ).process()
        return _material(row)

    async def _create(self, subject_id: str) -> SubjectResource:
        current = await subject_resources_repository.max_order_index(
            self.session, subject_id
        )
        row = SubjectResource(
            id=cuid(),
            subject_id=subject_id,
            title=self.body.title,
            description=self.body.description,
            resource_type=self.body.resourceType,
            url=self.body.url,
            author=self.body.author,
            is_free=self.body.isFree,
            order_index=current + 1,
        )
        await subject_resources_repository.add(self.session, row, flush=True)
        return row


class UpdateMaterialService:
    def __init__(
        self,
        session: AsyncSession,
        actor_id: str,
        material_id: str,
        body: MaterialPatchIn,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.material_id = material_id
        self.body = body

    async def process(self) -> dict:
        row = await subject_resources_repository.by_id(self.session, self.material_id)
        if row is None:
            raise ApiError(404, "Material not found")
        _check_material(self.body, partial=True)
        self._apply(row)
        fields = self.body.model_fields_set
        if "url" in fields or "resourceType" in fields:
            validate_resource_url(row.resource_type, row.url)
        await self.session.flush()
        await RecordAuditService(
            self.session,
            self.actor_id,
            "material.update",
            "SubjectResource",
            row.title,
            row.id,
        ).process()
        return _material(row)

    def _apply(self, row: SubjectResource) -> None:
        fields = self.body.model_fields_set
        if "title" in fields and self.body.title is not None:
            row.title = self.body.title
        if "description" in fields:
            row.description = self.body.description
        if "resourceType" in fields and self.body.resourceType is not None:
            row.resource_type = self.body.resourceType
        if "url" in fields and self.body.url is not None:
            row.url = self.body.url
        if "author" in fields:
            row.author = self.body.author
        if "isFree" in fields and self.body.isFree is not None:
            row.is_free = self.body.isFree


class DeleteMaterialService:
    def __init__(self, session: AsyncSession, actor_id: str, material_id: str) -> None:
        self.session = session
        self.actor_id = actor_id
        self.material_id = material_id

    async def process(self) -> dict:
        row = await subject_resources_repository.by_id(self.session, self.material_id)
        if row is None:
            raise ApiError(404, "Material not found")
        await RecordAuditService(
            self.session,
            self.actor_id,
            "material.delete",
            "SubjectResource",
            row.title,
            row.id,
        ).process()
        await subject_resources_repository.remove(self.session, row)
        return {"ok": True}


class ListAnnouncementsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def process(self) -> list[dict]:
        rows = await announcements_repository.recent(self.session, limit=50)
        return [_announcement(row) for row in rows]


class CreateAnnouncementService:
    def __init__(
        self, session: AsyncSession, actor_id: str, body: AnnouncementCreateIn
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.body = body

    async def process(self) -> dict:
        title, text, url, audience, days = self._parse()
        announcement = await self._create(title, text, url, audience, days)
        await self._queue_deliveries(announcement, audience)
        await RecordAuditService(
            self.session,
            self.actor_id,
            "announcement.send",
            "Announcement",
            title,
            announcement.id,
        ).process()
        return {
            "id": announcement.id,
            "recipientCount": announcement.recipient_count,
        }

    def _parse(self) -> tuple[str, str, str | None, dict, int]:
        title = (self.body.title or "").strip()
        text = (self.body.body or "").strip()
        if not 1 <= len(title) <= 60 or not 1 <= len(text) <= 180:
            raise ApiError(400, "Title is 1–60 characters and body is 1–180")
        url = _internal_path(self.body.url)
        audience = _audience(_audience_payload(self.body.audience))
        days = self.body.expiresInDays
        if not isinstance(days, int) or not 1 <= days <= 30:
            raise ApiError(400, "expiresInDays must be 1–30")
        return title, text, url, audience, days

    async def _create(
        self,
        title: str,
        text: str,
        url: str | None,
        audience: dict,
        days: int,
    ) -> Announcement:
        announcement = Announcement(
            id=cuid(),
            title=title,
            body=text,
            url=url,
            audience=audience,
            status="QUEUED",
            expires_at=utcnow() + timedelta(days=days),
        )
        await announcements_repository.add(self.session, announcement, flush=True)
        return announcement

    async def _queue_deliveries(
        self, announcement: Announcement, audience: dict
    ) -> None:
        subscriptions = await push_subscriptions_repository.for_audience(
            self.session, audience
        )
        for subscription in subscriptions:
            await announcement_deliveries_repository.add(
                self.session,
                AnnouncementDelivery(
                    id=cuid(),
                    announcement_id=announcement.id,
                    subscription_id=subscription.id,
                    status="PENDING",
                ),
            )
        announcement.recipient_count = len(subscriptions)
        if not subscriptions:
            announcement.status = "SENT"
            announcement.completed_at = utcnow()
        await self.session.flush()


class CancelAnnouncementService:
    def __init__(
        self, session: AsyncSession, actor_id: str, announcement_id: str
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.announcement_id = announcement_id

    async def process(self) -> dict:
        row = await announcements_repository.by_id(self.session, self.announcement_id)
        if row is None:
            raise ApiError(404, "Announcement not found")
        if row.status not in {"QUEUED", "SENDING"}:
            raise ApiError(409, "Announcement cannot be cancelled")
        deliveries = await announcement_deliveries_repository.pending_for(
            self.session, row.id
        )
        for delivery in deliveries:
            delivery.status = "CANCELLED"
        row.status = "CANCELLED"
        await RecordAuditService(
            self.session,
            self.actor_id,
            "announcement.cancel",
            "Announcement",
            row.title,
            row.id,
        ).process()
        return {"ok": True}


class PreviewAnnouncementAudienceService:
    def __init__(self, session: AsyncSession, audience: AudienceIn | None) -> None:
        self.session = session
        self.audience = audience

    async def process(self) -> dict:
        parsed = _audience(_audience_payload(self.audience))
        matching = await users_repository.for_audience(self.session, parsed)
        subscriptions = await push_subscriptions_repository.for_audience(
            self.session, parsed
        )
        subscribed = {row.user_id for row in subscriptions}
        return {
            "students": len(matching),
            "subscribedStudents": len(subscribed),
            "devices": len(subscriptions),
        }


class TestAnnouncementService:
    def __init__(
        self, session: AsyncSession, actor_id: str, body: AnnouncementTestIn
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.body = body

    async def process(self) -> dict:
        if not settings.push_enabled:
            raise ApiError(503, "Push is not configured")
        contact = (self.body.contact or "").strip()
        student = await users_repository.by_student_contact(self.session, contact)
        if student is None:
            raise ApiError(404, "Student not found")
        subscriptions = await push_subscriptions_repository.for_user(
            self.session, student.id
        )
        sent = await self._send(subscriptions)
        await RecordAuditService(
            self.session,
            self.actor_id,
            "announcement.test",
            "User",
            contact,
            student.id,
        ).process()
        name = f"{student.first_name or ''} {student.last_name or ''}".strip()
        return {
            "devices": len(subscriptions),
            "sent": sent,
            "student": name,
        }

    async def _send(self, subscriptions) -> int:
        payload = {
            "title": (self.body.title or "")[:60],
            "body": (self.body.body or "")[:180],
            "url": _internal_path(self.body.url) or "/dashboard",
            "tag": "announcement-test",
        }
        sent = 0
        for subscription in subscriptions:
            outcome = send_notification(subscription, payload)
            if outcome == "sent":
                sent += 1
            elif outcome == "gone":
                await push_subscriptions_repository.remove(self.session, subscription)
        return sent


class CreateAcademicTermService:
    def __init__(
        self, session: AsyncSession, actor_id: str, body: AcademicTermIn
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.body = body

    async def process(self) -> dict:
        candidate = _term_body(self.body)
        rows = await academic_terms_repository.all(self.session) or []
        validate_terms(list(rows), candidate)
        row = AcademicTerm(
            id=cuid(),
            session=candidate.session,
            term=candidate.term,
            starts_on=date.fromisoformat(candidate.starts_on),
            ends_on=date.fromisoformat(candidate.ends_on),
        )
        await academic_terms_repository.add(self.session, row, flush=True)
        await RecordAuditService(
            self.session,
            self.actor_id,
            "academic-term.create",
            "AcademicTerm",
            candidate.session,
            row.id,
        ).process()
        return _term(row)


class UpdateAcademicTermService:
    def __init__(
        self,
        session: AsyncSession,
        actor_id: str,
        term_id: str,
        body: AcademicTermIn,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.term_id = term_id
        self.body = body

    async def process(self) -> dict:
        row = await academic_terms_repository.by_id(self.session, self.term_id)
        if row is None:
            raise ApiError(404, "Academic term not found")
        candidate = _term_body(self.body)
        rows = await academic_terms_repository.all(self.session) or []
        validate_terms(list(rows), candidate, ignore_id=row.id)
        row.session = candidate.session
        row.term = candidate.term
        row.starts_on = date.fromisoformat(candidate.starts_on)
        row.ends_on = date.fromisoformat(candidate.ends_on)
        await RecordAuditService(
            self.session,
            self.actor_id,
            "academic-term.update",
            "AcademicTerm",
            candidate.session,
            row.id,
        ).process()
        return _term(row)


class DeleteAcademicTermService:
    def __init__(self, session: AsyncSession, actor_id: str, term_id: str) -> None:
        self.session = session
        self.actor_id = actor_id
        self.term_id = term_id

    async def process(self) -> dict:
        row = await academic_terms_repository.by_id(self.session, self.term_id)
        if row is None:
            raise ApiError(404, "Academic term not found")
        await RecordAuditService(
            self.session,
            self.actor_id,
            "academic-term.delete",
            "AcademicTerm",
            row.session,
            row.id,
        ).process()
        await academic_terms_repository.remove(self.session, row)
        return {"ok": True}


class GetAuditLogService:
    def __init__(self, session: AsyncSession, page: int, action: str | None) -> None:
        self.session = session
        self.page = page
        self.action = action

    async def process(self) -> dict:
        rows, total = await audits_repository.page(
            self.session, self.page, action=self.action
        )
        return {
            "entries": [
                {
                    "id": row.id,
                    "actorId": row.actor_id,
                    "action": row.action,
                    "entity": row.entity,
                    "entityId": row.entity_id,
                    "summary": row.summary,
                    "createdAt": (
                        row.created_at.isoformat() if row.created_at else None
                    ),
                }
                for row in rows
            ],
            "pagination": {
                "page": self.page,
                "pageSize": 50,
                "total": total,
            },
        }


class GetAdminStatsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def process(self) -> dict:
        return {
            "students": await users_repository.count(
                self.session, User.role == "STUDENT"
            ),
            "questions": await questions_repository.count(self.session),
            "attempts": await attempts_repository.count(self.session),
        }


class GetLessonTreeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def process(self) -> dict:
        subject_rows = await subjects_repository.ordered(self.session)
        topic_rows = await topics_repository.all_ordered(
            self.session, Topic.order_index
        )
        counts = await lessons_repository.counts_by_topic(self.session)
        return {
            "subjects": [
                {
                    "id": subject.id,
                    "name": subject.name,
                    "slug": subject.slug,
                    "topics": [
                        {
                            "id": topic.id,
                            "title": topic.title,
                            "slug": topic.slug,
                            "lessonCount": counts.get(topic.id, 0),
                        }
                        for topic in topic_rows
                        if topic.subject_id == subject.id
                    ],
                }
                for subject in subject_rows
            ]
        }


class GetStudentAnnouncementsService:
    def __init__(self, session: AsyncSession, user: User) -> None:
        self.session = session
        self.user = user

    async def process(self) -> dict:
        plan = await plans_repository.active(self.session, self.user.id)
        dismissed = await announcement_dismissals_repository.ids_for_user(
            self.session, self.user.id
        )
        rows = await announcements_repository.active_not_cancelled(
            self.session, now=utcnow()
        )
        visible = []
        for row in rows:
            if row.id in dismissed:
                continue
            if not audience_matches(
                row.audience or {},
                self.user,
                plan.target_exam if plan else None,
            ):
                continue
            visible.append(
                {
                    "id": row.id,
                    "title": row.title,
                    "body": row.body,
                    "url": row.url,
                    "expiresAt": (
                        row.expires_at.isoformat() if row.expires_at else None
                    ),
                }
            )
        return {"announcements": visible}


class DismissAnnouncementService:
    def __init__(
        self, session: AsyncSession, user_id: str, announcement_id: str
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.announcement_id = announcement_id

    async def process(self) -> dict:
        row = await announcements_repository.by_id(self.session, self.announcement_id)
        if row is None:
            raise ApiError(404, "Announcement not found")
        existing = await announcement_dismissals_repository.by_id(
            self.session, (self.announcement_id, self.user_id)
        )
        if existing is None:
            await announcement_dismissals_repository.add(
                self.session,
                AnnouncementDismissal(
                    announcement_id=self.announcement_id,
                    user_id=self.user_id,
                ),
            )
        return {"ok": True}


def audience_matches(audience: dict, user: User, target_exam: str | None) -> bool:
    if not audience:
        return True
    if "classLevels" in audience and user.class_level not in audience["classLevels"]:
        return False
    if "tracks" in audience and user.track not in audience["tracks"]:
        return False
    if "tiers" in audience and user.tier not in audience["tiers"]:
        return False
    if "userIds" in audience and user.id not in audience["userIds"]:
        return False
    return not (
        "examTargets" in audience and target_exam not in audience["examTargets"]
    )


def _check_material(body: MaterialCreateIn | MaterialPatchIn, partial: bool) -> None:
    fields = body.model_fields_set
    if not partial:
        title = body.title or ""
        if not 2 <= len(title) <= 200:
            raise ApiError(400, "Title must be 2–200 characters")
        if body.resourceType not in {"PDF", "IMAGE", "VIDEO", "LINK"}:
            raise ApiError(400, "Unknown material type")
        if not body.url:
            raise ApiError(400, "A URL is required")
    if "title" in fields and not 2 <= len(body.title or "") <= 200:
        raise ApiError(400, "Title must be 2–200 characters")
    if body.description and len(body.description) > 600:
        raise ApiError(400, "Description must be 600 characters or fewer")
    if body.author and len(body.author) > 120:
        raise ApiError(400, "Author must be 120 characters or fewer")
    if "resourceType" in fields and body.resourceType not in {
        "PDF",
        "IMAGE",
        "VIDEO",
        "LINK",
    }:
        raise ApiError(400, "Unknown material type")


def _audience(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ApiError(400, "Invalid audience")
    parsed: dict = {}
    for key, allowed in _AUDIENCE_KEYS.items():
        if key not in raw or raw[key] is None:
            continue
        values = raw[key]
        if not isinstance(values, list) or any(item not in allowed for item in values):
            raise ApiError(400, "Invalid audience")
        parsed[key] = values
    if "userIds" in raw and raw["userIds"] is not None:
        ids = raw["userIds"]
        if (
            not isinstance(ids, list)
            or len(ids) > 1000
            or any(not isinstance(item, str) for item in ids)
        ):
            raise ApiError(400, "Invalid audience")
        parsed["userIds"] = ids
    unknown = set(raw) - set(_AUDIENCE_KEYS) - {"userIds"}
    if unknown:
        raise ApiError(400, "Invalid audience")
    return parsed


def _internal_path(url: str | None) -> str | None:
    if url in (None, ""):
        return None
    parsed = urlparse(url)
    if (
        parsed.scheme
        or parsed.netloc
        or not url.startswith("/")
        or url.startswith("//")
    ):
        raise ApiError(400, "Announcement links must be an internal path")
    return url


def _audience_payload(audience: AudienceIn | None) -> dict:
    if audience is None:
        return {}
    return audience.model_dump(exclude_unset=True)


def _term_body(body: AcademicTermIn) -> TermRange:
    try:
        return TermRange(body.session, body.term, body.startsOn, body.endsOn)
    except TypeError as exc:
        raise ApiError(400, "A term needs session, term, startsOn, and endsOn") from exc


def _term(row: AcademicTerm) -> dict:
    return {
        "id": row.id,
        "session": row.session,
        "term": row.term,
        "startsOn": str(row.starts_on)[:10],
        "endsOn": str(row.ends_on)[:10],
    }


def _material(row: SubjectResource) -> dict:
    return {
        "id": row.id,
        "subjectId": row.subject_id,
        "title": row.title,
        "description": row.description,
        "resourceType": row.resource_type,
        "url": row.url,
        "author": row.author,
        "isFree": row.is_free,
        "orderIndex": row.order_index,
    }


def _announcement(row: Announcement) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "body": row.body,
        "url": row.url,
        "audience": row.audience,
        "status": row.status,
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
        "recipientCount": row.recipient_count,
        "sentCount": row.sent_count,
        "failedCount": row.failed_count,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "completedAt": (row.completed_at.isoformat() if row.completed_at else None),
    }
