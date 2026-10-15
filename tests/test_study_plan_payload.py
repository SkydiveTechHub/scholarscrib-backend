from datetime import UTC
from types import SimpleNamespace as NS

import pytest

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


def test_position_options_offer_only_the_plans_scope_in_curriculum_order():
    topics = [
        _topic("ss1-first-b", "SS1", "FIRST", 2, "Sets"),
        _topic("ss1-first-a", "SS1", "FIRST", 1),
        _topic("ss1-second", "SS1", "SECOND", 1),
        _topic("ss2-first", "SS2", "FIRST", 1),
    ]

    options = position_options(topics, "SS1", "FIRST")

    assert [o["id"] for o in options] == ["ss1-first-a", "ss1-first-b"]
    assert options[1] == {
        "id": "ss1-first-b",
        "title": "Sets",
        "scope": "SS1 First term",
    }


async def test_plan_topics_leave_out_topics_without_a_curriculum_level(monkeypatch):
    from types import SimpleNamespace as NS

    from app.services.planner import service

    def row(topic_id, level_id):
        return NS(
            id=topic_id, subject_id="maths", title=topic_id, slug=topic_id,
            order_index=1, estimated_minutes=30, waec_weight=1, jamb_weight=1,
            prerequisite_topic_id=None, curriculum_level_id=level_id,
        )

    async def for_subject(_, subject_id):
        return [row("mapped", "lv1"), row("unmapped", None), row("dangling", "gone")]

    async def by_id(_, level_id):
        return NS(class_level="SS2", term="SECOND") if level_id == "lv1" else None

    monkeypatch.setattr(service.topics_repository, "for_subject", for_subject)
    monkeypatch.setattr(service.curriculum_levels_repository, "by_id", by_id)

    topics = await service._plan_topics(None, "maths")

    assert [(t.id, t.class_level, t.term) for t in topics] == [
        ("mapped", "SS2", "SECOND")
    ]


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
        scope_class_level="SS1",
        scope_term="FIRST",
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
    assert payload["scope"] == {"classLevel": "SS1", "term": "FIRST"}
    assert payload["followsCalendar"] is True


async def test_plan_for_another_class_does_not_follow_the_calendar(monkeypatch):
    from datetime import date, datetime

    from app.services.planner import service
    from app.services.planner.term_context import TermRange, resolve_term_context

    plan = NS(
        id="p1", subject_ids=["maths"], target_exam=None, target_date=None,
        force_exam_mode=False, study_days=[1], weekday_minutes=30,
        weekend_minutes=60, planned_through=date(2026, 9, 28), outline=[],
        overload=None, created_at=datetime(2026, 9, 14, tzinfo=UTC),
        scope_class_level="SS2", scope_term="SECOND",
    )

    async def empty(*_):
        return []

    async def active_ids(_):
        return {"maths"}

    async def plan_topics(_, subject_id):
        return [
            _topic("a", "SS2", "SECOND", 1),
            _topic("b", "SS2", "SECOND", 2),
            _topic("x", "SS1", "FIRST", 1),
        ]

    monkeypatch.setattr(service.subjects_repository, "active_ids", active_ids)
    monkeypatch.setattr(service.subjects_repository, "active_ordered", empty)
    monkeypatch.setattr(service.plan_items_repository, "for_plan", empty)
    monkeypatch.setattr(service.plan_positions_repository, "for_plan", empty)
    monkeypatch.setattr(service.topics_repository, "for_ids", empty)
    monkeypatch.setattr(service, "_plan_topics", plan_topics)

    ctx = resolve_term_context(
        "2026-09-14", [TermRange("2026/2027", "FIRST", "2026-09-07", "2026-11-29")]
    )
    payload = await service.GetStudyPlanService(None, "s1", "SS1")._serialize(plan, ctx)

    assert payload["scope"] == {"classLevel": "SS2", "term": "SECOND"}
    assert payload["followsCalendar"] is False
    assert payload["calendarPositions"] == {"maths": "a"}
    assert [o["id"] for o in payload["positionOptions"]["maths"]] == ["a", "b"]


async def test_changing_scope_drops_positions_outside_it(monkeypatch):
    from app.services.planner import service

    inside = NS(topic_id="in")
    outside = NS(topic_id="out")
    removed = []

    async def for_plan(_, plan_id):
        return [inside, outside]

    async def topic_by_id(_, topic_id):
        return NS(id=topic_id, curriculum_level_id=f"lv-{topic_id}")

    async def level_by_id(_, level_id):
        return NS(class_level="SS2", term="SECOND") if level_id == "lv-in" else NS(
            class_level="SS1", term="FIRST"
        )

    async def remove(_, row):
        removed.append(row)

    monkeypatch.setattr(service.plan_positions_repository, "for_plan", for_plan)
    monkeypatch.setattr(service.plan_positions_repository, "remove", remove)
    monkeypatch.setattr(service.topics_repository, "by_id", topic_by_id)
    monkeypatch.setattr(service.curriculum_levels_repository, "by_id", level_by_id)

    plan = NS(id="p1", scope_class_level="SS2", scope_term="SECOND")
    await service._drop_positions_outside_scope(None, plan)

    assert removed == [outside]


@pytest.mark.parametrize("level", [NS(class_level="SS2", term="SECOND"), None])
async def test_position_outside_the_plans_term_is_rejected(monkeypatch, level):
    from app.api.schemas import PositionIn
    from app.core.errors import ApiError
    from app.services.planner import service

    writes = []

    async def for_plan_subject(_, plan_id, subject_id):
        return None

    async def topic_by_id(_, topic_id):
        return NS(
            id=topic_id,
            subject_id="maths",
            curriculum_level_id="lv" if level else None,
        )

    async def level_by_id(_, level_id):
        return level

    async def add(*args, **kwargs):
        writes.append(args)

    monkeypatch.setattr(
        service.plan_positions_repository, "for_plan_subject", for_plan_subject
    )
    monkeypatch.setattr(service.plan_positions_repository, "add", add)
    monkeypatch.setattr(service.topics_repository, "by_id", topic_by_id)
    monkeypatch.setattr(service.curriculum_levels_repository, "by_id", level_by_id)

    plan = NS(id="p1", scope_class_level="SS1", scope_term="FIRST")
    svc = service.SetStudyPlanPositionsService(None, "s1", "SS1", [])
    with pytest.raises(ApiError) as caught:
        await svc._apply_position(
            plan, {"maths"}, PositionIn(subjectId="maths", topicId="t9")
        )

    assert caught.value.status_code == 400
    assert caught.value.payload["error"] == "That topic is outside your plan's term"
    assert writes == []


async def test_delete_deactivates_the_active_plan_and_keeps_it(monkeypatch):
    from types import SimpleNamespace as NS

    from app.services.planner import service

    plan = NS(id="p1", is_active=True)
    locked = []

    async def active(_, student_id):
        return plan

    async def lock(_, *conditions):
        locked.append(conditions)

    monkeypatch.setattr(service.plans_repository, "active", active)
    monkeypatch.setattr(service.plans_repository, "lock", lock)

    result = await service.DeleteStudyPlanService(None, "s1").process()

    assert result == {"ok": True}
    assert plan.is_active is False
    assert len(locked) == 1


async def test_delete_without_an_active_plan_is_a_404(monkeypatch):
    import pytest

    from app.core.errors import ApiError
    from app.services.planner import service

    async def active(_, student_id):
        return None

    monkeypatch.setattr(service.plans_repository, "active", active)

    with pytest.raises(ApiError) as raised:
        await service.DeleteStudyPlanService(None, "s1").process()

    assert raised.value.status_code == 404


async def _follows_in_holiday(monkeypatch, scope_term):
    from datetime import date, datetime

    from app.services.planner import service
    from app.services.planner.term_context import TermRange, resolve_term_context

    plan = NS(
        id="p1", subject_ids=["maths"], target_exam=None, target_date=None,
        force_exam_mode=False, study_days=[1], weekday_minutes=30,
        weekend_minutes=60, planned_through=date(2026, 12, 20), outline=[],
        overload=None, created_at=datetime(2026, 9, 14, tzinfo=UTC),
        scope_class_level="SS1", scope_term=scope_term,
    )

    async def empty(*_):
        return []

    async def active_ids(_):
        return {"maths"}

    async def plan_topics(_, subject_id):
        return [
            _topic("a", "SS1", "FIRST", 1),
            _topic("b", "SS1", "FIRST", 2),
            _topic("c", "SS1", "SECOND", 1),
        ]

    monkeypatch.setattr(service.subjects_repository, "active_ids", active_ids)
    monkeypatch.setattr(service.subjects_repository, "active_ordered", empty)
    monkeypatch.setattr(service.plan_items_repository, "for_plan", empty)
    monkeypatch.setattr(service.plan_positions_repository, "for_plan", empty)
    monkeypatch.setattr(service.topics_repository, "for_ids", empty)
    monkeypatch.setattr(service, "_plan_topics", plan_topics)

    ctx = resolve_term_context(
        "2026-12-20",
        [
            TermRange("2026/2027", "FIRST", "2026-09-07", "2026-12-15"),
            TermRange("2026/2027", "SECOND", "2027-01-06", "2027-04-10"),
        ],
    )
    assert ctx["kind"] == "holiday"
    return await service.GetStudyPlanService(None, "s1", "SS1")._serialize(plan, ctx)


async def test_holiday_follows_the_calendar_for_the_term_just_ended(monkeypatch):
    payload = await _follows_in_holiday(monkeypatch, "FIRST")
    assert payload["calendarPositions"] == {"maths": "b"}  # last topic
    assert payload["followsCalendar"] is True


async def test_holiday_does_not_follow_the_calendar_for_another_term(monkeypatch):
    payload = await _follows_in_holiday(monkeypatch, "SECOND")
    assert payload["calendarPositions"] == {"maths": "c"}
    assert payload["followsCalendar"] is False
