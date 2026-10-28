from app.services.gamification.service import GetStreakService


class FakeStreakRepo:
    def __init__(self, attempt_days=(), card_counts=None):
        self.attempt_days = set(attempt_days)
        self.card_counts = card_counts or {}

    async def study_day_inputs(self, session, student_id, since_day):
        return self.attempt_days, self.card_counts


async def _run(repo, today="2026-10-28"):
    return await GetStreakService(None, "stu1", repo=repo, today=today).process()


async def test_the_payload_describes_streak_calendar_and_return_state():
    out = await _run(FakeStreakRepo({"2026-10-27", "2026-10-26"}))
    assert out["currentStreak"] == 2
    assert out["longestStreak"] == 2
    assert out["studiedToday"] is False
    assert out["daysSinceLastStudy"] == 1
    assert out["days"] == ["2026-10-26", "2026-10-27"]
    assert out["timezone"] == "Africa/Lagos"
    assert out["today"] == "2026-10-28"


async def test_opening_the_app_alone_changes_nothing():
    out = await _run(FakeStreakRepo())
    assert out["currentStreak"] == 0
    assert out["days"] == []
    assert out["daysSinceLastStudy"] is None


async def test_card_reviews_count_once_they_reach_the_threshold():
    out = await _run(FakeStreakRepo(card_counts={"2026-10-28": 6, "2026-10-27": 2}))
    assert out["days"] == ["2026-10-28"]
    assert out["currentStreak"] == 1
