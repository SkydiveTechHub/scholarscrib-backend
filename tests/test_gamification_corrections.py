# ruff: noqa: E501
from app.services.gamification.rules import (
    CORRECTIONS_PER_DAY,
    XP_AMOUNTS,
    corrections_allowed,
)
from app.services.gamification.service import AwardCorrectionXpService


def test_a_correction_is_worth_five_xp():
    assert XP_AMOUNTS["answer_corrected"] == 5


def test_the_daily_cap_limits_corrections():
    assert corrections_allowed(awarded_today=0) == CORRECTIONS_PER_DAY
    assert corrections_allowed(awarded_today=CORRECTIONS_PER_DAY - 3) == 3
    assert corrections_allowed(awarded_today=CORRECTIONS_PER_DAY) == 0
    assert corrections_allowed(awarded_today=CORRECTIONS_PER_DAY + 5) == 0


class FakeRepo:
    def __init__(self, corrected, awarded_today=0):
        self.corrected = corrected
        self.awarded_today = awarded_today
        self.rows = {}

    async def corrected_questions(self, session, student_id, attempt_id, before):
        return self.corrected

    async def count_since(self, session, student_id, event, since):
        return self.awarded_today

    async def award(
        self, session, *, student_id, event, source_id, amount, reason, key
    ):
        if key in self.rows:
            return False
        self.rows[key] = amount
        return True


async def test_each_corrected_question_pays_once():
    repo = FakeRepo(["q1", "q2"])
    svc = AwardCorrectionXpService(None, "stu1", "att1", repo=repo)
    first = await svc.process()
    again = await svc.process()
    assert [a["amount"] for a in first] == [5, 5]
    assert first[0]["reason"] == "Corrected a missed question"
    assert again == []


async def test_the_same_question_corrected_in_a_later_attempt_pays_nothing():
    repo = FakeRepo(["q1"])
    await AwardCorrectionXpService(None, "stu1", "att1", repo=repo).process()
    later = await AwardCorrectionXpService(None, "stu1", "att2", repo=repo).process()
    assert later == []


async def test_corrections_stop_at_the_daily_cap():
    repo = FakeRepo([f"q{i}" for i in range(15)], awarded_today=7)
    out = await AwardCorrectionXpService(None, "stu1", "att1", repo=repo).process()
    assert len(out) == 3


async def test_no_corrections_means_no_xp():
    out = await AwardCorrectionXpService(
        None, "stu1", "att1", repo=FakeRepo([])
    ).process()
    assert out == []
