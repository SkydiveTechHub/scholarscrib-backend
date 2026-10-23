"""Student account tools. Mutations propose; services write after approval."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.api.schemas import StudentProfileIn, StudentStatusIn, StudentTierIn
from app.services.admin import (
    DeleteStudentService,
    ForceSignOutService,
    GetStudentDetailService,
    SearchStudentsService,
    SetStudentStatusService,
    SetStudentTierService,
    UpdateStudentService,
)
from app.services.admin_astra.tools.factory import register_mutation, register_read
from app.services.console import GetStudentDeletionImpactService


class SearchStudentsArgs(BaseModel):
    query: str | None = None
    page: int = Field(default=1, ge=1, le=20)
    classLevel: str | None = None
    track: str | None = None
    tier: str | None = None
    status: str | None = None
    state: str | None = None


class UserIdArgs(BaseModel):
    userId: str = Field(min_length=1, max_length=64)


class UpdateStudentArgs(StudentProfileIn):
    userId: str = Field(min_length=1, max_length=64)


class SetStudentStatusArgs(StudentStatusIn):
    userId: str = Field(min_length=1, max_length=64)


class SetStudentTierArgs(StudentTierIn):
    userId: str = Field(min_length=1, max_length=64)


async def _search(ctx, args: SearchStudentsArgs) -> dict:
    return await SearchStudentsService(
        ctx.session,
        args.query,
        args.page,
        20,
        class_level=args.classLevel,
        track=args.track,
        tier=args.tier,
        status=args.status,
        state=args.state,
    ).process()


async def _detail(ctx, args: UserIdArgs) -> dict:
    data = await GetStudentDetailService(ctx.session, args.userId).process()
    devices = data.get("devices")
    if isinstance(devices, list):
        data["devices"] = devices[:8]
    return data


async def _impact(ctx, args: UserIdArgs) -> dict:
    return await GetStudentDeletionImpactService(ctx.session, args.userId).process()


async def _update(ctx, args: UpdateStudentArgs) -> dict:
    body = StudentProfileIn.model_validate(args.model_dump(exclude={"userId"}))
    return await UpdateStudentService(
        ctx.session, ctx.admin_id, args.userId, body
    ).process()


async def _status(ctx, args: SetStudentStatusArgs) -> dict:
    body = StudentStatusIn.model_validate(args.model_dump(exclude={"userId"}))
    return await SetStudentStatusService(
        ctx.session, ctx.admin_id, args.userId, body
    ).process()


async def _tier(ctx, args: SetStudentTierArgs) -> dict:
    body = StudentTierIn.model_validate(args.model_dump(exclude={"userId"}))
    return await SetStudentTierService(
        ctx.session, ctx.admin_id, args.userId, body
    ).process()


async def _delete(ctx, args: UserIdArgs) -> dict:
    return await DeleteStudentService(ctx.session, ctx.admin_id, args.userId).process()


async def _signout(ctx, args: UserIdArgs) -> dict:
    return await ForceSignOutService(ctx.session, ctx.admin_id, args.userId).process()


def register_student_tools() -> None:
    register_read(
        "search_students",
        "students",
        "Search students by name, email, class, track, tier, or status. "
        "Returns ids and labels. Page size is capped.",
        SearchStudentsArgs,
        _search,
    )
    register_read(
        "get_student",
        "students",
        "Student detail: profile, tier, devices, and mastery counts. "
        "Requires a student id from search_students.",
        UserIdArgs,
        _detail,
    )
    register_read(
        "get_student_deletion_impact",
        "students",
        "Counts of rows that a student delete would remove. Read this before "
        "proposing a delete.",
        UserIdArgs,
        _impact,
    )
    register_mutation(
        "update_student",
        "students",
        "Propose a profile update. Does not change tier. Requires the current "
        "first and last name plus the student id.",
        UpdateStudentArgs,
        _update,
    )
    register_mutation(
        "set_student_status",
        "students",
        "Propose suspending or reactivating a student. A suspension needs a reason.",
        SetStudentStatusArgs,
        _status,
    )
    register_mutation(
        "set_student_tier",
        "students",
        "Propose a complimentary tier grant or a freemium revoke. "
        "The student id cannot be edited on the card.",
        SetStudentTierArgs,
        _tier,
        destructive=True,
    )
    register_mutation(
        "delete_student",
        "students",
        "Propose a permanent student delete. Owner only. Read the deletion "
        "impact first. The student id cannot be edited on the card.",
        UserIdArgs,
        _delete,
        owner_only=True,
        destructive=True,
    )
    register_mutation(
        "force_signout_student",
        "students",
        "Propose signing a student out of every device. Owner only. "
        "The student id cannot be edited on the card.",
        UserIdArgs,
        _signout,
        owner_only=True,
        destructive=True,
    )
