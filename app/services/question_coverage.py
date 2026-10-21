"""Admin question coverage (what is populated) and export (download it)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ApiError
from app.database.repositories.curriculum import subjects_repository
from app.database.repositories.provider import fetches_repository
from app.database.repositories.question import questions_repository
from app.services.admin import RecordAuditService
from app.services.question_export import (
    EXPORT_FORMATS,
    EXPORT_ROW_CAP,
    MEDIA_TYPES,
    build_coverage,
    build_zip,
    export_row,
    group_papers,
    paper_filename,
    render,
)


class GetQuestionCoverageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def process(self) -> dict:
        counts = await questions_repository.coverage_counts(self.session)
        statuses = await fetches_repository.paper_statuses(
            self.session, settings.question_provider
        )
        return {"subjects": build_coverage(counts, statuses)}


class ExportQuestionsService:
    """One paper as a file, or a whole subject as a zip of papers."""

    def __init__(
        self,
        session: AsyncSession,
        actor_id: str,
        *,
        subject_id: str,
        exam_type: str | None,
        exam_year: int | None,
        fmt: str,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.subject_id = subject_id
        self.exam_type = exam_type
        self.exam_year = exam_year
        self.fmt = fmt

    async def process(self) -> tuple[bytes, str, str]:
        self._validate()
        subject = await subjects_repository.by_id(self.session, self.subject_id)
        if subject is None:
            raise ApiError(404, "Subject not found")
        pairs = await questions_repository.for_export(
            self.session,
            self.subject_id,
            self.exam_type,
            self.exam_year,
            EXPORT_ROW_CAP + 1,
        )
        if len(pairs) > EXPORT_ROW_CAP:
            raise ApiError(
                400,
                "Too many questions to export at once. Choose an exam type or year.",
            )
        if not pairs:
            raise ApiError(404, "No questions to export")
        rows = [export_row(question, topic) for question, topic in pairs]
        if self.exam_year is not None:
            content = render(rows, self.fmt)
            media_type = MEDIA_TYPES[self.fmt]
            filename = paper_filename(
                subject.slug, self.exam_type, self.exam_year, self.fmt
            )
        else:
            content = build_zip(subject.slug, group_papers(rows), self.fmt)
            media_type = "application/zip"
            filename = f"{subject.slug}.zip"
        await RecordAuditService(
            self.session,
            self.actor_id,
            "question.export",
            "Subject",
            f"{subject.slug} {self.exam_type or 'all'} "
            f"{self.exam_year or 'all years'} {self.fmt} ({len(rows)} rows)",
            subject.id,
        ).process()
        return content, media_type, filename

    def _validate(self) -> None:
        if self.fmt not in EXPORT_FORMATS:
            raise ApiError(400, "Format must be csv or json")
        if self.exam_year is not None and not self.exam_type:
            raise ApiError(400, "Choose an exam type to export a single year")
