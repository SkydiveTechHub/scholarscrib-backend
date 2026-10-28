"""Award XP for finished learning activity and report a student's standing.

The server decides every amount. Each award goes through the ledger's unique
idempotency key, so a retried request, a replayed attempt or a concurrent
duplicate cannot pay twice.
"""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.core.timeutil import lagos_day_key, lagos_day_start, utcnow
from app.database.repositories.learning import (
    quest_repository,
    streak_repository,
    xp_repository,
)
from app.services.gamification.improvement import compare, message_for
from app.services.gamification.levels import level_progress
from app.services.gamification.quests import build_quests, evaluate
from app.services.gamification.rules import (
    REASONS,
    XP_AMOUNTS,
    corrections_allowed,
    events_for_attempt,
    idempotency_key,
)
from app.services.gamification.streaks import (
    calendar_days,
    display_streak,
    longest_streak,
    return_state,
    study_days,
)
from app.services.planner.days import add_days


class AwardAttemptXpService:
    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        attempt_id: str,
        exit_passed: bool | None,
        repo=xp_repository,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.attempt_id = attempt_id
        self.exit_passed = exit_passed
        self.repo = repo

    async def process(self) -> list[dict]:
        completed, answered, lesson_topic = await self.repo.attempt_facts(
            self.session, self.student_id, self.attempt_id
        )
        awarded_today = await self.repo.count_since(
            self.session,
            self.student_id,
            "practice_set_completed",
            lagos_day_start(lagos_day_key()),
        )
        awards = []
        for event in events_for_attempt(
            completed=completed,
            exit_passed=self.exit_passed,
            answered=answered,
            practice_awarded_today=awarded_today,
        ):
            # A lesson pays once per topic, however many exits are passed.
            source = self.attempt_id
            if event == "lesson_completed":
                if lesson_topic is None:
                    continue
                source = lesson_topic
            paid = await self.repo.award(
                self.session,
                student_id=self.student_id,
                event=event,
                source_id=source,
                amount=XP_AMOUNTS[event],
                reason=REASONS[event],
                key=idempotency_key(event, self.student_id, source),
            )
            if paid:
                awards.append(
                    {
                        "event": event,
                        "amount": XP_AMOUNTS[event],
                        "reason": REASONS[event],
                    }
                )
        return awards


class GetXpProfileService:
    def __init__(
        self, session: AsyncSession, student_id: str, repo=xp_repository
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.repo = repo

    async def process(self) -> dict:
        total = await self.repo.total(self.session, self.student_id)
        progress = level_progress(total)
        recent = await self.repo.recent(self.session, self.student_id)
        return {
            "totalXp": total,
            "level": progress.level,
            "title": progress.title,
            "xpIntoLevel": progress.xp_into_level,
            "xpToNext": progress.xp_to_next,
            "levelSpan": progress.level_span,
            "isMax": progress.is_max,
            "recent": [
                {
                    "id": row.id,
                    "amount": row.amount,
                    "reason": row.reason,
                    "occurredAt": row.occurred_at.isoformat(),
                }
                for row in recent
            ],
        }


FOCUS_LOOKBACK = timedelta(days=14)


class GetDailyQuestsService:
    """Today's quests, stable for the Lagos day, with progress from real activity.

    Finishing every quest pays the day's bonus once, through the XP ledger's
    idempotency key. Reading the quests is safe to repeat.
    """

    def __init__(
        self, session: AsyncSession, student_id: str, repo=quest_repository
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.repo = repo

    async def process(self) -> dict:
        day = lagos_day_key()
        since = lagos_day_start(day)
        quests = await self.repo.quests_for_day(self.session, self.student_id, day)
        if quests is None:
            now = utcnow()
            built = build_quests(
                focus_subject=await self.repo.recent_focus_subject(
                    self.session, self.student_id, now - FOCUS_LOOKBACK
                ),
                due_cards=await self.repo.due_card_total(
                    self.session, self.student_id, now
                ),
            )
            await self.repo.save_quests(self.session, self.student_id, day, built)
            # Re-read: if a concurrent request won, its set is the day's set.
            quests = (
                await self.repo.quests_for_day(self.session, self.student_id, day)
                or built
            )
        counts = await self.repo.day_counts(self.session, self.student_id, since)
        rows = evaluate(quests, counts)
        all_complete = bool(rows) and all(q["complete"] for q in rows)
        bonus_awarded = False
        if all_complete:
            bonus_awarded = await self._pay_bonus(day)
        return {
            "day": day,
            "quests": rows,
            "allComplete": all_complete,
            "bonusXp": XP_AMOUNTS["daily_quests_completed"],
            "bonusAwarded": bonus_awarded,
        }

    async def _pay_bonus(self, day: str) -> bool:
        """True once the ledger holds the day's bonus (now or already)."""
        event = "daily_quests_completed"
        try:
            await self.repo.award(
                self.session,
                student_id=self.student_id,
                event=event,
                source_id=day,
                amount=XP_AMOUNTS[event],
                reason=REASONS[event],
                key=idempotency_key(event, self.student_id, day),
            )
            return True
        except Exception:
            logger.exception("Daily quest bonus failed for student %s", self.student_id)
            return False


STREAK_HISTORY_DAYS = 400


class GetStreakService:
    """The student's study days, streak and, if they have been away, the gap."""

    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        repo=streak_repository,
        today: str | None = None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.repo = repo
        self.today = today

    async def process(self) -> dict:
        today = self.today or lagos_day_key()
        days = await study_days_for(self.session, self.student_id, self.repo, today)
        return {
            "timezone": "Africa/Lagos",
            "today": today,
            "currentStreak": display_streak(days, today),
            "longestStreak": longest_streak(days),
            "days": calendar_days(days, today),
            **return_state(days, today),
        }


async def study_days_for(
    session: AsyncSession, student_id: str, repo=None, today: str | None = None
) -> set[str]:
    """The one definition of a study day, shared by the dashboard, achievements
    and the streak calendar."""
    repo = repo or streak_repository
    today = today or lagos_day_key()
    attempt_days, card_counts = await repo.study_day_inputs(
        session, student_id, add_days(today, -STREAK_HISTORY_DAYS)
    )
    return study_days(attempt_days, card_counts)


class AwardTopicMasteryXpService:
    """50 XP the first time a topic's evidence reaches mastery.

    Uses the same `is_mastered` rule the subject page paints with (enough
    answers from enough different days), so XP can never disagree with the
    graph. One award per student and topic, ever: a topic that later decays and
    is mastered again does not pay twice.
    """

    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        attempt_id: str,
        repo=xp_repository,
        states_for=None,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.attempt_id = attempt_id
        self.repo = repo
        self.states_for = states_for or self._mastery_states

    async def _mastery_states(self, topic_ids: list[str]) -> dict:
        from app.services.learning.mastery_store import GetTopicMasteryService

        return await GetTopicMasteryService(
            self.session, self.student_id, topic_ids
        ).process()

    async def process(self) -> list[dict]:
        from app.services.learning.mastery import is_mastered

        topic_ids = await self.repo.touched_topic_ids(
            self.session, self.student_id, self.attempt_id
        )
        if not topic_ids:
            return []
        event = "topic_mastery_demonstrated"
        awards = []
        for topic_id, state in (await self.states_for(topic_ids)).items():
            if not is_mastered(state):
                continue
            paid = await self.repo.award(
                self.session,
                student_id=self.student_id,
                event=event,
                source_id=topic_id,
                amount=XP_AMOUNTS[event],
                reason=REASONS[event],
                key=idempotency_key(event, self.student_id, topic_id),
            )
            if paid:
                awards.append(
                    {
                        "event": event,
                        "amount": XP_AMOUNTS[event],
                        "reason": REASONS[event],
                    }
                )
        return awards


class AwardCorrectionXpService:
    """5 XP for each question missed on an earlier day and now answered right.

    One award per question, ever, through the ledger key; at most
    CORRECTIONS_PER_DAY a day.
    """

    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        attempt_id: str,
        repo=xp_repository,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.attempt_id = attempt_id
        self.repo = repo

    async def process(self) -> list[dict]:
        event = "answer_corrected"
        day_start = lagos_day_start(lagos_day_key())
        corrected = await self.repo.corrected_questions(
            self.session, self.student_id, self.attempt_id, day_start
        )
        if not corrected:
            return []
        room = corrections_allowed(
            await self.repo.count_since(self.session, self.student_id, event, day_start)
        )
        awards = []
        for question_id in corrected:
            if room <= 0:
                break
            paid = await self.repo.award(
                self.session,
                student_id=self.student_id,
                event=event,
                source_id=question_id,
                amount=XP_AMOUNTS[event],
                reason=REASONS[event],
                key=idempotency_key(event, self.student_id, question_id),
            )
            if paid:
                room -= 1
                awards.append(
                    {
                        "event": event,
                        "amount": XP_AMOUNTS[event],
                        "reason": REASONS[event],
                    }
                )
        return awards


class GetImprovementService:
    """Whether this attempt shows real improvement over comparable practice."""

    def __init__(
        self,
        session: AsyncSession,
        student_id: str,
        attempt_id: str,
        repo=xp_repository,
    ) -> None:
        self.session = session
        self.student_id = student_id
        self.attempt_id = attempt_id
        self.repo = repo

    async def process(self) -> dict:
        history = await self.repo.comparable_history(
            self.session, self.student_id, self.attempt_id
        )
        result = compare(history)
        return {**result, "message": message_for(result)}
