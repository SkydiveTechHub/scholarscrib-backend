from datetime import UTC

from app.services.planner.service import position_options
from app.services.planner.topics import PlanTopic


def _topic(topic_id, class_level, term, order_index, title=None):
    return PlanTopic(
        id=topic_id,
        subject_id="maths",
        title=title or topic_id,
        slug=topic_id,
        order_index=order_index,
        estimated_minutes=30,
        waec_weight=1,
        jamb_weight=1,
        prerequisite_topic_id=None,
        class_level=class_level,
        term=term,
    )


def test_position_options_offer_topics_up_to_the_class_in_curriculum_order():
    topics = [
        _topic("ss2-first", "SS2", "FIRST", 1, "Matrices"),
        _topic("ss3-first", "SS3", "FIRST", 1),
        _topic("ss1-second", "SS1", "SECOND", 1, "Sets"),
        _topic("ss1-first-b", "SS1", "FIRST", 2),
        _topic("ss1-first-a", "SS1", "FIRST", 1),
    ]

    options = position_options(topics, "SS2")

    assert [o["id"] for o in options] == [
        "ss1-first-a",
        "ss1-first-b",
        "ss1-second",
        "ss2-first",
    ]
    assert options[2] == {
        "id": "ss1-second",
        "title": "Sets",
        "scope": "SS1 Second term",
    }


async def test_plan_payload_carries_every_field_the_page_reads(monkeypatch):
    from datetime import date, datetime
    from types import SimpleNamespace as NS

    from app.services.planner import service
    from app.services.planner.term_context import TermRange, resolve_term_context

    maths = NS(id="maths", name="Mathematics", code="MTH", slug="mathematics")
    topic_row = NS(id="t1", title="Sets", slug="sets")
    item = NS(
        id="i1",
        date=date(2026, 9, 15),
        subject_id="maths",
        topic_id="t1",
        activity_type="LESSON",
        duration_minutes=30,
        status="PENDING",
        notes=None,
        completed_at=None,
        completion_source=None,
        carried_from_date=date(2026, 9, 10),
    )
    plan = NS(
        id="p1",
        subject_ids=["maths"],
        target_exam=None,
        target_date=None,
        force_exam_mode=False,
        study_days=[1, 2],
        weekday_minutes=30,
        weekend_minutes=60,
        planned_through=date(2026, 9, 28),
        outline=[],
        overload=None,
        created_at=datetime(2026, 9, 14, tzinfo=UTC),
    )

    async def active_ids(_):
        return {"maths"}

    async def active_ordered(_):
        return [maths]

    async def for_plan_items(_, plan_id):
        return [item]

    async def for_plan_positions(_, plan_id):
        return [NS(subject_id="maths", topic_id="t1")]

    async def for_ids(_, ids):
        return [topic_row]

    async def plan_topics(_, subject_id):
        return [_topic("t1", "SS1", "FIRST", 1, "Sets")]

    monkeypatch.setattr(service.subjects_repository, "active_ids", active_ids)
    monkeypatch.setattr(service.subjects_repository, "active_ordered", active_ordered)
    monkeypatch.setattr(service.plan_items_repository, "for_plan", for_plan_items)
    monkeypatch.setattr(
        service.plan_positions_repository, "for_plan", for_plan_positions
    )
    monkeypatch.setattr(service.topics_repository, "for_ids", for_ids)
    monkeypatch.setattr(service, "_plan_topics", plan_topics)

    ctx = resolve_term_context(
        "2026-09-14", [TermRange("2026/2027", "FIRST", "2026-09-07", "2026-11-29")]
    )
    payload = await service.GetStudyPlanService(None, "s1", "SS1")._serialize(plan, ctx)

    assert payload["mode"] == "TERM"
    assert payload["runwayStart"] is None
    assert payload["positions"] == {"maths": "t1"}
    assert payload["calendarPositions"] == {"maths": "t1"}
    assert payload["positionOptions"] == {
        "maths": [{"id": "t1", "title": "Sets", "scope": "SS1 First term"}]
    }
    assert payload["items"][0]["subject"] == {
        "name": "Mathematics",
        "code": "MTH",
        "slug": "mathematics",
    }
    assert payload["items"][0]["topicTitle"] == "Sets"
    assert payload["items"][0]["topicSlug"] == "sets"
    assert payload["items"][0]["carriedFrom"] == "2026-09-10"
