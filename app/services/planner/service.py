import logging
from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import PositionIn, StudyPlanIn, TimetableIn
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.timeutil import lagos_day_key, utcnow
from app.database.models import (
    StudyPlan,
    StudyPlanItem,
    StudyPlanPosition,
)
from app.database.repositories.curriculum import (
    curriculum_levels_repository,
    subjects_repository,
    topic_edges_repository,
    topics_repository,
)
from app.database.repositories.learning import metrics_repository
from app.database.repositories.planner import (
    academic_terms_repository,
    plan_items_repository,
    plan_positions_repository,
    plans_repository,
)
from app.services.learning.graph import GraphEdge, GraphNode, build_graph
from app.services.learning.mastery import graph_colour
from app.services.learning.mastery_store import GetTopicMasteryService
from app.services.planner.days import add_days, days_between
from app.services.planner.layout import FixedItem, RevisionDue
from app.services.planner.mode import (
    CARRY_OVER_DAYS,
    DEFAULT_MINUTES,
    MANUAL_LOOKBACK_DAYS,
    PlanSettingsCheck,
    compute_runway_start,
    plan_settings_problem,
    resolve_plan_mode,
    scope_ordinal,
)
from app.services.planner.slots import Availability, SlotAllocation
from app.services.planner.term_context import (
    TermRange,
    default_term,
    resolve_term_context,
    term_header_label,
)
from app.services.planner.term_plan import PlannerInput, PlannerSubject, plan_window
from app.services.planner.timetable import (
    day_minutes_overrides,
    load_timetable,
    stored_timetable,
    timetable_issues,
    timetable_summary,
    timetable_warnings,
)
from app.services.planner.topics import CarryOver, PlanTopic, calendar_topic_id

logger = logging.getLogger(__name__)


def _carry_over(pending, today: str) -> list[CarryOver]:
    """Topics from pending sessions dated before today, in plan order."""
    return [
        CarryOver(item.topic_id, item.subject_id, _day(item.date))
        for item in pending
        if _day(item.date) < today and item.topic_id
    ]


def _availability(plan: StudyPlan) -> Availability:
    """The plan's hours, plus the timetable when the student chose custom subjects."""
    timetable = load_timetable(plan.timetable)
    allocations = (
        [
            SlotAllocation(row.day, row.subjectId, row.minutes, row.strength)
            for row in timetable.allocations
        ]
        if timetable is not None and timetable.mode == "CUSTOM"
        else []
    )
    return Availability(
        study_days=list(plan.study_days or []),
        weekday_minutes=plan.weekday_minutes,
        weekend_minutes=plan.weekend_minutes,
        day_minutes=day_minutes_overrides(timetable),
        allocations=allocations,
    )


def _day(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _as_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


async def _active(session: AsyncSession, student_id: str) -> StudyPlan | None:
    return await plans_repository.active(session, student_id)


def position_options(
    topics: list[PlanTopic], class_level: str, term: str
) -> list[dict]:
    """Topics a student may pick as "my class is here": the plan's own term only."""
    allowed = [t for t in topics if t.class_level == class_level and t.term == term]
    allowed.sort(key=lambda t: (t.order_index, t.id))
    return [
        {
            "id": t.id,
            "title": t.title,
            "scope": f"{t.class_level} {t.term.capitalize()} term",
        }
        for t in allowed
    ]


async def _plan_topics(session: AsyncSession, subject_id: str) -> list[PlanTopic]:
    """Topics with a curriculum level. Unmapped topics are left out of every plan."""
    rows = await topics_repository.for_subject(session, subject_id)
    topics = []
    for topic in rows:
        curriculum = (
            await curriculum_levels_repository.by_id(session, topic.curriculum_level_id)
            if topic.curriculum_level_id
            else None
        )
        if curriculum is None:
            logger.warning(
                "Topic %s (subject %s) has no curriculum level; "
                "left out of study plans",
                topic.id,
                subject_id,
            )
            continue
        topics.append(
            PlanTopic(
                id=topic.id,
                subject_id=topic.subject_id,
                title=topic.title,
                slug=topic.slug,
                order_index=topic.order_index,
                estimated_minutes=topic.estimated_minutes,
                waec_weight=topic.waec_weight,
                jamb_weight=topic.jamb_weight,
                prerequisite_topic_id=topic.prerequisite_topic_id,
                class_level=curriculum.class_level,
                term=curriculum.term,
            )
        )
    return topics


async def _term_context(session: AsyncSession, today: str) -> dict:
    rows = await academic_terms_repository.all_ordered(session)
    configured = [
        TermRange(row.session, row.term, _day(row.starts_on), _day(row.ends_on))
        for row in rows
    ]
    return resolve_term_context(today, configured)


def _resolve_timetable(body: StudyPlanIn, stored: dict | None) -> TimetableIn | None:
    """Omitted keeps the stored timetable, so older clients never wipe a student's
    choices; an explicit null returns the plan to Smart."""
    if "timetable" in body.model_fields_set:
        return body.timetable
    return load_timetable(stored)


def _settings_problem(
    body: StudyPlanIn, timetable: TimetableIn | None = None
) -> str | None:
    """The first problem with the plan's own settings, before any timetable."""
    target = body.targetDate.isoformat() if body.targetDate else None
    problem = plan_settings_problem(
        PlanSettingsCheck(
            class_level=body.classLevel,
            target_date=target,
            force_exam_mode=body.forceExamMode,
            study_days=body.studyDays,
            weekday_minutes=body.weekdayMinutes,
            weekend_minutes=body.weekendMinutes,
            today=lagos_day_key(),
            day_minutes=day_minutes_overrides(timetable),
        )
    )
    if problem:
        return problem
    if bool(body.targetExam) != bool(body.targetDate):
        return "An exam target needs both an exam and a date"
    return None


def _timetable_issues(body: StudyPlanIn, timetable: TimetableIn | None) -> list:
    return timetable_issues(
        timetable,
        study_days=body.studyDays,
        weekday_minutes=body.weekdayMinutes,
        weekend_minutes=body.weekendMinutes,
        subject_ids=body.subjectIds,
    )


def _check(
    body: StudyPlanIn, timetable: TimetableIn | None = None, kept: bool = False
) -> None:
    problem = _settings_problem(body, timetable)
    if problem:
        raise ApiError(400, problem)
    issues = _timetable_issues(body, timetable)
    if issues:
        message = issues[0].message
        raise ApiError(
            400,
            f"Your saved timetable no longer fits these settings. {message}"
            if kept
            else message,
        )


async def _ensure_subjects(
    session: AsyncSession,
    subject_ids: list[str],
    track: str | None = None,
    existing_ids: Sequence[str] = (),
) -> None:
    """Subjects must exist, be active and fit the student's track.

    CORE is shared by every track, subjects already in the plan stay allowed so
    an old plan can still be edited, and no track means no track check.
    """
    for subject_id in subject_ids:
        subject = await subjects_repository.by_id(session, subject_id)
        if subject is None or not subject.is_active:
            raise ApiError(404, "Subject not found")
        if (
            track
            and subject.track_category not in {"CORE", track}
            and subject_id not in existing_ids
        ):
            raise ApiError(400, "That subject is not part of your track")


async def _drop_positions_outside_scope(session: AsyncSession, plan: StudyPlan) -> None:
    """A position belongs to one class and term; changing scope invalidates it."""
    for row in await plan_positions_repository.for_plan(session, plan.id):
        topic = (
            await topics_repository.by_id(session, row.topic_id)
            if row.topic_id
            else None
        )
        curriculum = (
            await curriculum_levels_repository.by_id(session, topic.curriculum_level_id)
            if topic and topic.curriculum_level_id
            else None
        )
        if curriculum is None or (curriculum.class_level, curriculum.term) != (
            plan.scope_class_level,
            plan.scope_term,
        ):
            await plan_positions_repository.remove(session, row)


def selectable_subjects(rows, track: str | None, plan_subject_ids) -> list:
    """Subjects offered when planning: the student's track plus CORE.

    Subjects already in the plan stay listed so they can be seen and removed.
    No track means no filter.
    """
    if not track:
        return list(rows)
    keep = set(plan_subject_ids or [])
    return [
        subject
        for subject in rows
        if subject.track_category in {"CORE", track} or subject.id in keep
    ]


class GetStudyPlanService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        class_level: str | None,
        track: str | None = None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.class_level = class_level
        self.track = track

    async def process(self) -> dict:
        today = lagos_day_key()
        profile_class = self.class_level or "SS1"
        plan = await _active(self.session, self.student_id)
        if plan is not None and (
            plan.last_replanned_at is None or _day(plan.last_replanned_at) < today
        ):
            await ReplanStudyPlanService(self.session, plan, profile_class).process()
        ctx = await _term_context(self.session, today)
        subject_rows = selectable_subjects(
            await subjects_repository.active_ordered(self.session),
            self.track,
            plan.subject_ids if plan is not None else [],
        )
        return {
            "today": today,
            "classLevel": self.class_level,
            "termLabel": term_header_label(ctx),
            "termSource": ctx["source"],
            "currentTerm": default_term(ctx),
            "daysToExam": self._days_to_exam(plan, today),
            "defaults": DEFAULT_MINUTES.get(profile_class),
            "subjects": [
                {
                    "id": subject.id,
                    "name": subject.name,
                    "code": subject.code,
                    "slug": subject.slug,
                }
                for subject in subject_rows
            ],
            "plan": None if plan is None else await self._serialize(plan, ctx),
        }

    def _days_to_exam(self, plan: StudyPlan | None, today: str) -> int | None:
        if plan is None or plan.target_date is None:
            return None
        mode = resolve_plan_mode(
            plan.scope_class_level, _day(plan.target_date), plan.force_exam_mode
        )
        if mode == "TERM":
            return None
        from app.services.planner.days import days_between

        return days_between(today, _day(plan.target_date))

    async def _serialize(self, plan: StudyPlan, ctx: dict) -> dict:
        # Items already laid out for a subject that has since been deactivated
        # are hidden now; the next replan stops generating them.
        active_ids = await subjects_repository.active_ids(self.session)
        subjects = {
            subject.id: subject
            for subject in await subjects_repository.active_ordered(self.session)
        }
        plan_items = [
            item
            for item in await plan_items_repository.for_plan(self.session, plan.id)
            if item.subject_id in active_ids
        ]
        topic_rows = await topics_repository.for_ids(
            self.session, [i.topic_id for i in plan_items if i.topic_id]
        )
        topic_by_id = {topic.id: topic for topic in topic_rows}
        positions = {
            row.subject_id: row.topic_id
            for row in await plan_positions_repository.for_plan(self.session, plan.id)
            if row.subject_id in active_ids and row.topic_id
        }
        class_level = plan.scope_class_level
        term = plan.scope_term
        follow_calendar = class_level == (self.class_level or "SS1")
        target = _day(plan.target_date) if plan.target_date else None
        mode = resolve_plan_mode(class_level, target, plan.force_exam_mode)
        plan_start = _day(plan.created_at) if plan.created_at else lagos_day_key()
        runway_start = (
            compute_runway_start(plan_start, target)
            if target and mode != "TERM"
            else None
        )
        subject_ids = [s for s in plan.subject_ids or [] if s in active_ids]
        calendar_positions: dict[str, str | None] = {}
        options: dict[str, list[dict]] = {}
        for subject_id in subject_ids:
            topics = await _plan_topics(self.session, subject_id)
            calendar_positions[subject_id] = calendar_topic_id(
                topics, class_level, term, follow_calendar, ctx
            )
            options[subject_id] = position_options(topics, class_level, term)
        return {
            "id": plan.id,
            "mode": mode,
            "subjectIds": subject_ids,
            "targetExam": plan.target_exam,
            "targetDate": target,
            "forceExamMode": plan.force_exam_mode,
            "studyDays": plan.study_days,
            "weekdayMinutes": plan.weekday_minutes,
            "weekendMinutes": plan.weekend_minutes,
            "plannedThrough": _day(plan.planned_through)
            if plan.planned_through
            else None,
            "runwayStart": runway_start,
            "outline": plan.outline,
            "overload": plan.overload,
            "timetable": plan.timetable,
            "unmet": plan.unmet or [],
            "items": [
                {
                    "id": item.id,
                    "date": _day(item.date),
                    "subjectId": item.subject_id,
                    "subject": {
                        "name": subjects[item.subject_id].name,
                        "code": subjects[item.subject_id].code,
                        "slug": subjects[item.subject_id].slug,
                    },
                    "topicId": item.topic_id,
                    "topicSlug": topic_by_id[item.topic_id].slug
                    if item.topic_id in topic_by_id
                    else None,
                    "topicTitle": topic_by_id[item.topic_id].title
                    if item.topic_id in topic_by_id
                    else None,
                    "activityType": item.activity_type,
                    "durationMinutes": item.duration_minutes,
                    "status": item.status,
                    "notes": item.notes,
                    "completedAt": item.completed_at.isoformat()
                    if item.completed_at
                    else None,
                    "completionSource": item.completion_source,
                    "carriedFrom": _day(item.carried_from_date)
                    if item.carried_from_date
                    else None,
                }
                for item in plan_items
            ],
            "positions": positions,
            "calendarPositions": calendar_positions,
            "positionOptions": options,
            "scope": {"classLevel": class_level, "term": term},
            "followsCalendar": follow_calendar
            and (
                (ctx["kind"] == "in_term" and ctx["current"].term == term)
                or (
                    ctx["kind"] == "holiday"
                    and bool(ctx.get("previous"))
                    and ctx["previous"].term == term
                )
            ),
        }


class CreateStudyPlanService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        class_level: str | None,
        body: StudyPlanIn,
        track: str | None = None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.class_level = class_level
        self.body = body
        self.track = track

    async def process(self) -> dict:
        timetable = self.body.timetable
        _check(self.body, timetable)
        previous = await plans_repository.list_active(self.session, self.student_id)
        await _ensure_subjects(
            self.session,
            self.body.subjectIds,
            self.track,
            [subject_id for row in previous for subject_id in row.subject_ids or []],
        )
        for row in previous:
            row.is_active = False
        plan = StudyPlan(
            id=cuid(),
            student_id=self.student_id,
            subject_ids=self.body.subjectIds,
            target_exam=self.body.targetExam,
            target_date=_as_date(self.body.targetDate),
            force_exam_mode=self.body.forceExamMode,
            study_days=self.body.studyDays,
            weekday_minutes=self.body.weekdayMinutes,
            weekend_minutes=self.body.weekendMinutes,
            scope_class_level=self.body.classLevel,
            scope_term=self.body.term,
            timetable=stored_timetable(timetable),
            is_active=True,
        )
        await plans_repository.add(self.session, plan, flush=True)
        await ReplanStudyPlanService(
            self.session, plan, self.class_level or "SS1"
        ).process()
        return {"planId": plan.id}


class UpdateStudyPlanSettingsService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        class_level: str | None,
        plan_id: str,
        body: StudyPlanIn,
        track: str | None = None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.class_level = class_level
        self.plan_id = plan_id
        self.body = body
        self.track = track

    async def process(self) -> dict:
        plan = await plans_repository.by_id(self.session, self.plan_id)
        if plan is None:
            raise ApiError(404, "Study plan not found")
        if plan.student_id != self.student_id:
            raise ApiError(404, "Study plan not found")
        timetable = _resolve_timetable(self.body, plan.timetable)
        _check(self.body, timetable, kept="timetable" not in self.body.model_fields_set)
        await _ensure_subjects(
            self.session, self.body.subjectIds, self.track, plan.subject_ids or []
        )
        plan.subject_ids = self.body.subjectIds
        plan.target_exam = self.body.targetExam
        plan.target_date = _as_date(self.body.targetDate)
        plan.force_exam_mode = self.body.forceExamMode
        plan.study_days = self.body.studyDays
        plan.weekday_minutes = self.body.weekdayMinutes
        plan.weekend_minutes = self.body.weekendMinutes
        plan.scope_class_level = self.body.classLevel
        plan.scope_term = self.body.term
        plan.timetable = stored_timetable(timetable)
        await _drop_positions_outside_scope(self.session, plan)
        await ReplanStudyPlanService(
            self.session, plan, self.class_level or "SS1"
        ).process()
        return {"ok": True}


class PreviewStudyPlanService:
    """Dry run of a create or update: validate the settings and describe the week.

    Nothing is written and nothing is replanned. It judges the body as sent, so an
    omitted `timetable` means no timetable here (a save would keep the stored one).
    The summary describes what the student asked for; the schedule the planner
    will build from it is not part of the preview yet.
    """

    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        class_level: str | None,
        body: StudyPlanIn,
        track: str | None = None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.class_level = class_level
        self.body = body
        self.track = track

    async def process(self) -> dict:
        body = self.body
        timetable = body.timetable
        errors: list[dict] = []
        problem = _settings_problem(body, timetable)
        if problem:
            errors.append(
                {"code": "SETTINGS", "message": problem, "day": None, "subjectId": None}
            )
        previous = await plans_repository.list_active(self.session, self.student_id)
        try:
            await _ensure_subjects(
                self.session,
                body.subjectIds,
                self.track,
                [
                    subject_id
                    for row in previous
                    for subject_id in row.subject_ids or []
                ],
            )
        except ApiError as exc:
            errors.append(
                {
                    "code": "SUBJECTS",
                    "message": str(exc.payload["error"]),
                    "day": None,
                    "subjectId": None,
                }
            )
        errors.extend(issue.as_dict() for issue in _timetable_issues(body, timetable))
        if errors:
            return {
                "valid": False,
                "errors": errors,
                "warnings": [],
                "summary": None,
                "window": None,
            }
        names = await self._subject_names()
        warnings = timetable_warnings(
            timetable,
            study_days=body.studyDays,
            weekday_minutes=body.weekdayMinutes,
            weekend_minutes=body.weekendMinutes,
            subject_names=names,
            runway_days=self._runway_days(previous),
        )
        summary = timetable_summary(
            timetable,
            study_days=body.studyDays,
            weekday_minutes=body.weekdayMinutes,
            weekend_minutes=body.weekendMinutes,
            subject_ids=body.subjectIds,
        )
        return {
            "valid": True,
            "errors": [],
            "warnings": [issue.as_dict() for issue in warnings],
            "summary": summary,
            "window": await self._window(previous, timetable, names),
        }

    async def _window(
        self,
        previous: list[StudyPlan],
        timetable: TimetableIn | None,
        names: dict[str, str],
    ) -> dict:
        """The first 14 days this plan would get, exactly as a replan builds them.

        It reads the student's real progress (missed sessions, today's finished
        work, lessons done) but changes nothing.
        """
        body = self.body
        existing = previous[0] if previous else None
        draft = StudyPlan(
            id=existing.id if existing else "preview",
            student_id=self.student_id,
            subject_ids=body.subjectIds,
            target_exam=body.targetExam,
            target_date=_as_date(body.targetDate),
            force_exam_mode=body.forceExamMode,
            study_days=body.studyDays,
            weekday_minutes=body.weekdayMinutes,
            weekend_minutes=body.weekendMinutes,
            scope_class_level=body.classLevel,
            scope_term=body.term,
            timetable=stored_timetable(timetable),
            is_active=False,
            created_at=existing.created_at if existing else None,
        )
        replanner = ReplanStudyPlanService(
            self.session, draft, self.class_level or "SS1", read_only=True
        )
        today = lagos_day_key()
        planner_input = await replanner.build_input(
            today,
            await replanner.pending_carry_over(today) if existing else [],
            await replanner._settled_items(today) if existing else [],
        )
        output = plan_window(planner_input)
        titles = {
            topic.id: topic.title
            for subject in planner_input.subjects
            for topic in subject.topics
        }
        overload = output["overload"]
        return {
            "plannedThrough": output["plannedThrough"],
            "items": [
                {
                    "date": item.date,
                    "subjectId": item.subject_id,
                    "subjectName": names.get(item.subject_id, item.subject_id),
                    "topicId": item.topic_id,
                    "topicTitle": titles.get(item.topic_id) if item.topic_id else None,
                    "activityType": item.activity_type,
                    "durationMinutes": item.duration_minutes,
                    "notes": item.notes,
                    "carriedFrom": item.carried_from,
                }
                for item in output["items"]
            ],
            "unmet": output["unmet"],
            "overload": None
            if overload is None
            else {
                "topicsBehind": overload.topics_behind,
                "suggestedExtraMinutesPerWeek": (
                    overload.suggested_extra_minutes_per_week
                ),
            },
        }

    async def _subject_names(self) -> dict[str, str]:
        names: dict[str, str] = {}
        for subject_id in self.body.subjectIds:
            subject = await subjects_repository.by_id(self.session, subject_id)
            names[subject_id] = subject.name if subject else subject_id
        return names

    def _runway_days(self, previous: list[StudyPlan]) -> int | None:
        target = self.body.targetDate.isoformat() if self.body.targetDate else None
        mode = resolve_plan_mode(self.body.classLevel, target, self.body.forceExamMode)
        if not target or mode == "TERM":
            return None
        started = (
            previous[0].created_at if previous and previous[0].created_at else None
        )
        runway_start = compute_runway_start(
            _day(started) if started else lagos_day_key(), target
        )
        return days_between(runway_start, target) + 1


class DeleteStudyPlanService:
    """Soft delete: the plan stops being active; its items and history are kept."""

    def __init__(self, session: AsyncSession, student_id: str, plan_id: str) -> None:
        self.session = session
        self.student_id = student_id
        self.plan_id = plan_id

    async def process(self) -> dict:
        plan = await plans_repository.by_id(self.session, self.plan_id)
        if plan is None:
            raise ApiError(404, "Study plan not found")
        if plan.student_id != self.student_id:
            raise ApiError(404, "Study plan not found")
        await plans_repository.lock(self.session, StudyPlan.id == plan.id)
        plan.is_active = False
        return {"planId": plan.id}


class SetStudyPlanPositionsService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        class_level: str | None,
        positions: list[PositionIn],
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.class_level = class_level
        self.positions = positions

    async def process(self) -> dict:
        plan = await _active(self.session, self.student_id)
        if plan is None:
            raise ApiError(404, "No active study plan")
        subject_ids = set(plan.subject_ids or [])
        for item in self.positions:
            await self._apply_position(plan, subject_ids, item)
        await ReplanStudyPlanService(
            self.session, plan, self.class_level or "SS1"
        ).process()
        return {"ok": True}

    async def _apply_position(
        self, plan: StudyPlan, subject_ids: set[str], item: PositionIn
    ) -> None:
        if item.subjectId not in subject_ids:
            raise ApiError(400, "That subject is not on the plan")
        existing = await plan_positions_repository.for_plan_subject(
            self.session, plan.id, item.subjectId
        )
        if item.topicId is None:
            if existing:
                await plan_positions_repository.remove(self.session, existing)
            return
        topic = await topics_repository.by_id(self.session, item.topicId)
        if topic is None or topic.subject_id != item.subjectId:
            raise ApiError(400, "Topic is not on that subject")
        curriculum = (
            await curriculum_levels_repository.by_id(
                self.session, topic.curriculum_level_id
            )
            if topic.curriculum_level_id
            else None
        )
        if curriculum is None or (curriculum.class_level, curriculum.term) != (
            plan.scope_class_level,
            plan.scope_term,
        ):
            raise ApiError(400, "That topic is outside your plan's term")
        if existing:
            existing.topic_id = topic.id
        else:
            await plan_positions_repository.add(
                self.session,
                StudyPlanPosition(
                    id=cuid(),
                    study_plan_id=plan.id,
                    subject_id=item.subjectId,
                    topic_id=topic.id,
                ),
            )


class SetStudyPlanItemStatusService:
    def __init__(
        self, session: AsyncSession, student_id: str, item_id: str, status: str
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.item_id = item_id
        self.status = status

    async def process(self) -> dict:
        item = await plan_items_repository.by_id(self.session, self.item_id)
        if item is None:
            raise ApiError(404, "Plan item not found")
        plan = await plans_repository.by_id(self.session, item.study_plan_id)
        if plan is None or plan.student_id != self.student_id:
            raise ApiError(404, "Plan item not found")
        today = lagos_day_key()
        item_day = _day(item.date)
        if item_day > today:
            raise ApiError(400, "Future items cannot be changed yet")
        if self.status in {"COMPLETED", "SKIPPED"} and item_day < add_days(
            today, -MANUAL_LOOKBACK_DAYS
        ):
            raise ApiError(400, "That session is outside the 7 day lookback")
        item.status = self.status
        if self.status == "COMPLETED":
            item.completion_source = "MANUAL"
            item.completed_at = utcnow()
        elif self.status == "PENDING":
            item.completed_at = None
            item.completion_source = None
        else:
            item.completion_source = "MANUAL"
        return {"status": item.status}


class MarkStudyPlanItemService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        activity: str,
        subject_id: str | None = None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.activity = activity
        self.subject_id = subject_id

    async def process(self) -> None:
        plan = await _active(self.session, self.student_id)
        if plan is None:
            return
        today = lagos_day_key()
        earliest = add_days(today, -CARRY_OVER_DAYS)
        items = await plan_items_repository.pending_for_activity(
            self.session, plan.id, self.activity
        )
        for item in items:
            day = _day(item.date)
            if day < earliest or day > today:
                continue
            if self.subject_id and item.subject_id != self.subject_id:
                continue
            result = await plan_items_repository.update_where(
                self.session,
                {
                    "status": "COMPLETED",
                    "completion_source": "AUTO",
                    "completed_at": utcnow(),
                },
                StudyPlanItem.id == item.id,
                StudyPlanItem.status == "PENDING",
            )
            if isinstance(result, CursorResult) and result.rowcount:
                return


def _scope_topic_ids(subjects: list[PlannerSubject], plan: StudyPlan) -> set[str]:
    """Ids of topics in the plan's scope or an earlier one, never a later one."""
    limit = scope_ordinal(plan.scope_class_level, plan.scope_term)
    return {
        topic.id
        for subject in subjects
        for topic in subject.topics
        if scope_ordinal(topic.class_level, topic.term) <= limit
    }


class ReplanStudyPlanService:
    def __init__(
        self,
        session: AsyncSession,
        plan: StudyPlan,
        class_level: str,
        read_only: bool = False,
    ) -> None:
        self.session = session
        self.plan = plan
        self.class_level = class_level
        # A preview builds the same planner input but must leave nothing behind.
        self.read_only = read_only

    async def process(self) -> None:
        await plans_repository.lock(self.session, StudyPlan.id == self.plan.id)
        today = lagos_day_key()
        # Work already settled today keeps its time; the rest is regenerated.
        settled = await self._settled_items(today)
        carry = await self._miss_and_clear(today)
        output = plan_window(await self.build_input(today, carry, settled))
        await self._persist_output(output)

    async def build_input(
        self, today: str, carry: list[CarryOver], settled: list[FixedItem]
    ) -> PlannerInput:
        planner_subjects, nodes = await self._build_subjects()
        edges = await self._edges(nodes)
        state, pretest_passed, revision_due = await self._learning_inputs(
            nodes, _scope_topic_ids(planner_subjects, self.plan)
        )
        position_rows = await plan_positions_repository.for_plan(
            self.session, self.plan.id
        )
        target = _day(self.plan.target_date) if self.plan.target_date else None
        scope_class = self.plan.scope_class_level
        mode = resolve_plan_mode(scope_class, target, self.plan.force_exam_mode)
        ctx = await _term_context(self.session, today)
        return PlannerInput(
            today=today,
            plan_start=_day(self.plan.created_at) if self.plan.created_at else today,
            mode=mode,
            class_level=scope_class,
            term=self.plan.scope_term,
            home_class_level=self.class_level,
            target_date=target,
            term_context=ctx,
            availability=_availability(self.plan),
            subjects=planner_subjects,
            graph=build_graph(nodes, edges),
            state=state,
            pretest_passed=pretest_passed,
            revision_due=revision_due,
            positions={row.subject_id: row.topic_id for row in position_rows},
            carry_over=carry,
            fixed=settled,
            lessons_done=await self._lessons_done(),
        )

    async def _settled_items(self, today: str) -> list[FixedItem]:
        """Completed or skipped sessions from today on; they keep their time."""
        return [
            FixedItem(_day(item.date), item.subject_id, item.duration_minutes)
            for item in await plan_items_repository.for_plan(self.session, self.plan.id)
            if item.status in {"COMPLETED", "SKIPPED"} and _day(item.date) >= today
        ]

    async def _learning_inputs(
        self, nodes: list[GraphNode], in_scope: set[str]
    ) -> tuple[dict, set[str], list[RevisionDue]]:
        """Mastery, pretests and fading topics; a failure must not fail a replan.

        Only topics in `in_scope` (the plan's scope or earlier) may be revised.
        """
        topic_ids = [node.id for node in nodes]
        if not topic_ids:
            return {}, set(), []
        try:
            async with self.session.begin_nested() as savepoint:
                state = await GetTopicMasteryService(
                    self.session, self.plan.student_id, topic_ids
                ).process()
                passed = set(
                    await metrics_repository.passed_topic_ids(
                        self.session, self.plan.student_id, topic_ids
                    )
                )
                if self.read_only:
                    # Mastery caches what it derives; a preview keeps none of it.
                    await savepoint.rollback()
        except Exception:
            logger.exception(
                "Could not load learning state for plan of student %s",
                self.plan.student_id,
            )
            return {}, set(), []
        due = [
            RevisionDue(
                topic_id=node.id,
                subject_id=node.subject_id,
                title=node.title,
                reason="Fading from memory, time to refresh it",
            )
            for node in nodes
            if node.id in in_scope
            and node.id in state
            and graph_colour(state[node.id]) == "DECAYED"
        ]
        return state, passed, due

    async def _lessons_done(self) -> dict[str, int]:
        """Completed lessons per topic, so a replan does not teach them again."""
        done: dict[str, int] = {}
        for item in await plan_items_repository.for_plan(self.session, self.plan.id):
            if (
                item.status == "COMPLETED"
                and item.activity_type == "LESSON"
                and item.topic_id
            ):
                done[item.topic_id] = done.get(item.topic_id, 0) + 1
        return done

    async def _miss_and_clear(self, today: str) -> list[CarryOver]:
        pending = await plan_items_repository.pending_for_plan(
            self.session, self.plan.id
        )
        carry = _carry_over(pending, today)
        for item in pending:
            if _day(item.date) < today:
                item.status = "MISSED"
            else:
                await plan_items_repository.remove(self.session, item)
        return carry

    async def pending_carry_over(self, today: str) -> list[CarryOver]:
        """What a replan would carry over, without changing any item."""
        return _carry_over(
            await plan_items_repository.pending_for_plan(self.session, self.plan.id),
            today,
        )

    async def _build_subjects(
        self,
    ) -> tuple[list[PlannerSubject], list[GraphNode]]:
        planner_subjects = []
        nodes: list[GraphNode] = []
        for subject_id in self.plan.subject_ids or []:
            subject = await subjects_repository.by_id(self.session, subject_id)
            if subject is None or not subject.is_active:
                continue
            built = await _plan_topics(self.session, subject_id)
            nodes.extend(
                GraphNode(
                    topic.id,
                    topic.subject_id,
                    topic.title,
                    topic.slug,
                    topic.order_index,
                    topic.estimated_minutes,
                    topic.waec_weight,
                    topic.jamb_weight,
                    topic.prerequisite_topic_id,
                )
                for topic in built
            )
            planner_subjects.append(PlannerSubject(subject.id, subject.name, built))
        return planner_subjects, nodes

    async def _edges(self, nodes: list[GraphNode]) -> list[GraphEdge]:
        if not nodes:
            return []
        edge_rows = await topic_edges_repository.all_ordered(self.session)
        return [
            GraphEdge(
                row.id,
                row.prereq_topic_id,
                row.topic_id,
                row.kind,
                row.strength,
                row.rationale,
            )
            for row in edge_rows
        ]

    async def _persist_output(self, output: dict) -> None:
        for item in output["items"]:
            await plan_items_repository.add(
                self.session,
                StudyPlanItem(
                    id=cuid(),
                    study_plan_id=self.plan.id,
                    date=_as_date(item.date),
                    subject_id=item.subject_id,
                    topic_id=item.topic_id,
                    activity_type=item.activity_type,
                    duration_minutes=item.duration_minutes,
                    status="PENDING",
                    notes=item.notes,
                    carried_from_date=_as_date(item.carried_from),
                ),
            )
        self.plan.outline = output["outline"]
        self.plan.overload = (
            None
            if output["overload"] is None
            else {
                "topicsBehind": output["overload"].topics_behind,
                "suggestedExtraMinutesPerWeek": output[
                    "overload"
                ].suggested_extra_minutes_per_week,
            }
        )
        self.plan.unmet = output["unmet"] or None
        self.plan.planned_through = _as_date(output["plannedThrough"])
        self.plan.last_replanned_at = utcnow()
