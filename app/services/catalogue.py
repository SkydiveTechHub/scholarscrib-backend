import math
import time
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Question, Subject
from app.database.repositories.curriculum import subjects_repository, topics_repository
from app.database.repositories.provider import catalogues_repository
from app.database.repositories.question import questions_repository

_CACHE: dict[str, tuple[float, object]] = {}
CATALOGUE_TTL = 3600


def bust_catalogue() -> None:
    _CACHE.clear()


def _get(key: str):
    found = _CACHE.get(key)
    if not found:
        return None
    stored_at, value = found
    if time.monotonic() - stored_at > CATALOGUE_TTL:
        _CACHE.pop(key, None)
        return None
    return value


def _set(key: str, value: object) -> None:
    _CACHE[key] = (time.monotonic(), value)


class ListSubjectsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def process(self) -> list[dict]:
        cached = _get("subjects")
        if cached is not None:
            return cached  # type: ignore[return-value]
        payload = await self._load()
        _set("subjects", payload)
        return payload

    async def _load(self) -> list[dict]:
        rows = await subjects_repository.ordered(self.session)
        payload = []
        for subject in rows:
            topic_count = await topics_repository.count_for_subject(
                self.session, subject.id
            )
            question_count = await questions_repository.count_for_subject(
                self.session, subject.id
            )
            payload.append(
                {
                    "id": subject.id,
                    "name": subject.name,
                    "slug": subject.slug,
                    "code": subject.code,
                    "isWaec": subject.is_waec,
                    "isJamb": subject.is_jamb,
                    "isNeco": subject.is_neco,
                    "trackCategory": subject.track_category,
                    "_count": {"topics": topic_count, "questions": question_count},
                }
            )
        return payload


class GetTopicSummaryService:
    def __init__(
        self, session: AsyncSession, subject_slug: str, topic_slug: str
    ) -> None:
        self.session = session
        self.subject_slug = subject_slug
        self.topic_slug = topic_slug

    async def process(self) -> dict | None:
        subject = await subjects_repository.by_slug(self.session, self.subject_slug)
        if subject is None:
            return None
        topic = await topics_repository.by_slug(
            self.session, subject.id, self.topic_slug
        )
        if topic is None:
            return None
        count = await questions_repository.count_for_topic(self.session, topic.id)
        return {
            "subjectId": subject.id,
            "subjectName": subject.name,
            "topicId": topic.id,
            "topicTitle": topic.title,
            "questionCount": count or 0,
        }


class ListPastPapersService:
    def __init__(
        self, session: AsyncSession, exam_type: str | None, subject_id: str | None
    ) -> None:
        self.session = session
        self.exam_type = exam_type
        self.subject_id = subject_id

    async def process(self) -> dict:
        counted = await questions_repository.past_paper_groups(
            self.session, self.exam_type, self.subject_id
        )
        subject_rows = {
            row.id: row for row in await subjects_repository.all_ordered(self.session)
        }
        papers = self._cached_papers(counted, subject_rows)
        seen = {
            (paper["examType"], paper["examYear"], paper["subjectId"])
            for paper in papers
        }
        papers.extend(await self._catalogue_papers(subject_rows, seen))
        papers.sort(
            key=lambda paper: (
                paper["subjectName"],
                paper["examType"],
                -(paper["examYear"] or 0),
            )
        )
        return {"papers": papers}

    def _cached_papers(self, counted, subject_rows: dict) -> list[dict]:
        papers = []
        for board, year, subject, count in counted:
            owner = subject_rows.get(subject)
            if owner is None:
                continue
            papers.append(_paper(owner, board, year, count, True))
        return papers

    async def _catalogue_papers(self, subject_rows: dict, seen: set) -> list[dict]:
        papers = []
        for row in await catalogues_repository.matching(
            self.session, self.exam_type, self.subject_id
        ):
            key = (row.exam_type, row.exam_year, row.subject_id)
            if key in seen:
                continue
            owner = subject_rows.get(row.subject_id)
            if owner is None:
                continue
            papers.append(_paper(owner, row.exam_type, row.exam_year, None, False))
        return papers


def _paper(
    subject: Subject, exam_type: str, exam_year: int, count: int | None, cached: bool
) -> dict:
    return {
        "examType": exam_type,
        "examYear": exam_year,
        "subjectId": subject.id,
        "subjectName": subject.name,
        "subjectSlug": subject.slug,
        "trackCategory": subject.track_category,
        "questionCount": count,
        "cached": cached,
    }


def visible_subjects(
    subjects: list[dict], track: str | None, exam_type: str | None
) -> list[dict]:
    rows = subjects
    if track:
        rows = [
            row
            for row in rows
            if row["trackCategory"] == "CORE" or row["trackCategory"] == track
        ]
    flag = {"waec": "isWaec", "jamb": "isJamb", "neco": "isNeco"}.get(
        (exam_type or "").lower()
    )
    if flag:
        rows = [row for row in rows if row[flag]]
    return rows


def public_question(question: Question, include_answers: bool) -> dict:
    payload = {
        "id": question.id,
        "subjectId": question.subject_id,
        "topicId": question.topic_id,
        "examType": question.exam_type,
        "examYear": question.exam_year,
        "questionNumber": question.question_number,
        "questionText": question.question_text,
        "questionImageUrl": question.question_image_url,
        "questionType": question.question_type,
        "options": question.options,
        "difficulty": question.difficulty,
        "marks": question.marks,
        "timeEstimateSeconds": question.time_estimate_seconds,
    }
    if include_answers:
        payload["correctAnswer"] = question.correct_answer
        payload["explanation"] = question.explanation
        payload["explanationImageUrl"] = question.explanation_image_url
    return payload


def time_limit_minutes(count: int, untimed: bool, exam_type: str | None) -> int | None:
    if untimed and not exam_type:
        return None
    return math.ceil(count * 1.5)


def deadline_at(started, minutes: int | None):
    if minutes is None:
        return None
    return started + timedelta(minutes=minutes)
