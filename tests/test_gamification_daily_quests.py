# ruff: noqa: E501
from app.services.gamification.quests import DayCounts
from app.services.gamification.service import GetDailyQuestsService


class FakeQuestRepo:
    def __init__(self, counts=None, focus=None, due=0):
        self.stored: dict[str, list] = {}
        self.counts = counts or DayCounts(0, {}, 0, 0)
        self.focus = focus
        self.due = due
        self.generated = 0
        self.awards: dict[str, int] = {}

    async def quests_for_day(self, session, student_id, day):
        return self.stored.get(day)

    async def save_quests(self, session, student_id, day, quests):
        self.stored.setdefault(day, quests)  # first writer wins, like the unique key
        self.generated += 1

    async def day_counts(self, session, student_id, since):
        return self.counts

    async def recent_focus_subject(self, session, student_id, since):
        return self.focus

    async def due_card_total(self, session, student_id, now):
        return self.due

    async def award(
        self, session, *, student_id, event, source_id, amount, reason, key
    ):
        if key in self.awards:
            return False
        self.awards[key] = amount
        return True


def _svc(repo):
    return GetDailyQuestsService(None, "stu1", repo=repo)


async def test_the_set_is_generated_once_per_day_and_reused():
    repo = FakeQuestRepo(focus=("s1", "Physics"))
    first = await _svc(repo).process()
    repo.focus = ("s2", "Biology")  # the world changes; the day's set must not
    second = await _svc(repo).process()
    assert [q["id"] for q in first["quests"]] == [q["id"] for q in second["quests"]]
    assert second["quests"][1]["subjectId"] == "s1"


async def test_progress_follows_real_activity_without_resetting_the_set():
    repo = FakeQuestRepo()
    await _svc(repo).process()
    repo.counts = DayCounts(6, {}, 0, 0)
    out = await _svc(repo).process()
    assert out["quests"][0]["complete"] is True
    assert out["allComplete"] is False
    assert out["bonusAwarded"] is False
    assert repo.awards == {}


async def test_finishing_all_quests_pays_the_bonus_exactly_once():
    repo = FakeQuestRepo(counts=DayCounts(20, {}, 0, 1))
    first = await _svc(repo).process()
    again = await _svc(repo).process()
    assert first["allComplete"] and first["bonusAwarded"]
    assert again["allComplete"] and again["bonusAwarded"]
    assert list(repo.awards.values()) == [30]
    assert first["bonusXp"] == 30


async def test_a_failed_award_does_not_claim_the_bonus():
    repo = FakeQuestRepo(counts=DayCounts(20, {}, 0, 1))

    async def boom(*a, **k):
        raise RuntimeError("db down")

    repo.award = boom
    out = await _svc(repo).process()
    assert out["allComplete"] is True
    assert out["bonusAwarded"] is False
