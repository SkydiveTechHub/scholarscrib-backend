from app.services.gamification.service import AwardAttemptXpService, GetXpProfileService


class FakeXp:
    """In-memory stand-in with the real repository's uniqueness behaviour."""

    def __init__(self, completed=True, answered=10, lesson_topic=None):
        self.rows: dict[str, dict] = {}
        self.facts = (completed, answered, lesson_topic)

    async def attempt_facts(self, session, student_id, attempt_id):
        return self.facts

    async def count_since(self, session, student_id, event, since):
        return sum(1 for r in self.rows.values() if r["event"] == event)

    async def award(
        self, session, *, student_id, event, source_id, amount, reason, key
    ):
        if key in self.rows:
            return False
        self.rows[key] = {"event": event, "amount": amount, "reason": reason}
        return True

    async def total(self, session, student_id):
        return sum(r["amount"] for r in self.rows.values())

    async def recent(self, session, student_id, limit=20):
        return []


async def _run(repo, exit_passed=None, attempt="att1"):
    return await AwardAttemptXpService(
        None, "stu1", attempt, exit_passed, repo=repo
    ).process()


async def test_a_practice_set_pays_once_per_attempt():
    repo = FakeXp()
    first = await _run(repo)
    again = await _run(repo)
    assert first == [
        {
            "event": "practice_set_completed",
            "amount": 20,
            "reason": "Practice set completed",
        }
    ]
    assert again == []
    assert sum(r["amount"] for r in repo.rows.values()) == 20


async def test_a_passed_lesson_pays_once_per_topic_even_across_attempts():
    repo = FakeXp(lesson_topic="topicA")
    first = await _run(repo, exit_passed=True, attempt="att1")
    replay = await _run(repo, exit_passed=True, attempt="att2")
    assert [a["amount"] for a in first] == [30]
    assert replay == []


async def test_a_lesson_pass_without_a_recorded_completion_pays_nothing():
    repo = FakeXp(lesson_topic=None)
    assert await _run(repo, exit_passed=True) == []


async def test_an_incomplete_or_tiny_attempt_pays_nothing():
    assert await _run(FakeXp(completed=False)) == []
    assert await _run(FakeXp(answered=2)) == []


async def test_the_profile_reports_total_and_level():
    repo = FakeXp()
    await _run(repo)
    profile = await GetXpProfileService(None, "stu1", repo=repo).process()
    assert profile["totalXp"] == 20
    assert profile["level"] == 1
    assert profile["title"] == "Starter Scholar"
    assert profile["xpToNext"] == 80
    assert profile["isMax"] is False


async def test_a_new_student_has_a_zero_profile():
    profile = await GetXpProfileService(None, "stu1", repo=FakeXp()).process()
    assert profile["totalXp"] == 0
    assert profile["recent"] == []
