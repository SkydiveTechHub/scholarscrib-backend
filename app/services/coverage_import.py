"""Import for the coverage question bank, separate from the main question bank."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import CoverageImportQuestionIn, ImportCoverageQuestionsIn
from app.core.errors import ApiError
from app.core.ids import cuid
from app.database.models import CoverageQuestion
from app.database.repositories.coverage import coverage_questions_repository
from app.database.repositories.curriculum import subjects_repository, topics_repository
from app.services.admin import RecordAuditService
from app.services.provider.rules import objective_ok

EXAM_TYPES = {"WAEC", "JAMB", "NECO", "CUSTOM"}
QUESTION_TYPES = {"OBJECTIVE", "THEORY", "FILL_IN_BLANK"}
DIFFICULTIES = {"BASIC", "INTERMEDIATE", "ADVANCED"}
MAX_ROWS = 500


class ImportCoverageQuestionsService:
    def __init__(
        self, session: AsyncSession, actor_id: str, body: ImportCoverageQuestionsIn
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.body = body

    async def process(self) -> dict:
        rows = self.body.questions
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
            raise ApiError(400, f"questions must contain 1 to {MAX_ROWS} rows")
        imported = 0
        skipped = 0
        errors = []
        for index, row in enumerate(rows):
            try:
                if await self._import_row(row, self.body.skipDuplicates):
                    imported += 1
                else:
                    skipped += 1
            except ApiError as exc:
                errors.append({"index": index, "reason": exc.payload["error"]})
        if imported:
            await RecordAuditService(
                self.session,
                self.actor_id,
                "coverage_question.import",
                "CoverageQuestion",
                f"Imported {imported}",
                None,
            ).process()
        return {
            "message": f"Imported {imported}, skipped {skipped}, {len(errors)} errors",
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
        }

    async def _import_row(self, row: CoverageImportQuestionIn, skip: bool) -> bool:
        subject = None
        if isinstance(row.subjectCode, str):
            subject = await subjects_repository.by_code(self.session, row.subjectCode)
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
                400, "Question text and explanation must be at least 5 characters"
            )
        exam_type = (row.examType or "").strip().upper()
        if exam_type not in EXAM_TYPES:
            raise ApiError(400, "Unknown exam type")
        question_type = (row.questionType or "OBJECTIVE").strip().upper()
        if question_type not in QUESTION_TYPES:
            raise ApiError(400, "Unknown question type")
        difficulty = (row.difficulty or "INTERMEDIATE").strip().upper()
        if difficulty not in DIFFICULTIES:
            raise ApiError(400, "Unknown difficulty")
        if row.correctAnswer is None:
            raise ApiError(400, "correctAnswer is required")
        if question_type == "OBJECTIVE" and not objective_ok(
            row.options, row.correctAnswer
        ):
            raise ApiError(
                400,
                "Objective questions need at least 4 options and a matching answer",
            )
        if row.hasPassage and not (row.passage or "").strip():
            raise ApiError(400, "hasPassage is true but passage is empty")
        if skip and await coverage_questions_repository.duplicate_id(
            self.session,
            subject_id=subject.id,
            exam_type=exam_type,
            exam_year=row.examYear,
            question_number=row.questionNumber,
            question_text=text,
        ):
            return False
        await coverage_questions_repository.add(
            self.session,
            CoverageQuestion(
                id=cuid(),
                subject_id=subject.id,
                topic_id=topic_id,
                exam_type=exam_type,
                exam_year=row.examYear,
                country=(row.country or "NG").strip().upper(),
                question_number=row.questionNumber,
                question_type=question_type,
                has_passage=row.hasPassage,
                passage=row.passage,
                question_text=text,
                question_image=row.questionImage,
                options=row.options,
                correct_answer=str(row.correctAnswer).strip().upper(),
                explanation=row.explanation,
                detailed_explanation=row.detailedExplanation,
                possible_mistakes=row.possibleMistakes,
                solution_image=row.solutionImage,
                difficulty=difficulty,
                marks=row.marks or 1,
            ),
            flush=True,
        )
        return True


class ListCoverageQuestionsService:
    def __init__(
        self, session: AsyncSession, page: int, page_size: int, filters: dict
    ) -> None:
        self.session = session
        self.page = page
        self.page_size = page_size
        self.filters = filters

    async def process(self) -> dict:
        rows, total = await coverage_questions_repository.list_filtered(
            self.session, self.page, self.page_size, self.filters
        )
        return {
            "questions": [coverage_row(*row) for row in rows],
            "pagination": {
                "page": self.page,
                "pageSize": self.page_size,
                "total": total,
                "totalPages": max(1, -(-total // self.page_size)),
            },
        }


def coverage_row(question, subject, topic) -> dict:
    return {
        "id": question.id,
        "questionNumber": question.question_number,
        "questionText": question.question_text,
        "questionType": question.question_type,
        "examType": question.exam_type,
        "examYear": question.exam_year,
        "country": question.country,
        "difficulty": question.difficulty,
        "hasPassage": question.has_passage,
        "subject": {"name": subject.name, "code": subject.code},
        "topic": {"title": topic.title, "slug": topic.slug} if topic else None,
    }
