"""Dashboard, classroom, and performance reads for the Next pages."""

from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.timeutil import as_utc, lagos_day_key, utcnow
from app.database.models import Lesson, StudentProgress, Topic
from app.database.repositories.assessment import (
    attempts_repository,
    responses_repository,
)
from app.database.repositories.curriculum import (
    lessons_repository,
    subjects_repository,
    topic_edges_repository,
    topics_repository,
)
from app.database.repositories.flashcard import reviews_repository
from app.database.repositories.learning import (
    metrics_repository,
    progress_repository,
    student_achievements_repository,
)
from app.database.repositories.planner import plan_items_repository, plans_repository
from app.database.repositories.question import questions_repository
from app.domain import can, entitlement_denial
from app.services.assessments.grading import coarse_grade
from app.services.auth.rules import current_streak
from app.services.learning.evidence import DECAY_RETENTION, TARGET
from app.services.learning.graph import (
    GraphEdge,
    GraphNode,
    build_graph,
    topic_available,
)
from app.services.learning.mastery import (
    classify_gap,
    graph_colour,
    is_mastered,
    recommend,
    recommendation_score,
    sort_gaps,
)
from app.services.learning.mastery_store import GetTopicMasteryService

DASHBOARD_ATTEMPTS_PAGE_SIZE = 5
# Cards in each dashboard learning-path rail (gaps, revision).
DASHBOARD_RAIL_SIZE = 5
# Cards in the "Keep learning" rail.
DASHBOARD_PICKS = 3
# Matches CONTINUE_REASON in the frontend, which styles that card differently.
CONTINUE_REASON = "Continue where you left off"


class GetDashboardService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        first_name: str | None,
        tier: str,
        class_level: str | None,
        activity_page: int = 1,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.first_name = first_name
        self.tier = tier
        self.class_level = class_level
        self.activity_page = max(activity_page, 1)

    async def process(self) -> dict:
        today = lagos_day_key()
        completed = await attempts_repository.completed_at(
            self.session, self.student_id, limit=400
        )
        days = {lagos_day_key(moment) for moment in completed if moment}
        recent = await _attempts(
            self.session,
            self.student_id,
            DASHBOARD_ATTEMPTS_PAGE_SIZE,
            self.activity_page,
        )
        newest = (
            recent
            if self.activity_page == 1
            else await _attempts(
                self.session, self.student_id, DASHBOARD_ATTEMPTS_PAGE_SIZE
            )
        )
        attempt_total = await attempts_repository.completed_count(
            self.session, self.student_id
        )
        last_week = await attempts_repository.completed_count(
            self.session, self.student_id, since=utcnow() - timedelta(days=7)
        )
        plan = await plans_repository.active(self.session, self.student_id)
        active_ids = await subjects_repository.active_ids(self.session)
        today_items = [
            item
            for item in await self._today_items(plan, today)
            if item["subjectId"] in active_ids
        ]
        # A deactivated subject leaves every rail below, not just the classroom.
        all_topics = [
            topic
            for topic in await topics_repository.all_ordered(self.session)
            if topic.subject_id in active_ids
        ]
        states = await GetTopicMasteryService(
            self.session,
            self.student_id,
            [topic.id for topic in all_topics],
        ).process()
        await _paint(self.session, all_topics, states, self.student_id)
        by_id = {topic.id: topic for topic in all_topics}
        dependents = _unmastered_dependents(all_topics, states)
        # UNTOUCHED is "not started yet", not a weakness: the rail would
        # otherwise fill with the first topics of every subject for a new
        # student. Recommendations surface those instead.
        gaps = [
            _gap_view(gap, by_id, states, dependents)
            for gap in _gaps(all_topics, states)
            if gap["category"] != "UNTOUCHED"
        ][:DASHBOARD_RAIL_SIZE]
        keep = await self._keep_learning(all_topics, states)
        picks = await self._learning_picks(all_topics, states, dependents)
        revision = await self._revision_queue(all_topics, states, dependents)
        answered, correct, topic_count = await responses_repository.answer_totals(
            self.session, self.student_id
        )
        highlights = await self._highlights()
        referenced = {
            item["subjectId"]
            for item in [*gaps, *picks, *revision]
            if item.get("subjectId")
        }
        subjects = {
            subject.id: {
                "slug": subject.slug,
                "name": subject.name,
                "code": subject.code,
            }
            for subject in await subjects_repository.active_ordered(self.session)
            if subject.id in referenced
        }
        return {
            "firstName": self.first_name,
            "streak": current_streak(days, today),
            "tier": self.tier,
            "keepLearning": keep,
            "learningPicks": picks,
            "gaps": gaps,
            "revision": revision[:DASHBOARD_RAIL_SIZE],
            "revisionTotal": len(revision),
            "subjects": subjects,
            "todayItems": today_items,
            "hasStudyPlan": plan is not None,
            "hasActivity": attempt_total > 0
            or answered > 0
            or any(state.observations > 0 for state in states.values()),
            "recentAttempts": recent,
            "attemptTotal": attempt_total,
            "bestScore": max((row["percentage"] for row in newest), default=None),
            "lastWeekActivity": last_week,
            "totalResponses": answered,
            "correctResponses": correct,
            "accuracy": round(correct / answered * 100) if answered else None,
            "topicCount": topic_count,
            "achievements": highlights,
        }

    async def _learning_picks(
        self,
        topic_rows: Sequence[Topic],
        states: dict,
        dependents: dict[str, int],
    ) -> list[dict]:
        """The "Keep learning" rail: an unfinished lesson first, then ranked picks."""
        by_id = {topic.id: topic for topic in topic_rows}
        picks: list[dict] = []
        progress_row = await progress_repository.latest_in_progress(
            self.session, self.student_id
        )
        if progress_row is not None and progress_row.topic_id in states:
            topic = by_id.get(progress_row.topic_id)
            if topic is not None:
                picks.append(
                    _pick_view(
                        topic,
                        states[topic.id],
                        CONTINUE_REASON,
                        score=1.0,
                        unlocks=dependents.get(topic.id, 0),
                        lesson_id=progress_row.lesson_id,
                    )
                )
        leverage = _leverage(topic_rows)
        now = utcnow()
        taken = {pick["topicId"] for pick in picks}
        ranked = recommend(
            [state for state in states.values() if state.topic_id not in taken],
            leverage,
            now,
            k=DASHBOARD_PICKS - len(picks),
        )
        for state in ranked:
            topic = by_id.get(state.topic_id)
            if topic is None:
                continue
            unlocks = dependents.get(topic.id, 0)
            picks.append(
                _pick_view(
                    topic,
                    state,
                    _pick_reason(topic, state, leverage.get(topic.id, 0.0), unlocks),
                    score=recommendation_score(
                        state, leverage.get(topic.id, 0.0), state.available, now
                    ),
                    unlocks=unlocks,
                )
            )
        return picks

    async def _revision_queue(
        self,
        topic_rows: Sequence[Topic],
        states: dict,
        dependents: dict[str, int],
    ) -> list[dict]:
        """Merged revision queue: faded retention, cadence due, or SRS cards due."""
        now = utcnow()
        cadence = await progress_repository.revision_due_by_topic(
            self.session, self.student_id
        )
        due_cards = await reviews_repository.due_counts_by_topic(
            self.session, self.student_id, now
        )
        max_blocked = max(dependents.values(), default=0)
        items = []
        for topic in topic_rows:
            state = states.get(topic.id)
            if state is None or state.last_effort_at is None:
                continue
            faded = state.retention is not None and state.retention < DECAY_RETENTION
            cadence_at = cadence.get(topic.id)
            cadence_due = cadence_at is not None and as_utc(cadence_at) <= now
            cards = due_cards.get(topic.id, 0)
            if not (faded or cadence_due or cards > 0):
                continue
            blocked = dependents.get(topic.id, 0)
            retention_value = (
                DECAY_RETENTION if state.retention is None else state.retention
            )
            decay = max(0.0, DECAY_RETENTION - retention_value)
            blocked_factor = 1 + (blocked / max_blocked if max_blocked else 0)
            if faded:
                reason = f"Retention {round(state.retention * 100)}% — re-cement it"
            elif cards > 0:
                reason = f"{cards} card{'' if cards == 1 else 's'} due for review"
            else:
                reason = "Scheduled revision is due"
            items.append(
                {
                    **_evidence(state),
                    "topicId": topic.id,
                    "subjectId": topic.subject_id,
                    "title": topic.title,
                    "slug": topic.slug,
                    "mastery": state.mastery,
                    "retention": state.retention,
                    "priority": decay * _exam_weight(topic) * blocked_factor,
                    "reason": reason,
                    "blockedCount": blocked,
                    "dueSrsCards": cards,
                    "cadenceDue": cadence_due,
                }
            )
        items.sort(
            key=lambda item: (
                -item["priority"],
                -item["dueSrsCards"],
                -(1 if item["retention"] is None else item["retention"]),
            )
        )
        return items

    async def _today_items(self, plan, today: str) -> list[dict]:
        if plan is None:
            return []
        items = await plan_items_repository.for_plan(self.session, plan.id)
        return [
            {
                "id": item.id,
                "subjectId": item.subject_id,
                "topicId": item.topic_id,
                "activityType": item.activity_type,
                "durationMinutes": item.duration_minutes,
                "status": item.status,
            }
            for item in items
            if str(item.date)[:10] == today
        ]

    async def _keep_learning(
        self, topic_rows: Sequence[Topic], states: dict
    ) -> dict | None:
        progress_row = await progress_repository.latest_in_progress(
            self.session, self.student_id
        )
        by_id = {topic.id: topic for topic in topic_rows}
        if progress_row is not None and progress_row.topic_id in by_id:
            topic = by_id[progress_row.topic_id]
            subject = await subjects_repository.by_id(self.session, topic.subject_id)
            return {
                "kind": "lesson",
                "subjectSlug": subject.slug if subject else None,
                "topicSlug": topic.slug,
                "topicTitle": topic.title,
                "lessonId": progress_row.lesson_id,
            }
        leverage = _leverage(topic_rows)
        picked = recommend(list(states.values()), leverage, utcnow(), k=1)
        if not picked:
            return None
        topic = by_id.get(picked[0].topic_id)
        if topic is None:
            return None
        subject = await subjects_repository.by_id(self.session, topic.subject_id)
        return {
            "kind": "topic",
            "subjectSlug": subject.slug if subject else None,
            "topicSlug": topic.slug,
            "topicTitle": topic.title,
            "lessonId": None,
        }

    async def _highlights(self) -> dict:
        earned = await student_achievements_repository.recent_with_titles(
            self.session, self.student_id, limit=3
        )
        total = await student_achievements_repository.count_for_student(
            self.session, self.student_id
        )
        return {
            "earned": total or 0,
            "highlights": [
                {
                    "title": item.title,
                    "earnedAt": row.earned_at.isoformat() if row.earned_at else None,
                }
                for row, item in earned
            ],
        }


class GetPerformanceService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        tier: str,
        page: int,
        subject_id: str | None,
        track: str | None = None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.tier = tier
        self.page = page
        self.subject_id = subject_id
        self.track = track

    async def process(self) -> dict:
        advanced = can(self.tier, "advancedAnalytics")
        if self.subject_id and not advanced:
            denial = entitlement_denial("advancedAnalytics")
            raise ApiError(
                403,
                str(denial["error"]),
                requiredTier=denial["requiredTier"],
                feature="advancedAnalytics",
            )
        attempt_rows = await _attempts(self.session, self.student_id, 10, self.page)
        attempt_total = await attempts_repository.completed_count(
            self.session, self.student_id
        )
        # Only subjects the student has answered questions in, and only those on
        # their track (CORE is shared by every track). No track means no filter.
        totals = await responses_repository.totals_by_subject(
            self.session, self.student_id
        )
        subject_rows = [
            subject
            for subject in await subjects_repository.ordered(self.session)
            if totals.get(subject.id, (0, 0))[0] > 0
            and (not self.track or subject.track_category in {"CORE", self.track})
        ]
        all_topics = await topics_repository.all_ordered(self.session)
        states = await GetTopicMasteryService(
            self.session,
            self.student_id,
            [topic.id for topic in all_topics],
        ).process()
        by_subject: dict[str, list] = {}
        for topic in all_topics:
            by_subject.setdefault(topic.subject_id, []).append(topic)
        summary = []
        for subject in subject_rows:
            owned = by_subject.get(subject.id, [])
            scores = [states[topic.id].mastery for topic in owned if topic.id in states]
            average = round(sum(scores) / len(scores)) if scores else 0
            attempted, correct = totals[subject.id]
            summary.append(
                {
                    "subjectId": subject.id,
                    "id": subject.id,
                    "name": subject.name,
                    "slug": subject.slug,
                    "code": subject.code,
                    "mastery": average,
                    "letter": coarse_grade(average),
                    "totalAttempted": attempted,
                    "totalCorrect": correct,
                    "accuracy": round(correct / attempted * 100),
                }
            )
        payload: dict = {
            "attempts": attempt_rows,
            "attemptTotal": attempt_total,
            "subjects": summary,
            "advanced": advanced,
        }
        if self.subject_id:
            payload["subject"] = await self._subject_view(all_topics, states)
        return payload

    async def _subject_view(self, topic_rows: Sequence[Topic], states: dict) -> dict:
        subject_id = self.subject_id
        if subject_id is None:
            raise ApiError(404, "Subject not found")
        subject = await subjects_repository.by_id(self.session, subject_id)
        if subject is None:
            raise ApiError(404, "Subject not found")
        owned = [topic for topic in topic_rows if topic.subject_id == subject_id]
        await _paint(self.session, owned, states, self.student_id)
        answers = await responses_repository.timing_for_subject(
            self.session, self.student_id, subject_id
        )
        profile = self._profile(answers)
        gaps = _gaps(owned, states)
        insights = self._insights(owned, states, gaps)
        return {
            "id": subject.id,
            "name": subject.name,
            "slug": subject.slug,
            "profile": profile,
            "topics": [
                {
                    "id": topic.id,
                    "title": topic.title,
                    "mastery": states[topic.id].mastery,
                    "level": states[topic.id].level,
                    "colour": graph_colour(states[topic.id]),
                }
                for topic in owned
                if topic.id in states
            ],
            "gaps": gaps,
            "insights": insights[:3],
        }

    def _profile(self, answers: list) -> dict | None:
        if len(answers) < 20:
            return None
        rapid = sum(1 for spent, _estimate in answers if (spent or 0) < 3)
        ratios = [(spent or 0) / estimate for spent, estimate in answers if estimate]
        median = sorted(ratios)[len(ratios) // 2] if ratios else 1
        if median < 0.6:
            pacing = "RUSHED"
        elif median > 1.3:
            pacing = "SLOW"
        else:
            pacing = "STEADY"
        return {
            "rapidGuessRate": round(100 * rapid / len(answers)),
            "pacing": pacing,
        }

    def _insights(
        self, owned: list[Topic], states: dict, gaps: list[dict]
    ) -> list[dict]:
        insights = []
        win_used = False
        for gap in gaps:
            if (
                gap["category"] in {"WEAK", "BOTTLENECK", "DECAYED"}
                and len(insights) < 3
            ):
                insights.append(
                    {
                        "kind": gap["category"],
                        "topicId": gap["topicId"],
                        "title": gap["title"],
                    }
                )
        for topic in owned:
            state = states.get(topic.id)
            if state and is_mastered(state) and not win_used and len(insights) < 3:
                insights.append(
                    {
                        "kind": "WIN",
                        "topicId": topic.id,
                        "title": topic.title,
                    }
                )
                win_used = True
        return insights


class GetClassroomSubjectsService:
    def __init__(
        self, session: AsyncSession, student_id: str, track: str | None
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.track = track

    async def process(self) -> dict:
        subject_rows = await subjects_repository.active_ordered(self.session)
        if self.track:
            subject_rows = [
                subject
                for subject in subject_rows
                if subject.track_category in {"CORE", self.track}
            ]
        all_topics = await topics_repository.all_ordered(self.session)
        states = await GetTopicMasteryService(
            self.session,
            self.student_id,
            [topic.id for topic in all_topics],
        ).process()
        await _paint(self.session, all_topics, states, self.student_id)
        rows = []
        for subject in subject_rows:
            owned = [topic for topic in all_topics if topic.subject_id == subject.id]
            colours = [
                graph_colour(states[topic.id]) for topic in owned if topic.id in states
            ]
            rows.append(
                {
                    "id": subject.id,
                    "name": subject.name,
                    "slug": subject.slug,
                    "topicCount": len(owned),
                    "mastered": colours.count("MASTERED"),
                    "started": colours.count("STARTED"),
                }
            )
        return {"subjects": rows}


class GetSubjectPageService:
    def __init__(self, session: AsyncSession, student_id: str, slug: str) -> None:
        self.session = session
        self.student_id = student_id
        self.slug = slug

    async def process(self) -> dict:
        subject = await subjects_repository.active_by_slug(self.session, self.slug)
        if subject is None:
            raise ApiError(404, "Subject not found")
        topic_rows = await topics_repository.for_subject(self.session, subject.id)
        states = await GetTopicMasteryService(
            self.session,
            self.student_id,
            [topic.id for topic in topic_rows],
        ).process()
        await _paint(self.session, topic_rows, states, self.student_id)
        canonical = await self._canonical(topic_rows)
        return {
            "subject": {
                "id": subject.id,
                "name": subject.name,
                "slug": subject.slug,
            },
            "topics": [
                {
                    "id": topic.id,
                    "title": topic.title,
                    "slug": topic.slug,
                    "orderIndex": topic.order_index,
                    "colour": graph_colour(states[topic.id]),
                    "mastery": states[topic.id].mastery,
                    "level": states[topic.id].level,
                    "canonicalLessonId": canonical.get(topic.id),
                }
                for topic in topic_rows
            ],
        }

    async def _canonical(self, topic_rows: Sequence[Topic]) -> dict[str, str]:
        if not topic_rows:
            return {}
        return await lessons_repository.canonical_ids_for_topics(
            self.session, [topic.id for topic in topic_rows]
        )


class GetTopicPageService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        slug: str,
        topic_slug: str,
        view: str,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.slug = slug
        self.topic_slug = topic_slug
        self.view = view

    async def process(self) -> dict:
        subject = await subjects_repository.active_by_slug(self.session, self.slug)
        if subject is None:
            raise ApiError(404, "Subject not found")
        topic = await topics_repository.by_slug(
            self.session, subject.id, self.topic_slug
        )
        if topic is None:
            raise ApiError(404, "Topic not found")
        topic_rows = await topics_repository.for_subject(self.session, subject.id)
        states = await GetTopicMasteryService(
            self.session,
            self.student_id,
            [item.id for item in topic_rows],
        ).process()
        await _paint(self.session, topic_rows, states, self.student_id)
        state = states[topic.id]
        lesson = await lessons_repository.latest_for_topic(self.session, topic.id)
        progress_row = None
        if lesson is not None:
            progress_row = await progress_repository.for_lesson(
                self.session, self.student_id, lesson.id
            )
        passed = await metrics_repository.pretest_passed(
            self.session, self.student_id, topic.id
        )
        attempted_count = await questions_repository.count_attempted_for_topic(
            self.session, self.student_id, topic.id
        )
        base = {
            "subject": {
                "id": subject.id,
                "name": subject.name,
                "slug": subject.slug,
            },
            "topic": {
                "id": topic.id,
                "title": topic.title,
                "slug": topic.slug,
            },
            "colour": graph_colour(state),
            "mastery": state.mastery,
            "available": state.available,
            "alreadyPassed": passed is not None,
            "attemptedCount": attempted_count,
            "level": state.level,
            "retention": state.retention,
            "confidence": state.confidence,
            "accObservations": state.acc_observations,
            "lessonObservations": state.lesson_observations,
            "srsObservations": state.srs_observations,
        }
        if self.view == "overview":
            base["canonicalLessonId"] = lesson.id if lesson else None
            return base
        if self.view in {"quiz", "practice"}:
            base["createsAttempt"] = False
            return base
        unlocked = state.available and _lesson_unlocked(lesson, progress_row)
        base["lesson"] = (
            None
            if lesson is None
            else {
                "id": lesson.id,
                "title": lesson.title,
                "blocks": lesson.blocks or [],
                "checkpoint": progress_row.checkpoint_data if progress_row else None,
                "status": progress_row.status if progress_row else "NOT_STARTED",
                "unlocked": unlocked,
            }
        )
        return base


class GetPracticeResultService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        subject_slug: str,
        topic_slug: str,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.subject_slug = subject_slug
        self.topic_slug = topic_slug

    async def process(self) -> dict:
        subject = await subjects_repository.active_by_slug(
            self.session, self.subject_slug
        )
        topic = None
        if subject is not None:
            topic = await topics_repository.by_slug(
                self.session, subject.id, self.topic_slug
            )
        if subject is None or topic is None:
            raise ApiError(404, "Topic not found")
        attempt = await attempts_repository.latest_completed_topic_quiz(
            self.session, self.student_id, subject.id
        )
        if attempt is None:
            return {"result": None}
        return {
            "result": {
                "attemptId": attempt.id,
                "percentage": attempt.percentage,
                "grade": attempt.grade,
                "completedAt": attempt.completed_at.isoformat()
                if attempt.completed_at
                else None,
            }
        }


async def _attempts(
    session: AsyncSession, student_id: str, size: int, page: int = 1
) -> list[dict]:
    rows = await attempts_repository.completed_with_assessment(
        session,
        student_id,
        limit=size,
        offset=(page - 1) * size,
    )
    # Multi-subject papers (mock exams, JAMB CBT) carry no subject of their own.
    names = {
        subject.id: subject.name
        for subject in await subjects_repository.ordered(session)
    }
    payload = []
    for attempt, assessment in rows:
        percentage = attempt.percentage or 0
        payload.append(
            {
                "id": attempt.id,
                "attemptId": attempt.id,
                "title": assessment.title,
                "subjectId": assessment.subject_id,
                "subjectName": names.get(assessment.subject_id)
                if assessment.subject_id
                else None,
                "assessmentType": assessment.assessment_type,
                "score": attempt.score,
                "totalMarks": attempt.total_marks or assessment.total_marks,
                "percentage": percentage,
                "letter": coarse_grade(percentage),
                "completedAt": attempt.completed_at.isoformat()
                if attempt.completed_at
                else None,
            }
        )
    return payload


async def _paint(
    session: AsyncSession,
    topic_rows: Sequence[Topic],
    states: dict,
    student_id: str,
) -> None:
    if not topic_rows:
        return
    ids = [topic.id for topic in topic_rows]
    edges = await topic_edges_repository.for_topics(session, ids)
    passed_rows = await metrics_repository.passed_topic_ids(session, student_id, ids)
    graph = build_graph(
        [
            GraphNode(
                id=topic.id,
                subject_id=topic.subject_id,
                title=topic.title,
                slug=topic.slug,
                order_index=topic.order_index,
                prerequisite_topic_id=topic.prerequisite_topic_id,
            )
            for topic in topic_rows
        ],
        [
            GraphEdge(
                id=edge.id,
                source=edge.prereq_topic_id,
                target=edge.topic_id,
                kind=edge.kind,
                strength=edge.strength,
            )
            for edge in edges
        ],
    )
    passed = set(passed_rows)

    def mastery_of(topic_id: str) -> int:
        state = states.get(topic_id)
        return 0 if state is None else state.mastery

    for topic in topic_rows:
        state = states.get(topic.id)
        if state is None:
            continue
        state.available = topic_available(graph, topic.id, mastery_of, passed)


def _gaps(topic_rows: Sequence[Topic], states: dict) -> list[dict]:
    dependents: dict[str, int] = {}
    for topic in topic_rows:
        if topic.prerequisite_topic_id:
            state = states.get(topic.id)
            if state is None or not is_mastered(state):
                dependents[topic.prerequisite_topic_id] = (
                    dependents.get(topic.prerequisite_topic_id, 0) + 1
                )
    items = []
    for topic in topic_rows:
        state = states.get(topic.id)
        if state is None:
            continue
        category = classify_gap(state, dependents.get(topic.id, 0))
        if category is None:
            continue
        items.append(
            {
                "topicId": topic.id,
                "title": topic.title,
                "category": category,
                "mastery": state.mastery,
                "bottleneckScore": dependents.get(topic.id, 0),
            }
        )
    return sort_gaps(items)


def _leverage(topic_rows: Sequence[Topic]) -> dict[str, float]:
    counts: dict[str, int] = {}
    for topic in topic_rows:
        if topic.prerequisite_topic_id:
            counts[topic.prerequisite_topic_id] = (
                counts.get(topic.prerequisite_topic_id, 0) + 1
            )
    peak = max(counts.values(), default=1) or 1
    return {topic_id: count / peak for topic_id, count in counts.items()}


def _unmastered_dependents(topic_rows: Sequence[Topic], states: dict) -> dict[str, int]:
    """Direct dependents still below TARGET, per prerequisite topic."""
    counts: dict[str, int] = {}
    for topic in topic_rows:
        if not topic.prerequisite_topic_id:
            continue
        state = states.get(topic.id)
        if state is None or not is_mastered(state):
            counts[topic.prerequisite_topic_id] = (
                counts.get(topic.prerequisite_topic_id, 0) + 1
            )
    return counts


def _exam_weight(topic: Topic) -> float:
    return (topic.waec_weight or 0) + (topic.jamb_weight or 0)


def _evidence(state) -> dict:
    """What the frontend needs to decide between a mastery % and an evidence label."""
    return {
        "confidence": state.confidence,
        "accObservations": state.acc_observations,
        "lessonObservations": state.lesson_observations,
        "srsObservations": state.srs_observations,
        "lastStudy": state.last_effort_at.isoformat() if state.last_effort_at else None,
    }


def _gap_view(
    gap: dict, by_id: dict[str, Topic], states: dict, dependents: dict[str, int]
) -> dict:
    topic = by_id[gap["topicId"]]
    state = states[topic.id]
    return {
        **gap,
        **_evidence(state),
        "subjectId": topic.subject_id,
        "slug": topic.slug,
        "retention": state.retention,
        "blockedCount": dependents.get(topic.id, 0),
        "abandonedCount": state.abandon_count,
    }


def _pick_reason(topic: Topic, state, leverage: float, unlocks: int) -> str:
    urgency = max(0.0, (TARGET - state.mastery) / TARGET)
    decay = 0.0 if state.retention is None else max(0.0, 1 - state.retention)
    if leverage >= 0.25 and unlocks >= 1:
        return f"Unlocks {unlocks} topic{'' if unlocks == 1 else 's'}"
    if urgency >= 0.4:
        weight = round(_exam_weight(topic))
        if weight > 0:
            return f"High-yield — {weight} exam weight, {round(state.mastery)}% mastery"
        return "Next in your learning path"
    if decay >= 0.1:
        return "Fading — revise while fresh"
    return "Next in your learning path"


def _pick_view(
    topic: Topic,
    state,
    reason: str,
    *,
    score: float,
    unlocks: int,
    lesson_id: str | None = None,
) -> dict:
    return {
        **_evidence(state),
        "topicId": topic.id,
        "subjectId": topic.subject_id,
        "title": topic.title,
        "slug": topic.slug,
        "mastery": state.mastery,
        "score": score,
        "reason": reason,
        "unlocks": unlocks,
        "lessonId": lesson_id,
    }


def _lesson_unlocked(
    lesson: Lesson | None, progress_row: StudentProgress | None
) -> bool:
    if lesson is None:
        return False
    if progress_row is not None and progress_row.status == "COMPLETED":
        return True
    return True
