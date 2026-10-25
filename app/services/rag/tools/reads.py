"""Read-only tutor tools. These run immediately (no approval card)."""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.core.errors import ApiError
from app.core.logger import logger
from app.services.catalogue import ListSubjectsService
from app.services.learning import GetTopicStandingsService
from app.services.pages import GetDashboardService, GetPerformanceService
from app.services.planner.service import GetStudyPlanService
from app.services.rag.tools.context import get_tool_context
from app.services.rag.tools.registry import ToolSpec, register_tool


def _json(payload: Any) -> str:
    return json.dumps(payload, default=str)


def _error(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        return _json({"ok": False, "error": str(exc)})
    logger.exception("tutor read tool failed")
    return _json({"ok": False, "error": "Could not load that right now."})


def _compact_plan(page: dict) -> dict:
    plan = page.get("plan") or None
    today = page.get("today")
    items: list[dict] = []
    settings: dict | None = None
    if isinstance(plan, dict):
        settings = {
            "id": plan.get("id"),
            "subjectIds": plan.get("subjectIds"),
            "studyDays": plan.get("studyDays"),
            "weekdayMinutes": plan.get("weekdayMinutes"),
            "weekendMinutes": plan.get("weekendMinutes"),
            "targetExam": plan.get("targetExam"),
            "targetDate": plan.get("targetDate"),
            # null means the smart timetable. Copy this when changing it.
            "timetable": plan.get("timetable"),
            "unmetTimetableSessions": [
                {
                    "date": row.get("date"),
                    "subjectId": row.get("subjectId"),
                    "strength": row.get("strength"),
                }
                for row in (plan.get("unmet") or [])[:10]
                if isinstance(row, dict)
            ],
            "classLevel": (plan.get("scope") or {}).get("classLevel")
            if isinstance(plan.get("scope"), dict)
            else None,
            "term": (plan.get("scope") or {}).get("term")
            if isinstance(plan.get("scope"), dict)
            else None,
        }
        for item in plan.get("items") or []:
            if not isinstance(item, dict):
                continue
            if today and item.get("date") not in {today, None} and len(items) >= 8:
                continue
            if item.get("date") == today or len(items) < 8:
                items.append(
                    {
                        "id": item.get("id"),
                        "date": item.get("date"),
                        "subjectId": item.get("subjectId"),
                        "topicTitle": item.get("topicTitle"),
                        "activityType": item.get("activityType"),
                        "status": item.get("status"),
                        "durationMinutes": item.get("durationMinutes"),
                    }
                )
            if len(items) >= 12:
                break
    subjects = [
        {"id": row.get("id"), "name": row.get("name"), "slug": row.get("slug")}
        for row in (page.get("subjects") or [])
        if isinstance(row, dict)
    ]
    return {
        "today": today,
        "classLevel": page.get("classLevel"),
        "currentTerm": page.get("currentTerm"),
        "subjects": subjects[:30],
        "plan": None if settings is None else {**settings, "upcomingItems": items},
    }


class EmptyArgs(BaseModel):
    """No arguments."""


class TopicStandingsArgs(BaseModel):
    status: Literal["all", "strong", "weak", "normal"] = "all"
    subjectIds: list[str] | None = Field(
        default=None,
        description="Optional subject ids from list_subjects. Omit for every subject.",
    )
    limit: int = Field(default=20, ge=1, le=50)


async def _get_study_plan() -> str:
    ctx = get_tool_context()
    try:
        page = await GetStudyPlanService(
            ctx.session, ctx.user_id, ctx.class_level, ctx.track
        ).process()
    except Exception as exc:  # noqa: BLE001
        return _error(exc)
    return _json(_compact_plan(page))


async def _list_subjects() -> str:
    ctx = get_tool_context()
    try:
        rows = await ListSubjectsService(ctx.session).process()
    except Exception as exc:  # noqa: BLE001
        return _error(exc)
    compact = [
        {
            "id": row.get("id"),
            "name": row.get("name"),
            "slug": row.get("slug"),
            "code": row.get("code"),
        }
        for row in rows
        if isinstance(row, dict)
    ]
    return _json({"subjects": compact})


async def _dashboard() -> str:
    ctx = get_tool_context()
    try:
        data = await GetDashboardService(
            ctx.session, ctx.user_id, ctx.first_name, ctx.tier, ctx.class_level
        ).process()
    except Exception as exc:  # noqa: BLE001
        return _error(exc)
    today = data.get("todayItems") or []
    return _json(
        {
            "hasStudyPlan": data.get("hasStudyPlan"),
            "streak": data.get("streak"),
            "attemptTotal": data.get("attemptTotal"),
            "accuracy": data.get("accuracy"),
            "todayItems": today[:8] if isinstance(today, list) else [],
        }
    )


async def _performance() -> str:
    ctx = get_tool_context()
    try:
        data = await GetPerformanceService(
            ctx.session, ctx.user_id, ctx.tier, 1, None, ctx.track
        ).process()
    except Exception as exc:  # noqa: BLE001
        return _error(exc)
    subjects = []
    for row in data.get("subjects") or []:
        if not isinstance(row, dict):
            continue
        subjects.append(
            {
                "subjectId": row.get("subjectId"),
                "name": row.get("name"),
                "mastery": row.get("mastery"),
                "accuracy": row.get("accuracy"),
            }
        )
    return _json(
        {
            "attemptTotal": data.get("attemptTotal"),
            "subjects": subjects[:12],
        }
    )


async def _topic_standings(
    status: Literal["all", "strong", "weak", "normal"] = "all",
    subjectIds: list[str] | None = None,
    limit: int = 20,
) -> str:
    ctx = get_tool_context()
    try:
        data = await GetTopicStandingsService(
            ctx.session,
            ctx.user_id,
            status=status,
            subject_ids=subjectIds,
            limit=limit,
        ).process()
    except Exception as exc:  # noqa: BLE001
        return _error(exc)
    topics = []
    for row in data.get("topics") or []:
        if not isinstance(row, dict):
            continue
        topics.append(
            {
                "title": row.get("title"),
                "subjectName": row.get("subjectName"),
                "subjectId": row.get("subjectId"),
                "mastery": row.get("mastery"),
                "status": row.get("status"),
                "level": row.get("level"),
                "observations": row.get("observations"),
            }
        )
    return _json({"status": data.get("status"), "topics": topics})


def _noop_summary(_: dict) -> str:
    return ""


def register_read_tools() -> None:
    empty_specs = [
        (
            "get_study_plan",
            "Load the student's study plan, subjects, and upcoming items. "
            "Call this before a plan change. Never invent subject ids.",
            _get_study_plan,
        ),
        (
            "list_subjects",
            "List catalogue subjects with ids. Do not invent ids.",
            _list_subjects,
        ),
        (
            "get_dashboard_summary",
            "Dashboard snapshot: streak, study plan flag, today's items.",
            _dashboard,
        ),
        (
            "get_performance_summary",
            "Performance snapshot: attempts and per-subject accuracy.",
            _performance,
        ),
    ]
    for name, description, fn in empty_specs:
        tool = StructuredTool.from_function(
            coroutine=fn,
            name=name,
            description=description,
            args_schema=EmptyArgs,
        )
        register_tool(
            ToolSpec(
                name=name,
                mutating=False,
                feature=None,
                tool=tool,
                summary_fn=_noop_summary,
            )
        )
    standings = StructuredTool.from_function(
        coroutine=_topic_standings,
        name="get_topic_standings",
        description=(
            "Topic-level standing for this student: strong, weak, normal, or all. "
            "Use for 'what am I bad at', weak topics, or strong topics. "
            "Optional subjectIds from list_subjects; default status is all."
        ),
        args_schema=TopicStandingsArgs,
    )
    register_tool(
        ToolSpec(
            name="get_topic_standings",
            mutating=False,
            feature=None,
            tool=standings,
            summary_fn=_noop_summary,
        )
    )
