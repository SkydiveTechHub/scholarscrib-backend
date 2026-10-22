"""Lessons, materials, academic terms, and catalogue tools."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.api.schemas import (
    AcademicTermIn,
    CurriculumCreateIn,
    CurriculumPatchIn,
    LessonImportIn,
    MaterialCreateIn,
    MaterialPatchIn,
    SubjectCreateIn,
    SubjectPatchIn,
    TopicCreateIn,
    TopicPatchIn,
)
from app.database.repositories.planner import academic_terms_repository
from app.services.admin_astra.tools.factory import (
    EmptyArgs,
    register_mutation,
    register_read,
)
from app.services.console import (
    CreateAcademicTermService,
    CreateCurriculumService,
    CreateMaterialService,
    CreateSubjectService,
    CreateTopicService,
    DeleteAcademicTermService,
    DeleteCurriculumService,
    DeleteMaterialService,
    DeleteSubjectService,
    DeleteTopicService,
    GetLessonBrowseService,
    GetLessonForTopicService,
    GetLessonTreeService,
    GetMaterialSubjectsService,
    ImportLessonService,
    ListAdminCurriculumsService,
    ListAdminSubjectsService,
    ListAdminTopicsService,
    ListMaterialsService,
    UpdateAcademicTermService,
    UpdateCurriculumService,
    UpdateMaterialService,
    UpdateSubjectService,
    UpdateTopicService,
)


class BrowseLessonsArgs(BaseModel):
    subjectId: str | None = None
    classLevel: str | None = None
    term: str | None = None


class TopicIdArgs(BaseModel):
    topicId: str = Field(min_length=1, max_length=64)


class ImportLessonArgs(BaseModel):
    topicId: str = Field(min_length=1, max_length=64)
    markdown: str = Field(min_length=1, max_length=100_000)


class SubjectIdArgs(BaseModel):
    subjectId: str = Field(min_length=1, max_length=64)


class UpdateSubjectArgs(SubjectPatchIn):
    subjectId: str = Field(min_length=1, max_length=64)


class ListCurriculumsArgs(BaseModel):
    subjectId: str | None = None


class CurriculumIdArgs(BaseModel):
    curriculumId: str = Field(min_length=1, max_length=64)


class UpdateCurriculumArgs(CurriculumPatchIn):
    curriculumId: str = Field(min_length=1, max_length=64)


class CreateTopicArgs(TopicCreateIn):
    curriculumId: str = Field(min_length=1, max_length=64)


class UpdateTopicArgs(TopicPatchIn):
    topicId: str = Field(min_length=1, max_length=64)


class UpdateMaterialArgs(MaterialPatchIn):
    materialId: str = Field(min_length=1, max_length=64)


class MaterialIdArgs(BaseModel):
    materialId: str = Field(min_length=1, max_length=64)


class UpdateTermArgs(AcademicTermIn):
    termId: str = Field(min_length=1, max_length=64)


class TermIdArgs(BaseModel):
    termId: str = Field(min_length=1, max_length=64)


def _term_row(row) -> dict:
    return {
        "id": row.id,
        "session": row.session,
        "term": row.term,
        "startsOn": str(row.starts_on)[:10],
        "endsOn": str(row.ends_on)[:10],
    }


async def _tree(ctx, args: EmptyArgs) -> dict:
    return await GetLessonTreeService(ctx.session).process()


async def _browse(ctx, args: BrowseLessonsArgs) -> dict:
    return await GetLessonBrowseService(
        ctx.session, args.subjectId, args.classLevel, args.term
    ).process()


async def _topic_lesson(ctx, args: TopicIdArgs) -> dict:
    return await GetLessonForTopicService(ctx.session, args.topicId).process()


async def _import_lesson(ctx, args: ImportLessonArgs) -> dict:
    body = LessonImportIn(topicId=args.topicId, markdown=args.markdown, confirm=True)
    return await ImportLessonService(ctx.session, ctx.admin_id, body).process()


async def _material_subjects(ctx, args: EmptyArgs) -> dict:
    return await GetMaterialSubjectsService(ctx.session).process()


async def _materials(ctx, args: SubjectIdArgs) -> list:
    return await ListMaterialsService(ctx.session, args.subjectId).process()


async def _create_material(ctx, args: MaterialCreateIn) -> dict:
    return await CreateMaterialService(ctx.session, ctx.admin_id, args).process()


async def _update_material(ctx, args: UpdateMaterialArgs) -> dict:
    body = MaterialPatchIn.model_validate(args.model_dump(exclude={"materialId"}))
    return await UpdateMaterialService(
        ctx.session, ctx.admin_id, args.materialId, body
    ).process()


async def _delete_material(ctx, args: MaterialIdArgs) -> dict:
    return await DeleteMaterialService(
        ctx.session, ctx.admin_id, args.materialId
    ).process()


async def _terms(ctx, args: EmptyArgs) -> list:
    rows = await academic_terms_repository.ordered(ctx.session)
    return [_term_row(row) for row in rows]


async def _create_term(ctx, args: AcademicTermIn) -> dict:
    return await CreateAcademicTermService(ctx.session, ctx.admin_id, args).process()


async def _update_term(ctx, args: UpdateTermArgs) -> dict:
    body = AcademicTermIn.model_validate(args.model_dump(exclude={"termId"}))
    return await UpdateAcademicTermService(
        ctx.session, ctx.admin_id, args.termId, body
    ).process()


async def _delete_term(ctx, args: TermIdArgs) -> dict:
    return await DeleteAcademicTermService(
        ctx.session, ctx.admin_id, args.termId
    ).process()


async def _subjects(ctx, args: EmptyArgs) -> list:
    return await ListAdminSubjectsService(ctx.session).process()


async def _create_subject(ctx, args: SubjectCreateIn) -> dict:
    return await CreateSubjectService(ctx.session, ctx.admin_id, args).process()


async def _update_subject(ctx, args: UpdateSubjectArgs) -> dict:
    body = SubjectPatchIn.model_validate(args.model_dump(exclude={"subjectId"}))
    return await UpdateSubjectService(
        ctx.session, ctx.admin_id, args.subjectId, body
    ).process()


async def _delete_subject(ctx, args: SubjectIdArgs) -> dict:
    return await DeleteSubjectService(
        ctx.session, ctx.admin_id, args.subjectId
    ).process()


async def _curriculums(ctx, args: ListCurriculumsArgs) -> list:
    return await ListAdminCurriculumsService(ctx.session, args.subjectId).process()


async def _create_curriculum(ctx, args: CurriculumCreateIn) -> dict:
    return await CreateCurriculumService(ctx.session, ctx.admin_id, args).process()


async def _update_curriculum(ctx, args: UpdateCurriculumArgs) -> dict:
    body = CurriculumPatchIn.model_validate(args.model_dump(exclude={"curriculumId"}))
    return await UpdateCurriculumService(
        ctx.session, ctx.admin_id, args.curriculumId, body
    ).process()


async def _delete_curriculum(ctx, args: CurriculumIdArgs) -> dict:
    return await DeleteCurriculumService(
        ctx.session, ctx.admin_id, args.curriculumId
    ).process()


async def _topics(ctx, args: CurriculumIdArgs) -> list:
    return await ListAdminTopicsService(ctx.session, args.curriculumId).process()


async def _create_topic(ctx, args: CreateTopicArgs) -> dict:
    body = TopicCreateIn.model_validate(args.model_dump(exclude={"curriculumId"}))
    return await CreateTopicService(
        ctx.session, ctx.admin_id, args.curriculumId, body
    ).process()


async def _update_topic(ctx, args: UpdateTopicArgs) -> dict:
    body = TopicPatchIn.model_validate(args.model_dump(exclude={"topicId"}))
    return await UpdateTopicService(
        ctx.session, ctx.admin_id, args.topicId, body
    ).process()


async def _delete_topic(ctx, args: TopicIdArgs) -> dict:
    return await DeleteTopicService(ctx.session, ctx.admin_id, args.topicId).process()


def register_content_tools() -> None:
    register_read(
        "lesson_tree",
        "lessons",
        "Lesson tree: subjects, topics, and whether a lesson exists.",
        EmptyArgs,
        _tree,
    )
    register_read(
        "browse_lessons",
        "lessons",
        "Browse lessons for a subject, class, and term.",
        BrowseLessonsArgs,
        _browse,
    )
    register_read(
        "get_topic_lesson",
        "lessons",
        "Lesson summary for one topic: title and block count, not the full markdown.",
        TopicIdArgs,
        _topic_lesson,
    )
    register_mutation(
        "import_lesson",
        "lessons",
        "Propose importing lesson markdown for a topic. Approval is the confirm.",
        ImportLessonArgs,
        _import_lesson,
    )
    register_read(
        "list_material_subjects",
        "materials",
        "Subjects that can own a study material.",
        EmptyArgs,
        _material_subjects,
    )
    register_read(
        "list_materials",
        "materials",
        "Materials for one subject id.",
        SubjectIdArgs,
        _materials,
    )
    register_mutation(
        "create_material",
        "materials",
        "Propose a material. The file must already have a URL. Upload signing "
        "stays in the browser.",
        MaterialCreateIn,
        _create_material,
    )
    register_mutation(
        "update_material",
        "materials",
        "Propose edits to a material. The material id cannot change on the card.",
        UpdateMaterialArgs,
        _update_material,
    )
    register_mutation(
        "delete_material",
        "materials",
        "Propose deleting a material. The id cannot be edited on the card.",
        MaterialIdArgs,
        _delete_material,
        destructive=True,
    )
    register_read(
        "list_academic_terms",
        "terms",
        "Academic terms with session, term, and dates.",
        EmptyArgs,
        _terms,
    )
    register_mutation(
        "create_academic_term",
        "terms",
        "Propose a new academic term. Dates are YYYY-MM-DD.",
        AcademicTermIn,
        _create_term,
    )
    register_mutation(
        "update_academic_term",
        "terms",
        "Propose new dates or a session for one term. The term id stays fixed.",
        UpdateTermArgs,
        _update_term,
    )
    register_mutation(
        "delete_academic_term",
        "terms",
        "Propose deleting an academic term. The id cannot be edited on the card.",
        TermIdArgs,
        _delete_term,
        destructive=True,
    )
    register_read(
        "list_catalogue_subjects",
        "catalogue",
        "Catalogue subjects with ids, codes, and counts.",
        EmptyArgs,
        _subjects,
    )
    register_mutation(
        "create_subject",
        "catalogue",
        "Propose a subject. Owner only.",
        SubjectCreateIn,
        _create_subject,
        owner_only=True,
    )
    register_mutation(
        "update_subject",
        "catalogue",
        "Propose subject edits. Owner only. The subject id stays fixed.",
        UpdateSubjectArgs,
        _update_subject,
        owner_only=True,
    )
    register_mutation(
        "delete_subject",
        "catalogue",
        "Propose deleting a subject with no questions. Owner only.",
        SubjectIdArgs,
        _delete_subject,
        owner_only=True,
        destructive=True,
    )
    register_read(
        "list_curriculums",
        "catalogue",
        "Curriculum levels, optionally for one subject.",
        ListCurriculumsArgs,
        _curriculums,
    )
    register_mutation(
        "create_curriculum",
        "catalogue",
        "Propose a class-and-term curriculum level. Owner only.",
        CurriculumCreateIn,
        _create_curriculum,
        owner_only=True,
    )
    register_mutation(
        "update_curriculum",
        "catalogue",
        "Propose a new class or term on a curriculum level. Owner only.",
        UpdateCurriculumArgs,
        _update_curriculum,
        owner_only=True,
    )
    register_mutation(
        "delete_curriculum",
        "catalogue",
        "Propose deleting a curriculum level that has no topics. Owner only.",
        CurriculumIdArgs,
        _delete_curriculum,
        owner_only=True,
        destructive=True,
    )
    register_read(
        "list_topics",
        "catalogue",
        "Topics inside one curriculum level.",
        CurriculumIdArgs,
        _topics,
    )
    register_mutation(
        "create_topic",
        "catalogue",
        "Propose a topic on a curriculum level. Owner only.",
        CreateTopicArgs,
        _create_topic,
        owner_only=True,
    )
    register_mutation(
        "update_topic",
        "catalogue",
        "Propose topic edits. Owner only. The topic id stays fixed.",
        UpdateTopicArgs,
        _update_topic,
        owner_only=True,
    )
    register_mutation(
        "delete_topic",
        "catalogue",
        "Propose deleting a topic. Owner only.",
        TopicIdArgs,
        _delete_topic,
        owner_only=True,
        destructive=True,
    )
