"""Unit tests for topic standings bands and filters."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.learning import topic_standings as standings_mod
from app.services.learning.mastery import TopicState
from app.services.learning.topic_standings import (
    GetTopicStandingsService,
    standing_band,
)


def test_standing_band_rules():
    assert standing_band(TopicState(topic_id="t", observations=0)) is None
    assert (
        standing_band(TopicState(topic_id="t", observations=2, confidence=0.2))
        == "emerging"
    )
    assert (
        standing_band(
            TopicState(topic_id="t", observations=5, confidence=0.5, mastery=90)
        )
        == "strong"
    )
    assert (
        standing_band(
            TopicState(topic_id="t", observations=5, confidence=0.5, mastery=40)
        )
        == "weak"
    )
    assert (
        standing_band(
            TopicState(topic_id="t", observations=5, confidence=0.5, mastery=70)
        )
        == "normal"
    )


@pytest.mark.asyncio
async def test_filters_by_status_subject_and_limit(monkeypatch):
    bio = SimpleNamespace(id="bio", name="Biology", slug="biology")
    chem = SimpleNamespace(id="chem", name="Chemistry", slug="chemistry")
    topics = [
        SimpleNamespace(id="t-strong", title="Cells", slug="cells", subject_id="bio"),
        SimpleNamespace(
            id="t-weak", title="Genetics", slug="genetics", subject_id="bio"
        ),
        SimpleNamespace(
            id="t-normal", title="Bonding", slug="bonding", subject_id="chem"
        ),
        SimpleNamespace(id="t-empty", title="Unused", slug="unused", subject_id="bio"),
    ]
    states = {
        "t-strong": TopicState(
            topic_id="t-strong",
            mastery=92,
            confidence=0.6,
            observations=8,
            level="STRONG",
        ),
        "t-weak": TopicState(
            topic_id="t-weak", mastery=30, confidence=0.55, observations=6, level="WEAK"
        ),
        "t-normal": TopicState(
            topic_id="t-normal",
            mastery=68,
            confidence=0.5,
            observations=5,
            level="DEVELOPING",
        ),
        "t-empty": TopicState(topic_id="t-empty", observations=0),
    }

    monkeypatch.setattr(
        standings_mod.subjects_repository,
        "active_ordered",
        AsyncMock(return_value=[bio, chem]),
    )
    monkeypatch.setattr(
        standings_mod.topics_repository,
        "all_ordered",
        AsyncMock(return_value=topics),
    )
    monkeypatch.setattr(
        standings_mod,
        "GetTopicMasteryService",
        lambda *_a, **_k: SimpleNamespace(process=AsyncMock(return_value=states)),
    )

    all_rows = await GetTopicStandingsService(
        MagicMock(), "student-1", status="all", limit=50
    ).process()
    assert {row["topicId"] for row in all_rows["topics"]} == {
        "t-strong",
        "t-weak",
        "t-normal",
    }

    weak = await GetTopicStandingsService(
        MagicMock(), "student-1", status="weak", limit=50
    ).process()
    assert [row["topicId"] for row in weak["topics"]] == ["t-weak"]
    assert weak["topics"][0]["status"] == "weak"

    strong = await GetTopicStandingsService(
        MagicMock(), "student-1", status="strong", limit=50
    ).process()
    assert [row["topicId"] for row in strong["topics"]] == ["t-strong"]

    bio_only = await GetTopicStandingsService(
        MagicMock(),
        "student-1",
        status="all",
        subject_ids=["bio"],
        limit=50,
    ).process()
    assert {row["topicId"] for row in bio_only["topics"]} == {"t-strong", "t-weak"}

    multi = await GetTopicStandingsService(
        MagicMock(),
        "student-1",
        status="all",
        subject_ids=["bio", "chem"],
        limit=1,
    ).process()
    assert len(multi["topics"]) == 1
