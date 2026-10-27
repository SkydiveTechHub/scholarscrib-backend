"""Today's session: one short, ordered queue of study actions.

Everything here is pure. The dashboard service gathers the inputs and calls
`compose_session` at most once per Lagos day; `session_store.py` keeps the
result so the list does not reshuffle while the student works.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.core.timeutil import as_utc
from app.services.learning.evidence import (
    CONFIDENCE_FLOOR,
    DECAY_RETENTION,
    DONE_QUESTIONS,
    DUE_FLOOR,
    LEARN_MIN_MINUTES,
    PRACTICE_MINUTES,
    RETRIEVE_MAX_MINUTES,
    RETRIEVE_MIN_MINUTES,
    RETRIEVE_NO_CARDS_MINUTES,
    SESSION_MAX_ITEMS,
    SESSION_MINUTES,
    TARGET,
    WEAK_MASTERY,
    WEAK_PRACTICE_BOOST,
)
from app.services.learning.mastery import (
    TopicState,
    evidence_level,
    is_mastered,
    topic_retention,
)

PLAN_KINDS = {
    "LESSON": "LEARN",
    "PRACTICE": "PRACTICE",
    "REVISION": "RETRIEVE",
    "MOCK_EXAM": "MOCK",
    "PAST_QUESTIONS": "PAST",
}
PLAN_LABELS = {"MOCK_EXAM": "Mock exam", "PAST_QUESTIONS": "Past questions"}


@dataclass
class SessionInputs:
    topics: Sequence
    states: dict[str, TopicState]
    leverage: dict[str, float]
    dependents: dict[str, int]
    due_cards: dict[str, int]
    card_retention: dict[str, tuple[float, int]]
    cadence_due: set[str]
    plan_items: Sequence[dict]
    continue_lesson: tuple[str, str] | None
    recent: dict[str, list[bool]]
    now: datetime
    budget: int = SESSION_MINUTES


@dataclass(frozen=True)
class DayEvent:
    topic_id: str | None
    kind: str


def exam_weight(topic) -> float:
    return (topic.waec_weight or 0) + (topic.jamb_weight or 0)


def retrieval_priority(
    decay: float, weight: float, blocked: int, max_blocked: int
) -> float:
    urgency = max(decay, DUE_FLOOR)
    blocked_factor = 1 + (blocked / max_blocked if max_blocked else 0)
    return urgency * max(weight, 1.0) * blocked_factor


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _ago(days: float) -> str:
    if days < 1:
        return "today"
    if days < 7:
        count = max(1, round(days))
        return f"{count} day{'' if count == 1 else 's'} ago"
    if days < 45:
        count = round(days / 7)
        return f"{count} week{'' if count == 1 else 's'} ago"
    count = max(1, round(days / 30))
    return f"{count} month{'' if count == 1 else 's'} ago"


def _evidence(state: TopicState | None) -> dict:
    if state is None:
        return {
            "confidence": 0.0,
            "accObservations": 0,
            "lessonObservations": 0,
            "srsObservations": 0,
            "lastStudy": None,
        }
    return {
        "confidence": state.confidence,
        "accObservations": state.acc_observations,
        "lessonObservations": state.lesson_observations,
        "srsObservations": state.srs_observations,
        "lastStudy": state.last_effort_at.isoformat() if state.last_effort_at else None,
    }


def _item(
    *,
    item_id: str,
    kind: str,
    source: str,
    topic,
    state: TopicState | None,
    minutes: int,
    reason: str,
    **extra,
) -> dict:
    item = {
        "id": item_id,
        "kind": kind,
        "source": source,
        "topicId": topic.id if topic is not None else None,
        "subjectId": topic.subject_id if topic is not None else None,
        "title": topic.title if topic is not None else "",
        "slug": topic.slug if topic is not None else None,
        "minutes": minutes,
        "reason": reason,
        "doneAt": None,
        "lessonId": None,
        "dueCards": 0,
        "planItemId": None,
        "activityType": None,
        "mastery": state.mastery if state is not None else None,
        "retention": None,
        "retentionSource": None,
        "evidenceLevel": evidence_level(state) if state is not None else "NONE",
        **_evidence(state),
    }
    item.update(extra)
    return item


def _plan_entry(plan_item: dict, topics: dict, states: dict) -> dict:
    topic = topics.get(plan_item["topicId"]) if plan_item.get("topicId") else None
    state = states.get(topic.id) if topic is not None else None
    activity = plan_item["activityType"]
    title = topic.title if topic is not None else PLAN_LABELS.get(activity, "")
    return _item(
        item_id=f"plan:{plan_item['id']}",
        kind=PLAN_KINDS.get(activity, "PRACTICE"),
        source="PLAN",
        topic=topic,
        state=state,
        minutes=plan_item["durationMinutes"],
        reason="From your study plan",
        subjectId=plan_item.get("subjectId"),
        title=title or activity.replace("_", " ").title(),
        planItemId=plan_item["id"],
        activityType=activity,
    )


def _pick_retrieval(
    inputs: SessionInputs, used: set[str], remaining: int
) -> dict | None:
    if remaining < RETRIEVE_MIN_MINUTES:
        return None
    max_blocked = max(inputs.dependents.values(), default=0)
    best = None
    for topic in inputs.topics:
        state = inputs.states.get(topic.id)
        if topic.id in used or state is None or state.last_effort_at is None:
            continue
        value, source = topic_retention(state, inputs.card_retention.get(topic.id))
        faded = value is not None and value < DECAY_RETENTION
        cards = inputs.due_cards.get(topic.id, 0)
        cadence = topic.id in inputs.cadence_due
        if not (faded or cards > 0 or cadence):
            continue
        decay = 0.0 if value is None else max(0.0, DECAY_RETENTION - value)
        priority = retrieval_priority(
            decay,
            exam_weight(topic),
            inputs.dependents.get(topic.id, 0),
            max_blocked,
        )
        key = (-priority, -cards, topic.order_index)
        if best is None or key < best[0]:
            best = (key, topic, state, value, source, faded, cards)
    if best is None:
        return None
    _key, topic, state, value, source, faded, cards = best
    last_effort_at = state.last_effort_at
    if last_effort_at is None:
        return None
    wanted = (
        min(RETRIEVE_MAX_MINUTES, math.ceil(1 + cards * 0.5))
        if cards > 0
        else RETRIEVE_NO_CARDS_MINUTES
    )
    minutes = min(wanted, remaining)
    ago = _ago((as_utc(inputs.now) - as_utc(last_effort_at)).total_seconds() / 86400)
    if faded and source == "CARDS" and value is not None:
        reason = f"Retention about {round(value * 100)}% — last studied {ago}"
    elif faded:
        reason = f"Probably fading — last studied {ago}"
    elif cards > 0:
        reason = f"{_plural(cards, 'flashcard')} due"
    else:
        reason = "Scheduled revision is due"
    return _item(
        item_id=f"retrieve:{topic.id}",
        kind="RETRIEVE",
        source="ENGINE",
        topic=topic,
        state=state,
        minutes=minutes,
        reason=reason,
        dueCards=cards,
        retention=value,
        retentionSource=source,
    )


def _practice_reason(inputs: SessionInputs, topic, state: TopicState) -> str:
    parts = []
    recent = inputs.recent.get(topic.id, [])
    if len(recent) >= 3:
        parts.append(
            f"{sum(1 for right in recent if right)} of your last "
            f"{len(recent)} answers here were right"
        )
    elif state.acc_observations > 0:
        parts.append(f"{_plural(state.acc_observations, 'question')} answered so far")
    else:
        parts.append("Started — needs more practice")
    unlocks = inputs.dependents.get(topic.id, 0)
    if unlocks >= 1:
        parts.append(f"unlocks {_plural(unlocks, 'topic')}")
    weight = round(exam_weight(topic))
    if weight > 0:
        parts.append(f"~{weight} exam weight")
    return " · ".join(parts)


def _pick_practice(
    inputs: SessionInputs, used: set[str], remaining: int
) -> dict | None:
    if remaining < PRACTICE_MINUTES:
        return None
    best = None
    for topic in inputs.topics:
        state = inputs.states.get(topic.id)
        if topic.id in used or state is None:
            continue
        if not state.available or is_mastered(state):
            continue
        if evidence_level(state) == "NONE":
            continue
        urgency = (TARGET - state.mastery) / TARGET
        value = (
            urgency
            * (1 + inputs.leverage.get(topic.id, 0.0))
            * max(exam_weight(topic), 1.0)
        )
        if state.confidence >= CONFIDENCE_FLOOR and state.mastery < WEAK_MASTERY:
            value *= WEAK_PRACTICE_BOOST
        key = (-value, topic.order_index)
        if best is None or key < best[0]:
            best = (key, topic, state)
    if best is None:
        return None
    _key, topic, state = best
    return _item(
        item_id=f"practice:{topic.id}",
        kind="PRACTICE",
        source="ENGINE",
        topic=topic,
        state=state,
        minutes=PRACTICE_MINUTES,
        reason=_practice_reason(inputs, topic, state),
    )


def _pick_new(inputs: SessionInputs, used: set[str], remaining: int) -> dict | None:
    if remaining < LEARN_MIN_MINUTES:
        return None
    by_id = {topic.id: topic for topic in inputs.topics}
    if inputs.continue_lesson is not None:
        topic_id, lesson_id = inputs.continue_lesson
        topic = by_id.get(topic_id)
        state = inputs.states.get(topic_id)
        if topic is not None and state is not None and topic_id not in used:
            return _item(
                item_id=f"continue:{topic.id}",
                kind="CONTINUE",
                source="ENGINE",
                topic=topic,
                state=state,
                minutes=min(
                    remaining, max(LEARN_MIN_MINUTES, topic.estimated_minutes or 10)
                ),
                reason="Continue where you left off",
                lessonId=lesson_id,
            )
    studied_subjects = {
        topic.subject_id
        for topic in inputs.topics
        if (state := inputs.states.get(topic.id)) is not None and state.observations > 0
    }
    candidates = []
    for topic in inputs.topics:
        state = inputs.states.get(topic.id)
        if topic.id in used or state is None or not state.available:
            continue
        if evidence_level(state) != "NONE":
            continue
        candidates.append(
            (
                0 if topic.subject_id in studied_subjects else 1,
                topic.order_index,
                topic.title,
                topic,
                state,
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda found: found[:3])
    _a, _b, _c, topic, state = candidates[0]
    reason = "New topic — you're ready for it"
    prereq = (
        by_id.get(topic.prerequisite_topic_id) if topic.prerequisite_topic_id else None
    )
    prereq_state = inputs.states.get(prereq.id) if prereq is not None else None
    if (
        prereq is not None
        and prereq_state is not None
        and evidence_level(prereq_state) == "ENOUGH"
        and prereq_state.mastery >= TARGET
    ):
        reason = (
            f"{prereq.title} is solid ({prereq_state.mastery}%) — this is the next step"
        )
    return _item(
        item_id=f"learn:{topic.id}",
        kind="LEARN",
        source="ENGINE",
        topic=topic,
        state=state,
        minutes=min(remaining, max(LEARN_MIN_MINUTES, topic.estimated_minutes or 10)),
        reason=reason,
    )


def compose_session(inputs: SessionInputs) -> list[dict]:
    topics = {topic.id: topic for topic in inputs.topics}
    items: list[dict] = []
    used: set[str] = set()
    spent = 0
    for plan_item in inputs.plan_items:
        if len(items) >= SESSION_MAX_ITEMS:
            break
        entry = _plan_entry(plan_item, topics, inputs.states)
        items.append(entry)
        if entry["topicId"]:
            used.add(entry["topicId"])
        if plan_item["status"] != "COMPLETED":
            spent += entry["minutes"]
    # The unfinished lesson's topic is held back from the first two slots so the
    # "Continue" item is not pre-empted by a practice block on the same topic.
    reserved = {inputs.continue_lesson[0]} if inputs.continue_lesson else set()
    for pick in (_pick_retrieval, _pick_practice, _pick_new):
        if len(items) >= SESSION_MAX_ITEMS:
            break
        remaining = inputs.budget - spent
        if remaining <= 0:
            break
        blocked = used if pick is _pick_new else used | reserved
        found = pick(inputs, blocked, remaining)
        if found is None:
            continue
        items.append(found)
        used.add(found["topicId"])
        spent += found["minutes"]
    return items


def item_is_done(
    item: dict, events: Sequence[DayEvent], plan_status: Mapping[str, str]
) -> bool:
    if item["source"] == "PLAN":
        return plan_status.get(item["planItemId"]) == "COMPLETED"
    mine = [event for event in events if event.topic_id == item["topicId"]]
    answered = sum(1 for event in mine if event.kind == "QUESTION_ANSWERED")
    reviewed = sum(1 for event in mine if event.kind == "CARD_REVIEWED")
    lessons = sum(
        1
        for event in mine
        if event.kind in {"LESSON_BLOCK_COMPLETED", "LESSON_COMPLETED"}
    )
    kind = item["kind"]
    if kind == "RETRIEVE":
        due = item.get("dueCards", 0)
        target = min(DONE_QUESTIONS, due) if due > 0 else DONE_QUESTIONS
        return answered + reviewed >= target
    if kind == "PRACTICE":
        return answered >= DONE_QUESTIONS
    if kind in {"LEARN", "CONTINUE"}:
        return lessons >= 1
    return False


def reconcile_plan(items: list[dict], plan_items: Sequence[dict]) -> bool:
    """Re-sync stored PLAN items with today's live plan items; True if changed.

    A replan deletes pending plan items and re-creates them with new ids, so a
    stored `planItemId` can point at nothing. Such an item is re-pointed at an
    unclaimed live item for the same topic and activity (or subject, for items
    with no topic), and dropped when none is left. Finished items stay as they
    are.
    """
    live_ids = {plan_item["id"] for plan_item in plan_items}
    claimed = {
        item["planItemId"]
        for item in items
        if item["source"] == "PLAN" and item["planItemId"] in live_ids
    }
    changed = False
    kept: list[dict] = []
    for item in items:
        if (
            item["source"] != "PLAN"
            or item["planItemId"] in live_ids
            or item.get("doneAt")
        ):
            kept.append(item)
            continue
        match = next(
            (
                plan_item
                for plan_item in plan_items
                if plan_item["id"] not in claimed
                and plan_item["activityType"] == item.get("activityType")
                and plan_item.get("topicId") == item["topicId"]
                and (item["topicId"] or plan_item.get("subjectId") == item["subjectId"])
            ),
            None,
        )
        changed = True
        if match is None:
            continue
        claimed.add(match["id"])
        item["planItemId"] = match["id"]
        item["id"] = f"plan:{match['id']}"
        kept.append(item)
    items[:] = kept
    return changed


def mark_done(
    items: list[dict],
    events: Sequence[DayEvent],
    plan_status: Mapping[str, str],
    now: datetime,
) -> bool:
    """Stamp `doneAt` on newly finished items; never clears one. True if changed."""
    changed = False
    for item in items:
        if item.get("doneAt"):
            continue
        if item_is_done(item, events, plan_status):
            item["doneAt"] = as_utc(now).isoformat()
            changed = True
    return changed


def drop_inactive(items: list[dict], active_subject_ids: set[str]) -> list[dict]:
    return [
        item
        for item in items
        if not item.get("subjectId") or item["subjectId"] in active_subject_ids
    ]


def summarise(items: list[dict]) -> dict:
    return {
        "minutes": sum(item["minutes"] for item in items),
        "done": sum(1 for item in items if item.get("doneAt")),
        "total": len(items),
        "items": items,
    }
