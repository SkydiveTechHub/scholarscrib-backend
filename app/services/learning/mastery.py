from dataclasses import dataclass, field
from datetime import datetime

from app.core.timeutil import as_utc
from app.services.learning.evidence import (
    CARD_OUTCOMES,
    CHANNEL_WEIGHTS,
    CONFIDENCE_FLOOR,
    DECAY_RETENTION,
    GAP_RETENTION,
    OBSERVATION_FLOOR,
    PRIOR_STRENGTH,
    SCORING_VERSION,
    STRONG_MASTERY,
    TARGET,
    WEAK_MASTERY,
    channel_score,
    mastery_level,
    question_outcome,
    recency_weight,
    response_weight,
    retention,
)


@dataclass
class ChannelAggregate:
    outcome: float = 0.0
    mass: float = 0.0
    observations: int = 0


@dataclass
class TopicAggregate:
    student_id: str
    topic_id: str
    acc: ChannelAggregate = field(default_factory=ChannelAggregate)
    lesson: ChannelAggregate = field(default_factory=ChannelAggregate)
    srs: ChannelAggregate = field(default_factory=ChannelAggregate)
    abandon_count: int = 0
    decay_anchor: datetime | None = None
    last_effort_at: datetime | None = None
    cursor_seq: int = 0
    scoring_version: int = SCORING_VERSION


@dataclass
class LearningEventView:
    seq: int
    topic_id: str | None
    kind: str
    correct: bool | None = None
    score: float | None = None
    difficulty: str | None = None
    seconds: float | None = None
    occurred_at: datetime | None = None
    rating: str | None = None


EFFORT_KINDS = {
    "QUESTION_ANSWERED",
    "LESSON_BLOCK_COMPLETED",
    "LESSON_COMPLETED",
    "CARD_REVIEWED",
}


def decay_channel(channel: ChannelAggregate, factor: float) -> None:
    channel.outcome *= factor
    channel.mass *= factor


def decay_to(aggregate: TopicAggregate, now: datetime) -> None:
    if aggregate.decay_anchor is None:
        aggregate.decay_anchor = now
        return
    age_days = (as_utc(now) - as_utc(aggregate.decay_anchor)).total_seconds() / 86400
    if age_days <= 0:
        aggregate.decay_anchor = now
        return
    factor = recency_weight(age_days)
    decay_channel(aggregate.acc, factor)
    decay_channel(aggregate.lesson, factor)
    decay_channel(aggregate.srs, factor)
    aggregate.decay_anchor = now


def apply_event(
    aggregate: TopicAggregate, event: LearningEventView, now: datetime
) -> None:
    occurred = as_utc(event.occurred_at or now)
    age_days = max(0.0, (as_utc(now) - occurred).total_seconds() / 86400)
    kind = event.kind
    if kind == "QUIZ_ABANDONED":
        aggregate.abandon_count += 1
        return
    if kind == "PRETEST_PASSED":
        return
    if kind == "QUESTION_ANSWERED":
        weight = response_weight(age_days, event.seconds)
        outcome = question_outcome(event.difficulty, bool(event.correct))
        aggregate.acc.outcome += weight * outcome
        aggregate.acc.mass += weight
        aggregate.acc.observations += 1
    elif kind in {"LESSON_BLOCK_COMPLETED", "LESSON_COMPLETED"}:
        if event.score is None:
            return
        weight = recency_weight(age_days)
        aggregate.lesson.outcome += weight * event.score
        aggregate.lesson.mass += weight
        aggregate.lesson.observations += 1
    elif kind == "CARD_REVIEWED":
        item = (
            event.score
            if event.score is not None
            else CARD_OUTCOMES.get(event.rating or "", 0.0)
        )
        weight = recency_weight(age_days)
        aggregate.srs.outcome += weight * item
        aggregate.srs.mass += weight
        aggregate.srs.observations += 1
    else:
        return
    if kind in EFFORT_KINDS and (
        aggregate.last_effort_at is None or occurred > as_utc(aggregate.last_effort_at)
    ):
        aggregate.last_effort_at = occurred
    aggregate.cursor_seq = max(aggregate.cursor_seq, event.seq)


def fold_events(
    aggregate: TopicAggregate,
    events: list[LearningEventView],
    now: datetime,
) -> TopicAggregate:
    if aggregate.scoring_version != SCORING_VERSION:
        aggregate.acc = ChannelAggregate()
        aggregate.lesson = ChannelAggregate()
        aggregate.srs = ChannelAggregate()
        aggregate.abandon_count = 0
        aggregate.cursor_seq = 0
        aggregate.decay_anchor = None
        aggregate.last_effort_at = None
        aggregate.scoring_version = SCORING_VERSION
    decay_to(aggregate, now)
    for event in events:
        if event.seq > aggregate.cursor_seq:
            apply_event(aggregate, event, now)
    return aggregate


@dataclass
class TopicState:
    topic_id: str
    mastery: int = 0
    confidence: float = 0.0
    retention: float | None = None
    observations: int = 0
    abandon_count: int = 0
    last_effort_at: datetime | None = None
    level: str = "WEAK"
    stability_days: int = 5
    available: bool = True


def state_from_aggregate(aggregate: TopicAggregate, now: datetime) -> TopicState:
    channels = {
        "acc": aggregate.acc,
        "lesson": aggregate.lesson,
        "srs": aggregate.srs,
    }
    weighted = []
    total_mass = 0.0
    observations = 0
    for name, channel in channels.items():
        score, confidence = channel_score(channel.outcome, channel.mass)
        weighted.append((score, CHANNEL_WEIGHTS[name] * confidence, channel.mass))
        total_mass += channel.mass
        observations += channel.observations
    weight_sum = sum(item[1] for item in weighted)
    if weight_sum <= 0:
        composite = channel_score(0, 0)[0]
    else:
        composite = (
            sum(score * weight for score, weight, _mass in weighted) / weight_sum
        )
    mastery = round(min(100, max(0, composite * 100)))
    confidence = total_mass / (total_mass + PRIOR_STRENGTH)
    level, stability = mastery_level(mastery)
    topic_retention = None
    if aggregate.last_effort_at is not None:
        days = max(
            0.0,
            (as_utc(now) - as_utc(aggregate.last_effort_at)).total_seconds() / 86400,
        )
        topic_retention = retention(days, stability)
    return TopicState(
        topic_id=aggregate.topic_id,
        mastery=mastery,
        confidence=confidence,
        retention=topic_retention,
        observations=observations,
        abandon_count=aggregate.abandon_count,
        last_effort_at=aggregate.last_effort_at,
        level=level,
        stability_days=stability,
    )


TopicStateMap = dict[str, TopicState]


def graph_colour(state: TopicState) -> str:
    if not state.available:
        return "LOCKED"
    if (
        state.retention is not None
        and state.retention < DECAY_RETENTION
        and state.observations > 0
    ):
        return "DECAYED"
    if state.mastery >= TARGET:
        return "MASTERED"
    if state.observations > 0:
        return "STARTED"
    return "READY"


def classify_gap(state: TopicState, dependents_unmastered: int) -> str | None:
    if state.confidence >= CONFIDENCE_FLOOR and state.mastery < WEAK_MASTERY:
        return "WEAK"
    if (
        state.observations >= OBSERVATION_FLOOR
        and state.retention is not None
        and state.retention < GAP_RETENTION
    ):
        return "DECAYED"
    if not state.available and dependents_unmastered >= 2:
        return "BOTTLENECK"
    if state.abandon_count >= 2:
        return "ABANDONED"
    if state.available:
        return "UNTOUCHED"
    return None


def sort_gaps(items: list[dict]) -> list[dict]:
    def key(item: dict) -> tuple:
        abandoned = 1 if item["category"] == "ABANDONED" else 0
        return (abandoned, -item.get("bottleneckScore", 0), item.get("mastery", 0))

    return sorted(items, key=key)


def recommendation_score(
    state: TopicState,
    leverage: float,
    available: bool,
    now: datetime,
) -> float:
    urgency = max(0.0, (TARGET - state.mastery) / TARGET)
    decay = 0.0 if state.retention is None else max(0.0, 1 - state.retention)
    readiness = 1.0 if available else 0.0
    freshness = 1.0
    if state.last_effort_at is not None:
        age_days = (as_utc(now) - as_utc(state.last_effort_at)).total_seconds() / 86400
        if age_days < 1:
            freshness = 0.5
    return (
        0.30 * urgency
        + 0.30 * leverage
        + 0.20 * decay
        + 0.10 * readiness
        + 0.10 * freshness
    )


def recommend(
    states: list[TopicState], leverage: dict[str, float], now: datetime, k: int = 3
) -> list[TopicState]:
    eligible = [
        state for state in states if state.available and state.mastery < STRONG_MASTERY
    ]
    if not eligible:
        eligible = [
            state
            for state in states
            if state.mastery >= TARGET
            and state.retention is not None
            and state.retention < DECAY_RETENTION
        ]
    ranked = sorted(
        eligible,
        key=lambda state: recommendation_score(
            state, leverage.get(state.topic_id, 0.0), state.available, now
        ),
        reverse=True,
    )
    return ranked[:k]
