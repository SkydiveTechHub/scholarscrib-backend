"""Dashboard, classroom, and performance reads for the Next pages."""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.timeutil import lagos_day_key, utcnow
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
from app.services.learning.evidence import TARGET
from app.services.learning.graph import (
    GraphEdge,
    GraphNode,
    build_graph,
    topic_available,
)
from app.services.learning.mastery import (
    classify_gap,
    graph_colour,
    recommend,
    sort_gaps,
)
from app.services.learning.mastery_store import GetTopicMasteryService


class GetDashboardService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        first_name: str | None,
        tier: str,
        class_level: str | None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.first_name = first_name
        self.tier = tier
        self.class_level = class_level

    async def process(self) -> dict:
        today = lagos_day_key()
        completed = await attempts_repository.completed_at(
            self.session, self.student_id, limit=400
        )
        days = {lagos_day_key(moment) for moment in completed if moment}
        recent = await _attempts(self.session, self.student_id, 5)
        plan = await plans_repository.active(self.session, self.student_id)
        today_items = await self._today_items(plan, today)
        all_topics = await topics_repository.all_ordered(self.session)
        states = await GetTopicMasteryService(
            self.session,
            self.student_id,
            [topic.id for topic in all_topics],
        ).process()
        await _paint(self.session, all_topics, states, self.student_id)
        gaps = _gaps(all_topics, states)
        keep = await self._keep_learning(all_topics, states)
        highlights = await self._highlights()
        return {
            "firstName": self.first_name,
            "streak": current_streak(days, today),
            "tier": self.tier,
            "keepLearning": keep,
            "gaps": gaps[:5],
            "todayItems": today_items,
            "recentAttempts": recent,
            "achievements": highlights,
        }

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
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.tier = tier
        self.page = page
        self.subject_id = subject_id

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
        subject_rows = await subjects_repository.ordered(self.session)
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
            summary.append(
                {
                    "subjectId": subject.id,
                    "name": subject.name,
                    "slug": subject.slug,
                    "mastery": average,
                    "letter": coarse_grade(average),
                }
            )
        payload: dict = {
            "attempts": attempt_rows,
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
            if state and state.mastery >= TARGET and not win_used and len(insights) < 3:
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
        subject_rows = await subjects_repository.ordered(self.session)
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
        subject = await subjects_repository.by_slug(self.session, self.slug)
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
        subject = await subjects_repository.by_slug(self.session, self.slug)
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
        question_count = await questions_repository.count_objective_for_topic(
            self.session, topic.id
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
            "questionCount": question_count or 0,
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
        subject = await subjects_repository.by_slug(self.session, self.subject_slug)
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
    payload = []
    for attempt, assessment in rows:
        percentage = attempt.percentage or 0
        payload.append(
            {
                "attemptId": attempt.id,
                "title": assessment.title,
                "subjectId": assessment.subject_id,
                "assessmentType": assessment.assessment_type,
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
            if state is None or state.mastery < TARGET:
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


def _lesson_unlocked(
    lesson: Lesson | None, progress_row: StudentProgress | None
) -> bool:
    if lesson is None:
        return False
    if progress_row is not None and progress_row.status == "COMPLETED":
        return True
    return True
