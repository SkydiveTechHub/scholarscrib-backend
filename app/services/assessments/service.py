from datetime import timedelta

from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AnswerIn,
    GenerateQuizIn,
    PretestIn,
    ScopedMockIn,
    SubmitIn,
)
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.timeutil import as_utc, utcnow
from app.database.models import (
    Assessment,
    AssessmentAttempt,
    AssessmentQuestion,
    CurriculumLevel,
    LearningEvent,
    PerformanceMetric,
    Question,
    QuestionResponse,
    Subject,
)
from app.database.repositories.assessment import (
    assessment_questions_repository,
    assessments_repository,
    attempts_repository,
    responses_repository,
)
from app.database.repositories.curriculum import (
    curriculum_levels_repository,
    subjects_repository,
    topics_repository,
)
from app.database.repositories.learning import (
    learning_events_repository,
    metrics_repository,
)
from app.database.repositories.question import questions_repository
from app.domain import (
    JAMB_DURATION_MINUTES,
    JAMB_ENGLISH_CODE,
    JAMB_ENGLISH_QUESTIONS,
    JAMB_SUBJECT_QUESTIONS,
    JAMB_TOTAL_MARKS,
    SEEN_QUESTION_DAYS,
    SUBMIT_GRACE_SECONDS,
    UNTIMED_STALE_HOURS,
)
from app.services.assessments.grading import (
    round_percentage,
    score_jamb_paper,
    topic_breakdown_status,
    waec_grade,
)
from app.services.catalogue import deadline_at, public_question, time_limit_minutes


def _is_stale(attempt: AssessmentAttempt, assessment: Assessment, now) -> bool:
    started = as_utc(attempt.started_at)
    if assessment.time_limit_minutes:
        deadline = started + timedelta(minutes=assessment.time_limit_minutes)
        return now > deadline + timedelta(seconds=SUBMIT_GRACE_SECONDS)
    return now > started + timedelta(hours=UNTIMED_STALE_HOURS)


async def _subject(
    session: AsyncSession, subject_id: str | None, subject_slug: str | None
) -> Subject | None:
    if subject_id:
        return await subjects_repository.by_id(session, subject_id)
    if subject_slug:
        return await subjects_repository.by_slug(session, subject_slug)
    return None


async def _seen_ids(session: AsyncSession, student_id: str) -> set[str]:
    since = utcnow() - timedelta(days=SEEN_QUESTION_DAYS)
    return await responses_repository.seen_question_ids(session, student_id, since)


async def _pick(
    session: AsyncSession,
    *,
    subject_id: str,
    count: int,
    topic_ids: list[str] | None = None,
    exam_type: str | None = None,
    exam_year: int | None = None,
    difficulty: str | None = None,
    exclude: set[str] | None = None,
    class_levels: list[tuple[str, str]] | None = None,
) -> list[Question]:
    return await questions_repository.pick_objective(
        session,
        subject_id=subject_id,
        count=count,
        topic_ids=topic_ids,
        exam_type=exam_type,
        exam_year=exam_year,
        difficulty=difficulty,
        exclude=exclude,
        class_levels=class_levels,
    )


async def _pick_prefer_unseen(
    session: AsyncSession, student_id: str, count: int, **filters
) -> list[Question]:
    seen = await _seen_ids(session, student_id)
    unseen = await _pick(session, count=count, exclude=seen, **filters)
    if len(unseen) >= count:
        return unseen[:count]
    shortfall = count - len(unseen)
    chosen = {question.id for question in unseen}
    filler = await _pick(session, count=shortfall + len(chosen), **filters)
    for question in filler:
        if question.id not in chosen:
            unseen.append(question)
            chosen.add(question.id)
        if len(unseen) >= count:
            break
    return unseen


async def reap_stale(session: AsyncSession, student_id: str) -> None:
    now = utcnow()
    rows = await attempts_repository.in_progress(session, student_id, limit=100)
    for attempt in rows:
        assessment = await assessments_repository.by_id(session, attempt.assessment_id)
        if assessment is None or not _is_stale(attempt, assessment, now):
            continue
        result = await attempts_repository.update_where(
            session,
            {"status": "TIMED_OUT"},
            AssessmentAttempt.id == attempt.id,
            AssessmentAttempt.status == "IN_PROGRESS",
        )
        if not isinstance(result, CursorResult) or result.rowcount == 0:
            continue
        topic_ids = await questions_repository.topic_ids_for_assessment(
            session, assessment.id
        )
        for topic_id in set(topic_ids):
            topic = await topics_repository.by_id(session, topic_id)
            await learning_events_repository.add(
                session,
                LearningEvent(
                    student_id=student_id,
                    subject_id=(topic.subject_id if topic else assessment.subject_id),
                    topic_id=topic_id,
                    kind="QUIZ_ABANDONED",
                    occurred_at=attempt.started_at,
                ),
            )


async def _paper_questions(session: AsyncSession, assessment_id: str) -> list[Question]:
    return await questions_repository.for_assessment(session, assessment_id)


def _payload(
    assessment: Assessment,
    attempt: AssessmentAttempt,
    paper: list[Question],
    resumed: bool = False,
) -> dict:
    started = as_utc(attempt.started_at)
    limit = assessment.time_limit_minutes
    body = {
        "assessmentId": assessment.id,
        "attemptId": attempt.id,
        "title": assessment.title,
        "source": "resumed" if resumed else "generated",
        "totalQuestions": len(paper),
        "timeLimitMinutes": limit,
        "questions": [
            public_question(question, include_answers=False) for question in paper
        ],
    }
    if resumed:
        body["resumed"] = True
    deadline = deadline_at(started, limit)
    if deadline:
        body["deadlineAt"] = deadline.isoformat()
    return body


async def _persist(
    session: AsyncSession,
    *,
    student_id: str,
    subject: Subject,
    questions: list[Question],
    assessment_type: str,
    exam_type: str | None,
    exam_year: int | None,
    title: str,
    untimed: bool,
    total_marks: int | None = None,
    pass_mark: int = 50,
    time_override: int | None = None,
) -> dict:
    limit = (
        time_override
        if time_override is not None
        else time_limit_minutes(len(questions), untimed, exam_type)
    )
    assessment = Assessment(
        id=cuid(),
        title=title,
        subject_id=subject.id,
        assessment_type=assessment_type,
        exam_type=exam_type,
        exam_year=exam_year,
        total_marks=total_marks if total_marks is not None else len(questions),
        time_limit_minutes=limit,
        pass_mark_percent=pass_mark,
    )
    attempt = AssessmentAttempt(
        id=cuid(),
        student_id=student_id,
        assessment_id=assessment.id,
        status="IN_PROGRESS",
        started_at=utcnow(),
    )
    await assessments_repository.add(session, assessment)
    await attempts_repository.add(session, attempt)
    for index, question in enumerate(questions):
        await assessment_questions_repository.add(
            session,
            AssessmentQuestion(
                id=cuid(),
                assessment_id=assessment.id,
                question_id=question.id,
                order_index=index,
            ),
        )
    await session.flush()
    return _payload(assessment, attempt, questions)


async def _resume(
    session: AsyncSession,
    student_id: str,
    subject_id: str,
    assessment_type: str,
    exam_type: str | None,
    exam_year: int | None,
    count: int,
    topic_ids: list[str] | None,
) -> dict | None:
    rows = await attempts_repository.resumable(
        session,
        student_id=student_id,
        subject_id=subject_id,
        assessment_type=assessment_type,
        exam_type=exam_type,
        exam_year=exam_year,
        total_marks=count,
        limit=5,
    )
    for attempt in rows:
        assessment = await assessments_repository.by_id(session, attempt.assessment_id)
        if assessment is None or _is_stale(attempt, assessment, utcnow()):
            continue
        paper = await _paper_questions(session, assessment.id)
        if topic_ids:
            paper_topics = {question.topic_id for question in paper}
            if not paper_topics.issubset(set(topic_ids)):
                continue
        return _payload(assessment, attempt, paper, resumed=True)
    return None


async def _jamb_sections(
    session: AsyncSession, paper: list[Question], answers: dict[str, AnswerIn]
) -> list[tuple[str, str, int, int]]:
    grouped: dict[str, list[Question]] = {}
    for question in paper:
        grouped.setdefault(question.subject_id, []).append(question)
    sections = []
    for subject_id, group in grouped.items():
        subject = await subjects_repository.by_id(session, subject_id)
        correct = 0
        for question in group:
            answer = answers.get(question.id)
            selected = answer.selectedAnswer if answer else None
            if selected is not None and str(selected) == str(question.correct_answer):
                correct += 1
        sections.append(
            (
                subject_id,
                subject.name if subject else subject_id,
                correct,
                len(group),
            )
        )
    return sections


async def select_jamb_subjects(
    session: AsyncSession, subject_ids: list[str]
) -> tuple[Subject, list[Subject]]:
    if len(subject_ids) != 3 or len(set(subject_ids)) != 3:
        raise ApiError(400, "Choose exactly 3 subjects besides English")
    english = await subjects_repository.by_code(session, JAMB_ENGLISH_CODE)
    if english is None:
        raise ApiError(500, "English is missing from the question bank")
    if english.id in subject_ids:
        raise ApiError(400, "English is already included. Choose 3 other subjects")
    chosen = []
    for subject_id in subject_ids:
        subject = await subjects_repository.by_id(session, subject_id)
        if subject is None or not subject.is_jamb:
            raise ApiError(400, "One or more subjects are not available for JAMB")
        chosen.append(subject)
    return english, chosen


class GenerateQuizService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        body: GenerateQuizIn,
        schedule_provider=None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.body = body
        self.schedule_provider = schedule_provider

    async def process(self) -> dict:
        subject = await self._load_subject()
        topic_ids = await self._resolve_topic_ids(subject)
        exam_type = self.body.examType
        exam_year = self.body.examYear
        count = self.body.count or 10
        scheduled = await self._maybe_schedule(subject, exam_type, exam_year)
        assessment_type = "PAST_PAPER" if exam_type else "TOPIC_QUIZ"
        await reap_stale(self.session, self.student_id)
        resumed = await _resume(
            self.session,
            self.student_id,
            subject.id,
            assessment_type,
            exam_type,
            exam_year,
            count,
            topic_ids or None,
        )
        if resumed:
            return resumed
        picked = await self._select_questions(
            subject, topic_ids, exam_type, exam_year, count
        )
        if not picked:
            if scheduled:
                raise ApiError(
                    503,
                    "Questions for this paper are being prepared",
                    preparing=True,
                )
            raise ApiError(404, "No questions found")
        title = self.body.title or f"{subject.name} practice"
        return await _persist(
            self.session,
            student_id=self.student_id,
            subject=subject,
            questions=picked,
            assessment_type=assessment_type,
            exam_type=exam_type,
            exam_year=exam_year,
            title=title,
            untimed=self.body.untimed,
        )

    async def _load_subject(self) -> Subject:
        subject = await _subject(
            self.session, self.body.subjectId, self.body.subjectSlug
        )
        if subject is None:
            raise ApiError(404, "Subject not found")
        return subject

    async def _resolve_topic_ids(self, subject: Subject) -> list[str]:
        topic_ids = list(self.body.topicIds or [])
        if self.body.topicSlug:
            topic = await topics_repository.by_slug(
                self.session, subject.id, self.body.topicSlug
            )
            if topic is None:
                raise ApiError(404, "Topic not found")
            topic_ids.append(topic.id)
        return topic_ids

    async def _maybe_schedule(
        self, subject: Subject, exam_type: str | None, exam_year: int | None
    ) -> bool:
        if (
            self.schedule_provider
            and exam_type in {"WAEC", "JAMB", "NECO"}
            and exam_year
        ):
            return await self.schedule_provider(subject, exam_type, exam_year)
        return False

    async def _select_questions(
        self,
        subject: Subject,
        topic_ids: list[str],
        exam_type: str | None,
        exam_year: int | None,
        count: int,
    ) -> list[Question]:
        picked = await _pick_prefer_unseen(
            self.session,
            self.student_id,
            count,
            subject_id=subject.id,
            topic_ids=topic_ids or None,
            exam_type=exam_type,
            exam_year=exam_year,
            difficulty=self.body.difficulty,
        )
        if not picked and topic_ids:
            picked = await _pick_prefer_unseen(
                self.session,
                self.student_id,
                count,
                subject_id=subject.id,
                exam_type=exam_type,
                exam_year=exam_year,
                difficulty=self.body.difficulty,
            )
        return picked


class SubmitAttemptService:
    def __init__(self, session: AsyncSession, student_id: str, body: SubmitIn) -> None:
        self.session = session
        self.student_id = student_id
        self.body = body

    async def process(self) -> tuple[dict, bool]:
        attempt, assessment = await self._load()
        if attempt.status == "COMPLETED":
            return await self._existing_result(attempt.id), False
        paper = await _paper_questions(self.session, assessment.id)
        answers = {item.questionId: item for item in self.body.answers}
        score, total, percentage, grade, remark, credit, extra = await self._score(
            assessment, paper, answers
        )
        response_rows = self._response_rows(paper, answers)
        now = utcnow()
        time_spent = self._time_spent(attempt, assessment, response_rows, now)
        completed = await self._complete(
            attempt, score, total, percentage, grade, time_spent, now
        )
        if not completed:
            await self.session.refresh(attempt)
            if attempt.status == "COMPLETED":
                return await self._existing_result(attempt.id), False
            raise ApiError(409, "This attempt is no longer in progress")
        await self._persist_responses(attempt, response_rows, now)
        await self.session.flush()
        payload = await self._existing_result(attempt.id)
        if extra:
            payload["jamb"] = extra
        payload["gradeRemark"] = remark
        payload["isCredit"] = credit
        return payload, True

    async def _load(self) -> tuple[AssessmentAttempt, Assessment]:
        attempt = await attempts_repository.by_id(self.session, self.body.attemptId)
        if attempt is None or attempt.student_id != self.student_id:
            raise ApiError(404, "Attempt not found")
        assessment = await assessments_repository.by_id(
            self.session, attempt.assessment_id
        )
        if assessment is None:
            raise ApiError(404, "Attempt not found")
        return attempt, assessment

    async def _existing_result(self, attempt_id: str) -> dict:
        return await GetAttemptResultService(
            self.session, self.student_id, attempt_id
        ).process()

    async def _score(
        self,
        assessment: Assessment,
        paper: list[Question],
        answers: dict[str, AnswerIn],
    ):
        if assessment.assessment_type == "CBT_PRACTICE":
            sections = await _jamb_sections(self.session, paper, answers)
            scored = score_jamb_paper(sections)
            score = scored.score
            total = float(scored.total_marks)
            percentage = scored.percentage
            grade, remark, credit = waec_grade(percentage)
            extra = {
                "score": scored.score,
                "totalMarks": scored.total_marks,
                "percentage": scored.percentage,
                "subjects": [
                    {
                        "subjectId": section.subject_id,
                        "name": section.subject_name,
                        "correct": section.correct,
                        "total": section.total,
                        "marks": section.marks,
                    }
                    for section in scored.subjects
                ],
                "band": scored.band,
            }
            return score, total, percentage, grade, remark, credit, extra
        earned = 0
        for question in paper:
            answer = answers.get(question.id)
            selected = answer.selectedAnswer if answer else None
            if selected is not None and str(selected) == str(question.correct_answer):
                earned += question.marks
        score = float(earned)
        total = float(assessment.total_marks or len(paper))
        percentage = round_percentage(score, total)
        grade, remark, credit = waec_grade(percentage)
        return score, total, percentage, grade, remark, credit, None

    def _response_rows(
        self, paper: list[Question], answers: dict[str, AnswerIn]
    ) -> list[dict]:
        response_rows = []
        for question in paper:
            answer = answers.get(question.id)
            selected = answer.selectedAnswer if answer else None
            response_rows.append(
                {
                    "question": question,
                    "selected": selected,
                    "correct": selected is not None
                    and str(selected) == str(question.correct_answer),
                    "seconds": answer.timeSpentSeconds if answer else 0,
                    "flagged": bool(answer.flaggedForReview) if answer else False,
                }
            )
        return response_rows

    def _time_spent(
        self,
        attempt: AssessmentAttempt,
        assessment: Assessment,
        response_rows: list[dict],
        now,
    ) -> int:
        elapsed = max(0, int((now - as_utc(attempt.started_at)).total_seconds()))
        client_reported = sum(item["seconds"] for item in response_rows)
        allowed = (
            assessment.time_limit_minutes * 60
            if assessment.time_limit_minutes
            else None
        )
        bounds = [client_reported, elapsed]
        if allowed is not None:
            bounds.append(allowed)
        return min(bounds)

    async def _complete(
        self,
        attempt: AssessmentAttempt,
        score: float,
        total: float,
        percentage: float,
        grade: str,
        time_spent: int,
        now,
    ) -> bool:
        result = await attempts_repository.update_where(
            self.session,
            {
                "status": "COMPLETED",
                "score": score,
                "total_marks": total,
                "percentage": percentage,
                "grade": grade,
                "time_spent_seconds": time_spent,
                "away_events": self.body.awayEvents or 0,
                "completed_at": now,
            },
            AssessmentAttempt.id == attempt.id,
            AssessmentAttempt.status == "IN_PROGRESS",
        )
        return isinstance(result, CursorResult) and result.rowcount != 0

    async def _persist_responses(
        self, attempt: AssessmentAttempt, response_rows: list[dict], now
    ) -> None:
        for item in response_rows:
            await responses_repository.add(
                self.session,
                QuestionResponse(
                    id=cuid(),
                    attempt_id=attempt.id,
                    question_id=item["question"].id,
                    selected_answer=None
                    if item["selected"] is None
                    else str(item["selected"]),
                    is_correct=item["correct"],
                    time_spent_seconds=item["seconds"],
                    flagged_for_review=item["flagged"],
                ),
            )
            await learning_events_repository.add(
                self.session,
                LearningEvent(
                    student_id=self.student_id,
                    subject_id=item["question"].subject_id,
                    topic_id=item["question"].topic_id,
                    kind="QUESTION_ANSWERED",
                    correct=item["correct"],
                    difficulty=item["question"].difficulty,
                    seconds=item["seconds"],
                    source_id=item["question"].id,
                    occurred_at=now,
                ),
            )


class GetAttemptResultService:
    def __init__(self, session: AsyncSession, student_id: str, attempt_id: str) -> None:
        self.session = session
        self.student_id = student_id
        self.attempt_id = attempt_id

    async def process(self) -> dict:
        attempt, assessment = await self._load()
        paper = await _paper_questions(self.session, assessment.id)
        stored = await responses_repository.for_attempt(self.session, attempt.id)
        by_question = {row.question_id: row for row in stored}
        results, breakdown, correct_count = await self._build_results(
            paper, by_question
        )
        topic_rows = self._topic_rows(breakdown)
        grade, remark, credit = waec_grade(attempt.percentage or 0)
        return {
            "attemptId": attempt.id,
            "assessmentTitle": assessment.title,
            "assessmentType": assessment.assessment_type,
            "examYear": assessment.exam_year,
            "jamb": None,
            "score": attempt.score,
            "totalMarks": attempt.total_marks,
            "percentage": attempt.percentage,
            "grade": attempt.grade or grade,
            "gradeRemark": remark,
            "isCredit": credit,
            "timeSpentSeconds": attempt.time_spent_seconds,
            "awayEvents": attempt.away_events,
            "totalQuestions": len(paper),
            "correctCount": correct_count,
            "results": results,
            "topicBreakdown": topic_rows,
        }

    async def _load(self) -> tuple[AssessmentAttempt, Assessment]:
        attempt = await attempts_repository.by_id(self.session, self.attempt_id)
        if attempt is None or attempt.student_id != self.student_id:
            raise ApiError(404, "Attempt not found")
        assessment = await assessments_repository.by_id(
            self.session, attempt.assessment_id
        )
        if assessment is None:
            raise ApiError(404, "Attempt not found")
        return attempt, assessment

    async def _build_results(
        self, paper: list[Question], by_question: dict
    ) -> tuple[list[dict], dict[str, dict], int]:
        results = []
        breakdown: dict[str, dict] = {}
        correct_count = 0
        for question in paper:
            response = by_question.get(question.id)
            is_correct = bool(response and response.is_correct)
            if is_correct:
                correct_count += 1
            topic_title = None
            if question.topic_id:
                topic = await topics_repository.by_id(self.session, question.topic_id)
                topic_title = topic.title if topic else None
                bucket = breakdown.setdefault(
                    question.topic_id,
                    {
                        "topicId": question.topic_id,
                        "topicTitle": topic_title,
                        "correct": 0,
                        "total": 0,
                    },
                )
                bucket["total"] += 1
                bucket["correct"] += int(is_correct)
            results.append(
                {
                    "questionId": question.id,
                    "questionText": question.question_text,
                    "questionImageUrl": question.question_image_url,
                    "options": question.options,
                    "selectedAnswer": response.selected_answer if response else None,
                    "correctAnswer": question.correct_answer,
                    "isCorrect": is_correct,
                    "explanation": question.explanation,
                    "explanationImageUrl": question.explanation_image_url,
                    "topicId": question.topic_id,
                    "topicTitle": topic_title,
                    "timeSpentSeconds": (
                        response.time_spent_seconds if response else 0
                    ),
                    "flaggedForReview": (
                        response.flagged_for_review if response else False
                    ),
                }
            )
        return results, breakdown, correct_count

    def _topic_rows(self, breakdown: dict[str, dict]) -> list[dict]:
        topic_rows = []
        for bucket in breakdown.values():
            accuracy = (
                0
                if bucket["total"] == 0
                else round(100 * bucket["correct"] / bucket["total"], 1)
            )
            topic_rows.append(
                {
                    **bucket,
                    "accuracy": accuracy,
                    "status": topic_breakdown_status(accuracy),
                }
            )
        return topic_rows


class GetBoardReadinessService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def process(self) -> dict:
        boards = {}
        for board in ("WAEC", "JAMB", "NECO"):
            counts = await questions_repository.board_subject_counts(
                self.session, board
            )
            qualifying = sum(1 for _subject_id, count in counts if count >= 20)
            ready = qualifying >= 3
            boards[board] = {
                "board": board,
                "ready": ready,
                "qualifying": qualifying,
                "started": sum(1 for _subject_id, count in counts if count > 0),
                "required": 3,
                "reason": None
                if ready
                else "Need at least 3 subjects with 20 tagged questions",
            }
        return {"boards": boards}


class GetMockOptionsService:
    def __init__(self, session: AsyncSession, exam_type: str) -> None:
        self.session = session
        self.exam_type = exam_type

    async def process(self) -> dict:
        subject_rows = await subjects_repository.ordered(self.session)
        payload = []
        for subject in subject_rows:
            levels = await curriculum_levels_repository.list_where(
                self.session, CurriculumLevel.subject_id == subject.id
            )
            scopes = []
            for level in levels:
                count = await questions_repository.count_for_level(
                    self.session,
                    subject_id=subject.id,
                    exam_type=self.exam_type,
                    curriculum_level_id=level.id,
                )
                scopes.append(
                    {
                        "classLevel": level.class_level,
                        "term": level.term,
                        "count": count or 0,
                    }
                )
            payload.append(
                {
                    "id": subject.id,
                    "name": subject.name,
                    "slug": subject.slug,
                    "scopes": scopes,
                }
            )
        return {"examType": self.exam_type, "subjects": payload}


class GenerateScopedMockService:
    def __init__(
        self, session: AsyncSession, student_id: str, body: ScopedMockIn
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.body = body

    async def process(self) -> dict:
        subject = await self._load_subject()
        scopes = self._scope_range()
        count = self.body.count or 40
        picked = await _pick(
            self.session,
            subject_id=subject.id,
            count=count,
            exam_type=self.body.examType,
            class_levels=scopes,
        )
        if not picked:
            raise ApiError(
                422,
                "No questions in that scope",
                reason="NO_QUESTIONS_IN_SCOPE",
                scope={
                    "from": self._point(self.body.from_),
                    "to": self._point(self.body.to),
                },
            )
        paper = await _persist(
            self.session,
            student_id=self.student_id,
            subject=subject,
            questions=picked,
            assessment_type="MOCK_EXAM",
            exam_type=self.body.examType,
            exam_year=None,
            title=f"{subject.name} {self.body.examType} mock",
            untimed=False,
        )
        paper["scope"] = {
            "from": self._point(self.body.from_),
            "to": self._point(self.body.to),
        }
        paper["subject"] = {"id": subject.id, "name": subject.name}
        paper["requestedCount"] = count
        paper["short"] = len(picked) < count
        return paper

    async def _load_subject(self) -> Subject:
        subject = await subjects_repository.by_id(self.session, self.body.subjectId)
        if subject is None:
            raise ApiError(404, "Subject not found")
        return subject

    def _point(self, point) -> dict:
        return {"classLevel": point.classLevel, "term": point.term}

    def _scope_range(self) -> list[tuple[str, str]]:
        start, end = self.body.from_, self.body.to
        order = ["SS1", "SS2", "SS3"]
        terms = ["FIRST", "SECOND", "THIRD"]
        start_key = (order.index(start.classLevel), terms.index(start.term))
        end_key = (order.index(end.classLevel), terms.index(end.term))
        if start_key > end_key:
            start_key, end_key = end_key, start_key
        scopes = []
        for level_index, level in enumerate(order):
            for term_index, term in enumerate(terms):
                key = (level_index, term_index)
                if start_key <= key <= end_key:
                    scopes.append((level, term))
        return scopes


class GetJambOptionsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def process(self) -> dict:
        english = await subjects_repository.by_code(self.session, JAMB_ENGLISH_CODE)
        subject_rows = await subjects_repository.list_where(
            self.session,
            Subject.is_jamb.is_(True),
            order_by=(Subject.name,),
        )
        years = []
        if english:
            years = await questions_repository.exam_years(
                self.session, english.id, "JAMB"
            )
        return {
            "spec": {
                "englishQuestions": JAMB_ENGLISH_QUESTIONS,
                "subjectQuestions": JAMB_SUBJECT_QUESTIONS,
                "totalQuestions": 180,
                "durationMinutes": JAMB_DURATION_MINUTES,
                "totalMarks": JAMB_TOTAL_MARKS,
            },
            "english": None
            if english is None
            else {"id": english.id, "name": english.name, "code": english.code},
            "englishYears": sorted([year for year in years if year], reverse=True),
            "subjects": [
                {
                    "id": subject.id,
                    "name": subject.name,
                    "slug": subject.slug,
                    "code": subject.code,
                }
                for subject in subject_rows
                if english is None or subject.id != english.id
            ],
        }


class PrepareJambService:
    def __init__(
        self,
        session: AsyncSession,
        english: Subject,
        subjects: list[Subject],
        exam_year: int,
    ) -> None:
        self.session = session
        self.english = english
        self.subjects = subjects
        self.exam_year = exam_year

    async def process(self) -> dict:
        coverage = [
            {
                "subjectId": self.english.id,
                "code": self.english.code,
                "have": await self._count(self.english.id),
                "need": JAMB_ENGLISH_QUESTIONS,
            }
        ]
        for subject in self.subjects:
            coverage.append(
                {
                    "subjectId": subject.id,
                    "code": subject.code,
                    "have": await self._count(subject.id),
                    "need": JAMB_SUBJECT_QUESTIONS,
                }
            )
        shortfalls = [row for row in coverage if row["have"] < row["need"]]
        ready = not shortfalls
        return {
            "outcome": "ok",
            "examYear": self.exam_year,
            "ready": ready,
            "message": "This year is ready"
            if ready
            else "This year is still short of a full paper",
            "coverage": coverage,
            "shortfalls": shortfalls,
        }

    async def _count(self, subject_id: str) -> int:
        return await questions_repository.count_objective(
            self.session,
            subject_id=subject_id,
            exam_type="JAMB",
            exam_year=self.exam_year,
        )


class GenerateJambService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        subject_ids: list[str],
        exam_year: int,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.subject_ids = subject_ids
        self.exam_year = exam_year

    async def process(self) -> dict:
        english, chosen = await select_jamb_subjects(self.session, self.subject_ids)
        report = await PrepareJambService(
            self.session, english, chosen, self.exam_year
        ).process()
        if not report["ready"]:
            raise ApiError(
                422,
                "Not enough questions for a full JAMB paper",
                reason="INSUFFICIENT_QUESTIONS",
                **report,
            )
        resumed = await self._resume_existing(english, chosen)
        if resumed is not None:
            return resumed
        quotas = [
            (english, JAMB_ENGLISH_QUESTIONS),
            *[(subject, JAMB_SUBJECT_QUESTIONS) for subject in chosen],
        ]
        drawn = await self._draw(quotas, report)
        payload = await _persist(
            self.session,
            student_id=self.student_id,
            subject=english,
            questions=drawn,
            assessment_type="CBT_PRACTICE",
            exam_type="JAMB",
            exam_year=self.exam_year,
            title=f"JAMB CBT {self.exam_year}",
            untimed=False,
            total_marks=JAMB_TOTAL_MARKS,
            time_override=JAMB_DURATION_MINUTES,
        )
        payload["subjects"] = [
            {"id": subject.id, "name": subject.name, "count": quota}
            for subject, quota in quotas
        ]
        return payload

    async def _resume_existing(
        self, english: Subject, chosen: list[Subject]
    ) -> dict | None:
        existing = await attempts_repository.in_progress_cbt(
            self.session,
            student_id=self.student_id,
            exam_type="JAMB",
            exam_year=self.exam_year,
            total_marks=JAMB_TOTAL_MARKS,
        )
        if not existing:
            return None
        assessment = await assessments_repository.by_id(
            self.session, existing.assessment_id
        )
        if assessment is None:
            raise ApiError(404, "Attempt not found")
        paper = await _paper_questions(self.session, assessment.id)
        payload = _payload(assessment, existing, paper, resumed=True)
        payload["subjects"] = [
            {"id": subject.id, "name": subject.name} for subject in [english, *chosen]
        ]
        return payload

    async def _draw(
        self, quotas: list[tuple[Subject, int]], report: dict
    ) -> list[Question]:
        drawn: list[Question] = []
        for subject, quota in quotas:
            picked = await _pick(
                self.session,
                subject_id=subject.id,
                count=quota,
                exam_type="JAMB",
                exam_year=self.exam_year,
            )
            if len(picked) < quota:
                raise ApiError(
                    422,
                    "Not enough questions for a full JAMB paper",
                    reason="INSUFFICIENT_QUESTIONS",
                    **report,
                )
            drawn.extend(picked)
        return drawn


class StartPretestService:
    def __init__(self, session: AsyncSession, student_id: str, topic_id: str) -> None:
        self.session = session
        self.student_id = student_id
        self.topic_id = topic_id

    async def process(self) -> dict:
        from app.services.learning.evidence import PRETEST_PASS

        topic, subject = await self._load()
        picked = await self._select_questions(subject, topic)
        if not picked:
            raise ApiError(404, "No questions found")
        already = await metrics_repository.first(
            self.session,
            PerformanceMetric.student_id == self.student_id,
            PerformanceMetric.topic_id == topic.id,
            PerformanceMetric.pretest_passed_at.is_not(None),
        )
        payload = await _persist(
            self.session,
            student_id=self.student_id,
            subject=subject,
            questions=picked[:5],
            assessment_type="TOPIC_QUIZ",
            exam_type=None,
            exam_year=None,
            title=f"{topic.title} pretest",
            untimed=False,
            pass_mark=PRETEST_PASS,
        )
        payload["alreadyPassed"] = already is not None
        payload["threshold"] = PRETEST_PASS
        return payload

    async def _load(self) -> tuple:
        topic = await topics_repository.by_id(self.session, self.topic_id)
        if topic is None:
            raise ApiError(404, "Topic not found")
        subject = await subjects_repository.by_id(self.session, topic.subject_id)
        if subject is None:
            raise ApiError(404, "Topic not found")
        return topic, subject

    async def _select_questions(self, subject: Subject, topic) -> list[Question]:
        picked = await _pick(
            self.session, subject_id=subject.id, count=5, topic_ids=[topic.id]
        )
        if len(picked) < 5:
            extra = await _pick(
                self.session,
                subject_id=subject.id,
                count=5 - len(picked),
                exclude={question.id for question in picked},
            )
            picked.extend(extra)
        return picked


class GradePretestService:
    def __init__(
        self, session: AsyncSession, student_id: str, topic_id: str, body: PretestIn
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.topic_id = topic_id
        self.body = body

    async def process(self) -> dict:
        from app.services.learning import RecordPretestPassService
        from app.services.learning.evidence import PRETEST_PASS

        attempt = await self._load_attempt()
        topic = await self._load_topic()
        paper = await _paper_questions(self.session, attempt.assessment_id)
        correct = await self._grade_answers(attempt, paper)
        total = len(paper) or 1
        percentage = round(100 * correct / total, 2)
        await self._complete(attempt, correct, len(paper), percentage, PRETEST_PASS)
        prior = await metrics_repository.first(
            self.session,
            PerformanceMetric.student_id == self.student_id,
            PerformanceMetric.topic_id == topic.id,
            PerformanceMetric.pretest_passed_at.is_not(None),
        )
        already = prior is not None
        recorded = False
        if percentage >= PRETEST_PASS:
            already = await RecordPretestPassService(
                self.session,
                self.student_id,
                topic.subject_id,
                topic.id,
                percentage,
            ).process()
            recorded = not already
        return {
            "passed": percentage >= PRETEST_PASS,
            "alreadyPassed": already,
            "percentage": percentage,
            "correctCount": correct,
            "totalQuestions": len(paper),
            "threshold": PRETEST_PASS,
            "recorded": recorded,
        }

    async def _load_attempt(self) -> AssessmentAttempt:
        attempt = await attempts_repository.by_id(self.session, self.body.attemptId)
        if attempt is None or attempt.student_id != self.student_id:
            raise ApiError(404, "Attempt not found")
        if attempt.status != "IN_PROGRESS":
            raise ApiError(400, "This pretest is no longer in progress")
        return attempt

    async def _load_topic(self):
        topic = await topics_repository.by_id(self.session, self.topic_id)
        if topic is None:
            raise ApiError(404, "Topic not found")
        return topic

    async def _grade_answers(
        self, attempt: AssessmentAttempt, paper: list[Question]
    ) -> int:
        answers = self.body.answers
        if answers is None:
            raise ApiError(400, "answers are required")
        by_id = {question.id: question for question in paper}
        correct = 0
        seen: set[str] = set()
        for item in answers:
            question = by_id.get(item.questionId)
            if question is None:
                raise ApiError(400, "Answer does not belong to this pretest")
            if question.id in seen:
                continue
            seen.add(question.id)
            selected = item.selectedAnswer
            is_correct = (
                selected is not None
                and str(selected).upper() == question.correct_answer.upper()
            )
            correct += int(is_correct)
            await responses_repository.add(
                self.session,
                QuestionResponse(
                    id=cuid(),
                    attempt_id=attempt.id,
                    question_id=question.id,
                    selected_answer=None if selected is None else str(selected),
                    is_correct=is_correct,
                    time_spent_seconds=item.timeSpentSeconds,
                ),
            )
        return correct

    async def _complete(
        self,
        attempt: AssessmentAttempt,
        correct: int,
        paper_len: int,
        percentage: float,
        pretest_pass: int,
    ) -> None:
        attempt.status = "COMPLETED"
        attempt.score = float(correct)
        attempt.total_marks = float(paper_len)
        attempt.percentage = percentage
        attempt.grade = "Pass" if percentage >= pretest_pass else "Retry"
        attempt.completed_at = utcnow()
        await self.session.flush()
