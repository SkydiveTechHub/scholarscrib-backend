from app.services.gamification.service import AwardTopicMasteryXpService
from app.services.learning.mastery import TopicState


def _mastered(topic_id):
    return TopicState(
        topic_id=topic_id, mastery=82, observations=12, evidence_days=3, confidence=0.9
    )


class FakeRepo:
    def __init__(self, touched):
        self.touched = touched
        self.rows: dict[str, int] = {}

    async def touched_topic_ids(self, session, student_id, attempt_id):
        return self.touched

    async def award(  # noqa: E501
        self, session, *, student_id, event, source_id, amount, reason, key
    ):
        if key in self.rows:
            return False
        self.rows[key] = amount
        return True


def _svc(repo, states):
    async def load(topic_ids):
        return {t: states[t] for t in topic_ids if t in states}

    return AwardTopicMasteryXpService(None, "stu1", "att1", repo=repo, states_for=load)


async def test_a_newly_mastered_topic_pays_fifty_once():
    repo = FakeRepo(["t1"])
    svc = _svc(repo, {"t1": _mastered("t1")})
    first = await svc.process()
    again = await svc.process()
    assert [a["amount"] for a in first] == [50]
    assert first[0]["reason"] == "Topic mastery demonstrated"
    assert again == []


async def test_a_topic_that_is_not_mastered_pays_nothing():
    weak = TopicState(topic_id="t1", mastery=40, observations=10, evidence_days=3)
    assert await _svc(FakeRepo(["t1"]), {"t1": weak}).process() == []


async def test_a_high_score_on_one_day_is_not_mastery():
    one_day = TopicState(topic_id="t1", mastery=95, observations=20, evidence_days=1)
    assert await _svc(FakeRepo(["t1"]), {"t1": one_day}).process() == []


async def test_each_mastered_topic_pays_separately():
    repo = FakeRepo(["t1", "t2"])
    out = await _svc(repo, {"t1": _mastered("t1"), "t2": _mastered("t2")}).process()
    assert len(out) == 2
    assert sorted(repo.rows.values()) == [50, 50]


async def test_an_attempt_that_touched_no_topics_pays_nothing():
    assert await _svc(FakeRepo([]), {}).process() == []
