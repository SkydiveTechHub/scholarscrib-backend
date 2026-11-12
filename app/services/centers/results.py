"""Assignment-scoped tutor analytics (P-1..P-4, P-6, D7). Never TopicMastery."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.timeutil import as_utc, utcnow
from app.database.models import (
    AssessmentAttempt,
    Assignment,
    CenterMembership,
    CoverageQuestion,
    Question,
    QuestionResponse,
    User,
)
from app.database.repositories.assessment import attempts_repository
from app.database.repositories.centers import (
    assignment_targets_repository,
    assignments_repository,
    memberships_repository,
)
from app.database.repositories.curriculum import topics_repository
from app.database.repositories.identity import users_repository
from app.services.assessments.grading import waec_grade
from app.services.centers.assignments import (
    _assignment_out,
    _OwnedCenterMixin,
    _settings,
    resolve_target_user_ids,
)
from app.services.centers.audit import record_tutor_access
from app.services.gamification.improvement import Attempt as TrendAttempt
from app.services.gamification.improvement import compare as trend_compare
from app.services.learning.evidence import OBSERVATION_FLOOR

THIN_TOPIC_COPY = "{n} question{s} so far — assign more on this topic to see a pattern"
THIN_TREND_COPY = "Not enough tasks to tell"
FOCUS_LOSS_THRESHOLD = 1


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _display_name(user: User | None, *, fallback: str = "Student") -> str:
    if user is None:
        return fallback
    name = " ".join(
        part for part in (user.first_name or "", user.last_name or "") if part
    ).strip()
    return name or user.email or fallback


def _pick_attempt(
    attempts: list[AssessmentAttempt], scoring: str
) -> AssessmentAttempt | None:
    completed = [row for row in attempts if row.status == "COMPLETED"]
    in_progress = [row for row in attempts if row.status == "IN_PROGRESS"]
    if not completed and not in_progress:
        return None
    if scoring == "best" and completed:
        return max(
            completed,
            key=lambda row: (
                row.percentage if row.percentage is not None else -1.0,
                as_utc(row.completed_at) if row.completed_at else datetime.min,
            ),
        )
    if scoring == "first" and completed:
        return min(
            completed,
            key=lambda row: as_utc(row.started_at),
        )
    # latest (default): prefer newest completed, else in-progress
    if completed:
        return max(
            completed,
            key=lambda row: as_utc(row.completed_at or row.started_at),
        )
    return max(in_progress, key=lambda row: as_utc(row.started_at))


def _student_row_status(
    *,
    attempt: AssessmentAttempt | None,
    due_at: datetime | None,
    now: datetime,
) -> str:
    past_due = bool(due_at and now > as_utc(due_at))
    if attempt is None:
        return "overdue" if past_due else "not_started"
    if attempt.status == "IN_PROGRESS":
        return "in_progress"
    if attempt.status == "COMPLETED":
        if (
            due_at is not None
            and attempt.completed_at is not None
            and as_utc(attempt.completed_at) > as_utc(due_at)
        ):
            return "late"
        return "submitted"
    return "not_started"


def _focus_loss_flag(away_events: int | None) -> dict[str, Any]:
    count = int(away_events or 0)
    return {
        "flagged": count >= FOCUS_LOSS_THRESHOLD,
        "awayEvents": count,
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _topic_evidence_row(
    *,
    topic_id: str,
    topic_title: str | None,
    correct: int,
    total: int,
) -> dict[str, Any]:
    thin = total < OBSERVATION_FLOOR
    row: dict[str, Any] = {
        "topicId": topic_id,
        "topicTitle": topic_title,
        "correct": correct,
        "total": total,
        "evidenceLevel": "THIN" if thin else "ENOUGH",
    }
    if thin:
        row["accuracy"] = None
        row["status"] = None
        row["message"] = THIN_TOPIC_COPY.format(n=total, s="" if total == 1 else "s")
    else:
        accuracy = 0.0 if total == 0 else round(100 * correct / total, 1)
        row["accuracy"] = accuracy
        if accuracy >= 80:
            row["status"] = "strong"
        elif accuracy >= 60:
            row["status"] = "competent"
        elif accuracy >= 40:
            row["status"] = "developing"
        else:
            row["status"] = "weak"
        row["message"] = None
    return row


class _ResultsMixin(_OwnedCenterMixin):
    async def _assignment_attempts(self, assignment_id: str) -> list[AssessmentAttempt]:
        return await attempts_repository.list_where(
            self.session,
            AssessmentAttempt.assignment_id == assignment_id,
            order_by=(AssessmentAttempt.started_at.asc(),),
            limit=5000,
        )

    async def _active_targets(self, assignment: Assignment) -> list[str]:
        targets = await assignment_targets_repository.for_assignment(
            self.session, assignment.id
        )
        return await resolve_target_user_ids(
            self.session, assignment.center_id, targets
        )

    async def _require_active_member(self, student_id: str) -> CenterMembership:
        membership = await memberships_repository.by_center_user(
            self.session, self.center_id, student_id
        )
        if (
            membership is None
            or membership.kind != "STUDENT"
            or membership.status != "ACTIVE"
        ):
            # D5: per-student detail hidden after leave/remove.
            raise ApiError(404, "Student not found in this center")
        return membership


class AssignmentOverviewService(_ResultsMixin):
    """P-1: counts, score distribution, averages — assignment_id scoped only."""

    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        assignment_id: str,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.assignment_id = assignment_id

    async def process(self) -> dict[str, Any]:
        assignment = await self._load_assignment(self.assignment_id)
        settings = _settings(assignment.settings)
        scoring = str(settings.get("attemptScoring") or "latest")
        target_ids = await self._active_targets(assignment)
        attempts = await self._assignment_attempts(assignment.id)
        by_student: dict[str, list[AssessmentAttempt]] = defaultdict(list)
        for attempt in attempts:
            by_student[attempt.student_id].append(attempt)

        now = utcnow()
        counts = {
            "notStarted": 0,
            "inProgress": 0,
            "submitted": 0,
            "late": 0,
            "overdue": 0,
        }
        scores: list[float] = []
        times: list[float] = []
        for student_id in target_ids:
            picked = _pick_attempt(by_student.get(student_id, []), scoring)
            status = _student_row_status(
                attempt=picked, due_at=assignment.due_at, now=now
            )
            key = {
                "not_started": "notStarted",
                "in_progress": "inProgress",
                "submitted": "submitted",
                "late": "late",
                "overdue": "overdue",
            }[status]
            counts[key] += 1
            if (
                picked is not None
                and picked.status == "COMPLETED"
                and picked.percentage is not None
            ):
                scores.append(float(picked.percentage))
                if picked.time_spent_seconds is not None:
                    times.append(float(picked.time_spent_seconds))

        # Aggregate also includes historical leavers' completed attempts (D5).
        for student_id, rows in by_student.items():
            if student_id in target_ids:
                continue
            picked = _pick_attempt(rows, scoring)
            if (
                picked is not None
                and picked.status == "COMPLETED"
                and picked.percentage is not None
            ):
                scores.append(float(picked.percentage))
                if picked.time_spent_seconds is not None:
                    times.append(float(picked.time_spent_seconds))

        distribution = Counter()
        for score in scores:
            band = int(score // 10) * 10
            distribution[f"{band}-{band + 9}"] += 1

        await record_tutor_access(
            self.session,
            actor_id=self.tutor_id,
            center_id=self.center_id,
            action="view_assignment_overview",
            resource=f"assignment:{assignment.id}",
            meta={"assignmentId": assignment.id},
        )

        avg = round(sum(scores) / len(scores), 1) if scores else None
        median = _median(scores) if scores else None
        return {
            "assignment": _assignment_out(assignment),
            "framing": {
                "basedOnTasks": 1,
                "label": "Based on this assignment",
            },
            "counts": counts,
            "targetedStudents": len(target_ids),
            "scores": {
                "average": avg,
                "median": round(median, 1) if median is not None else None,
                "highest": max(scores) if scores else None,
                "lowest": min(scores) if scores else None,
                "distribution": [
                    {"band": band, "count": distribution[band]}
                    for band in sorted(
                        distribution.keys(), key=lambda text: int(text.split("-")[0])
                    )
                ],
            },
            "averageTimeSpentSeconds": (
                round(sum(times) / len(times), 1) if times else None
            ),
        }


class AssignmentStudentRowsService(_ResultsMixin):
    """P-2: per-student rows; D5 hides left students."""

    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        assignment_id: str,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.assignment_id = assignment_id

    async def process(self) -> dict[str, Any]:
        assignment = await self._load_assignment(self.assignment_id)
        settings = _settings(assignment.settings)
        scoring = str(settings.get("attemptScoring") or "latest")
        target_ids = await self._active_targets(assignment)
        attempts = await self._assignment_attempts(assignment.id)
        by_student: dict[str, list[AssessmentAttempt]] = defaultdict(list)
        for attempt in attempts:
            by_student[attempt.student_id].append(attempt)

        users: dict[str, User] = {}
        if target_ids:
            users = {
                user.id: user
                for user in await users_repository.list_where(
                    self.session,
                    User.id.in_(target_ids),
                    limit=len(target_ids),
                )
            }
        now = utcnow()
        rows: list[dict[str, Any]] = []
        not_started: list[dict[str, Any]] = []
        overdue: list[dict[str, Any]] = []
        for student_id in target_ids:
            user = users.get(student_id)
            picked = _pick_attempt(by_student.get(student_id, []), scoring)
            status = _student_row_status(
                attempt=picked, due_at=assignment.due_at, now=now
            )
            grade = None
            if picked is not None and picked.percentage is not None:
                grade, _remark, _credit = waec_grade(picked.percentage)
            row = {
                "studentId": student_id,
                "name": _display_name(user),
                "classLevel": getattr(user, "class_level", None),
                "track": getattr(user, "track", None),
                "status": status,
                "score": picked.percentage if picked else None,
                "grade": grade or (picked.grade if picked else None),
                "timeSpentSeconds": (picked.time_spent_seconds if picked else None),
                "submittedAt": _iso(picked.completed_at if picked else None),
                "attemptId": picked.id if picked else None,
                "focusLoss": _focus_loss_flag(picked.away_events if picked else None),
            }
            rows.append(row)
            if status == "not_started":
                not_started.append(row)
            elif status == "overdue":
                overdue.append(row)

        await record_tutor_access(
            self.session,
            actor_id=self.tutor_id,
            center_id=self.center_id,
            action="view_assignment_students",
            resource=f"assignment:{assignment.id}",
            meta={"assignmentId": assignment.id, "rowCount": len(rows)},
        )
        return {
            "assignmentId": assignment.id,
            "students": rows,
            "notStarted": not_started,
            "overdue": overdue,
        }


class AssignmentItemAnalysisService(_ResultsMixin):
    """P-3: per-question class stats from assignment-linked completed attempts."""

    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        assignment_id: str,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.assignment_id = assignment_id

    async def process(self) -> dict[str, Any]:
        assignment = await self._load_assignment(self.assignment_id)
        if not assignment.assessment_id:
            return {
                "assignmentId": assignment.id,
                "items": [],
                "message": "No paper on this assignment yet",
            }
        settings = _settings(assignment.settings)
        scoring = str(settings.get("attemptScoring") or "latest")
        attempts = await self._assignment_attempts(assignment.id)
        by_student: dict[str, list[AssessmentAttempt]] = defaultdict(list)
        for attempt in attempts:
            by_student[attempt.student_id].append(attempt)
        chosen: list[AssessmentAttempt] = []
        for rows in by_student.values():
            picked = _pick_attempt(rows, scoring)
            if picked is not None and picked.status == "COMPLETED":
                chosen.append(picked)
        if not chosen:
            await record_tutor_access(
                self.session,
                actor_id=self.tutor_id,
                center_id=self.center_id,
                action="view_item_analysis",
                resource=f"assignment:{assignment.id}",
                meta={"assignmentId": assignment.id, "attemptCount": 0},
            )
            return {
                "assignmentId": assignment.id,
                "items": [],
                "attemptCount": 0,
                "message": "No submitted attempts yet",
            }

        attempt_ids = [row.id for row in chosen]
        result = await self.session.execute(
            select(
                QuestionResponse,
                Question,
                CoverageQuestion,
            )
            .select_from(QuestionResponse)
            .outerjoin(Question, Question.id == QuestionResponse.question_id)
            .outerjoin(
                CoverageQuestion,
                CoverageQuestion.id == QuestionResponse.coverage_question_id,
            )
            .where(QuestionResponse.attempt_id.in_(attempt_ids))
        )
        responses = list(result.all())

        buckets: dict[str, dict[str, Any]] = {}
        for response, question, coverage in responses:
            qid = response.question_id or response.coverage_question_id
            if not qid:
                continue
            bucket = buckets.setdefault(
                qid,
                {
                    "questionId": qid,
                    "questionText": None,
                    "options": None,
                    "correctAnswer": None,
                    "topicId": None,
                    "answered": 0,
                    "correct": 0,
                    "timeSum": 0,
                    "timeCount": 0,
                    "wrongOptions": Counter(),
                },
            )
            source = question or coverage
            if source is not None:
                text = getattr(source, "question_text", None)
                bucket["questionText"] = text
                bucket["options"] = getattr(source, "options", None)
                bucket["correctAnswer"] = getattr(source, "correct_answer", None)
                bucket["topicId"] = getattr(source, "topic_id", None)
            bucket["answered"] += 1
            if response.is_correct:
                bucket["correct"] += 1
            else:
                selected = response.selected_answer
                if selected:
                    bucket["wrongOptions"][selected] += 1
            if response.time_spent_seconds is not None:
                bucket["timeSum"] += response.time_spent_seconds
                bucket["timeCount"] += 1

        items: list[dict[str, Any]] = []
        for bucket in buckets.values():
            answered = bucket["answered"]
            correct = bucket["correct"]
            pct = None if answered == 0 else round(100 * correct / answered, 1)
            wrong = bucket["wrongOptions"]
            most_common_wrong = wrong.most_common(1)[0][0] if wrong else None
            avg_time = (
                round(bucket["timeSum"] / bucket["timeCount"], 1)
                if bucket["timeCount"]
                else None
            )
            items.append(
                {
                    "questionId": bucket["questionId"],
                    "questionText": bucket["questionText"],
                    "topicId": bucket["topicId"],
                    "answered": answered,
                    "correct": correct,
                    "percentCorrect": pct,
                    "mostCommonWrongOption": most_common_wrong,
                    "averageTimeSeconds": avg_time,
                }
            )
        items.sort(
            key=lambda row: (
                row["percentCorrect"] if row["percentCorrect"] is not None else 101.0,
                row["questionId"],
            )
        )

        await record_tutor_access(
            self.session,
            actor_id=self.tutor_id,
            center_id=self.center_id,
            action="view_item_analysis",
            resource=f"assignment:{assignment.id}",
            meta={
                "assignmentId": assignment.id,
                "attemptCount": len(chosen),
                "itemCount": len(items),
            },
        )
        return {
            "assignmentId": assignment.id,
            "attemptCount": len(chosen),
            "items": items,
        }


class StudentScopedDetailService(_ResultsMixin):
    """P-4 / P-6: student history + topic breakdown from assigned tasks only."""

    def __init__(
        self,
        session: AsyncSession,
        tutor_id: str,
        center_id: str,
        student_id: str,
    ) -> None:
        self.session = session
        self.tutor_id = tutor_id
        self.center_id = center_id
        self.student_id = student_id

    async def process(self) -> dict[str, Any]:
        await self._owned_center()
        await self._require_active_member(self.student_id)
        student = await users_repository.by_id(self.session, self.student_id)
        center_assignments = await assignments_repository.for_center(
            self.session, self.center_id
        )
        assignment_ids = [row.id for row in center_assignments]
        history: list[dict[str, Any]] = []
        trend_inputs: list[TrendAttempt] = []
        completed_attempts: list[AssessmentAttempt] = []

        if assignment_ids:
            attempts = await attempts_repository.list_where(
                self.session,
                AssessmentAttempt.assignment_id.in_(assignment_ids),
                AssessmentAttempt.student_id == self.student_id,
                AssessmentAttempt.status == "COMPLETED",
                order_by=(AssessmentAttempt.completed_at.asc(),),
                limit=500,
            )
            completed_attempts = list(attempts)
            by_assignment = {row.id: row for row in center_assignments}
            for attempt in completed_attempts:
                assignment = by_assignment.get(attempt.assignment_id or "")
                if assignment is None:
                    continue
                grade = attempt.grade
                if grade is None and attempt.percentage is not None:
                    grade, _, _ = waec_grade(attempt.percentage)
                history.append(
                    {
                        "assignmentId": assignment.id,
                        "title": assignment.title,
                        "taskType": assignment.task_type,
                        "score": attempt.percentage,
                        "grade": grade,
                        "submittedAt": _iso(attempt.completed_at),
                        "timeSpentSeconds": attempt.time_spent_seconds,
                        "focusLoss": _focus_loss_flag(attempt.away_events),
                        "attemptId": attempt.id,
                    }
                )
                questions = 0
                if attempt.total_marks is not None:
                    questions = int(attempt.total_marks)
                trend_inputs.append(
                    TrendAttempt(
                        percentage=float(attempt.percentage or 0),
                        questions=max(questions, 0),
                    )
                )

        topic_rows = await self._topic_breakdown(completed_attempts)
        trend = trend_compare(trend_inputs)
        if trend.get("status") == "insufficient":
            trend_out = {
                "status": "insufficient",
                "message": THIN_TREND_COPY,
            }
        else:
            trend_out = {
                **trend,
                "message": None,
            }

        task_count = len(history)
        await record_tutor_access(
            self.session,
            actor_id=self.tutor_id,
            center_id=self.center_id,
            action="view_student_detail",
            resource=f"student:{self.student_id}",
            student_id=self.student_id,
            meta={"basedOnTasks": task_count},
        )

        return {
            "student": {
                "id": self.student_id,
                "name": _display_name(student),
                "classLevel": getattr(student, "class_level", None),
                "track": getattr(student, "track", None),
            },
            "framing": {
                "basedOnTasks": task_count,
                "label": (
                    f"Based on {task_count} "
                    f"task{'s' if task_count != 1 else ''} you assigned"
                ),
            },
            "history": list(reversed(history)),
            "trend": trend_out,
            "topicBreakdown": topic_rows,
        }

    async def _topic_breakdown(
        self, attempts: list[AssessmentAttempt]
    ) -> list[dict[str, Any]]:
        if not attempts:
            return []
        attempt_ids = [row.id for row in attempts]
        result = await self.session.execute(
            select(
                QuestionResponse.is_correct,
                Question.topic_id,
                CoverageQuestion.topic_id,
            )
            .select_from(QuestionResponse)
            .outerjoin(Question, Question.id == QuestionResponse.question_id)
            .outerjoin(
                CoverageQuestion,
                CoverageQuestion.id == QuestionResponse.coverage_question_id,
            )
            .where(QuestionResponse.attempt_id.in_(attempt_ids))
        )
        tallies: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for is_correct, q_topic, c_topic in result.all():
            topic_id = q_topic or c_topic
            if not topic_id:
                continue
            tallies[topic_id][1] += 1
            if is_correct:
                tallies[topic_id][0] += 1

        rows: list[dict[str, Any]] = []
        for topic_id, (correct, total) in tallies.items():
            topic = await topics_repository.by_id(self.session, topic_id)
            rows.append(
                _topic_evidence_row(
                    topic_id=topic_id,
                    topic_title=topic.title if topic else None,
                    correct=correct,
                    total=total,
                )
            )
        rows.sort(
            key=lambda row: (
                0 if row["evidenceLevel"] == "ENOUGH" else 1,
                row["accuracy"] if row["accuracy"] is not None else 101.0,
                row["topicTitle"] or "",
            )
        )
        return rows
