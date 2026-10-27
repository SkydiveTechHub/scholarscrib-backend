from datetime import UTC, datetime

import pytest

from app.services.learning.evidence import PRIOR_OUTCOME
from app.services.learning.mastery import (
    ChannelAggregate,
    TopicAggregate,
    TopicState,
    state_from_aggregate,
)

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def test_score_is_the_unrounded_composite():
    aggregate = TopicAggregate(
        student_id="s",
        topic_id="t",
        acc=ChannelAggregate(outcome=2.0, mass=2.0, observations=2),
    )
    state = state_from_aggregate(aggregate, NOW)
    # (2 + 4 * 0.45) / (2 + 4) with the lesson channel carrying no weight
    assert state.score == pytest.approx(3.8 / 6)
    assert state.mastery == 63


def test_score_for_a_topic_with_no_evidence_is_the_prior():
    state = state_from_aggregate(TopicAggregate(student_id="s", topic_id="t"), NOW)
    assert state.score == pytest.approx(PRIOR_OUTCOME)


def test_score_default_on_a_bare_topic_state():
    assert TopicState(topic_id="t").score == PRIOR_OUTCOME
