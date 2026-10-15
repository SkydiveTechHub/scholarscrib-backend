from datetime import UTC, datetime, timedelta

from app.services.srs.analytics import (
    CardRow,
    DeckRow,
    LogRow,
    ReviewSnapshot,
    build_stats,
)

# 2026-10-14 10:00 in Lagos (UTC+1).
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def _log(card="c1", days_ago=0, rating="GOOD", hour_utc=9):
    moment = (NOW - timedelta(days=days_ago)).replace(hour=hour_utc, minute=0)
    return LogRow(card, moment, rating)


def _review(state="REVIEW", **overrides):
    data = dict(
        state=state,
        difficulty=5.0,
        stability=10.0,
        interval_days=10.0,
        lapses=0,
        due_at=NOW + timedelta(days=3),
        last_reviewed_at=NOW - timedelta(days=1),
    )
    data.update(overrides)
    return ReviewSnapshot(**data)


def _card(card_id="c1", deck="d1", review=None, authored=None, front="Q"):
    return CardRow(card_id, deck, front, authored, review)


def _stats(cards=(), logs=(), outcomes=None, decks=None):
    if decks is None:
        decks = [DeckRow("d1", "Deck")]
    return build_stats(
        now=NOW,
        decks=list(decks),
        cards=list(cards),
        logs=list(logs),
        outcomes=outcomes or {},
    )


def test_empty_student_gets_zeroes_and_nulls_not_errors():
    stats = _stats(decks=())
    assert stats["reviewsThisWeek"] == 0
    assert stats["streak"] == 0
    assert stats["measuredRetention"] is None
    assert stats["predictedRetention"] is None
    assert stats["decks"] == []
    assert stats["leechCards"] == []
    assert len(stats["activity"]) == 14


def test_activity_is_zero_filled_oldest_first_and_counts_successes():
    stats = _stats(
        logs=[_log(), _log(rating="AGAIN"), _log(days_ago=2), _log(days_ago=20)]
    )
    activity = stats["activity"]
    assert activity[-1] == {"date": "2026-10-14", "reviews": 2, "successful": 1}
    assert activity[-2] == {"date": "2026-10-13", "reviews": 0, "successful": 0}
    assert activity[-3]["reviews"] == 1
    assert activity[0]["date"] == "2026-10-01"
    assert stats["reviewsThisWeek"] == 3


def test_a_late_evening_review_counts_on_the_lagos_day():
    # 23:00 UTC on the 13th is midnight, the 14th, in Lagos.
    stats = _stats(logs=[_log(days_ago=1, hour_utc=23)])
    assert stats["activity"][-1]["reviews"] == 1


def test_streak_counts_back_from_today_and_survives_a_quiet_today():
    days = [0, 1, 2, 4]
    assert _stats(logs=[_log(days_ago=d) for d in days])["streak"] == 3
    assert _stats(logs=[_log(days_ago=d) for d in (1, 2)])["streak"] == 2
    assert _stats(logs=[_log(days_ago=3)])["streak"] == 0


def test_measured_retention_uses_the_last_30_days():
    logs = [
        _log(rating="GOOD"),
        _log(rating="EASY"),
        _log(rating="HARD"),
        _log(rating="AGAIN"),
        _log(days_ago=60, rating="AGAIN"),
    ]
    assert _stats(logs=logs)["measuredRetention"] == 0.75


def test_cards_learned_and_predicted_retention():
    cards = [
        _card("a", review=_review()),
        _card("b", review=_review(interval_days=20.0)),
        _card("c", review=_review("LEARNING", interval_days=0.01)),
        _card("d"),
    ]
    stats = _stats(cards=cards)
    assert stats["cardsLearned"] == 2
    assert 0 < stats["predictedRetention"] < 1


def test_deck_breakdown_and_totals_split_due_fresh_and_reviewed():
    due = _review(due_at=NOW - timedelta(hours=1))
    cards = [
        _card("a", review=due),
        _card("b", review=_review()),
        _card("c"),
        _card("d", review=_review("NEW", stability=0.0, due_at=None)),
    ]
    stats = _stats(cards=cards)
    deck = stats["decks"][0]
    assert (deck["total"], deck["due"], deck["fresh"], deck["reviewed"]) == (4, 1, 2, 2)
    assert deck["retention"] is not None
    assert stats["totalDue"] == 1
    assert stats["totalNew"] == 2


def test_a_deck_with_nothing_started_has_no_retention():
    stats = _stats(cards=[_card("a")])
    assert stats["decks"][0]["retention"] is None


def test_difficulty_mix_uses_review_difficulty_else_the_authored_one():
    cards = [
        _card("a", review=_review(difficulty=2.0)),
        _card("b", review=_review(difficulty=5.0)),
        _card("c", review=_review(difficulty=9.0)),
        _card("d", authored="ADVANCED"),
        _card("e"),
    ]
    mix = _stats(cards=cards)["difficultyMix"]
    assert mix == {"easy": 1, "medium": 2, "hard": 2}


def test_leeches_use_lapses_or_a_poor_success_rate_and_report_whole_percents():
    cards = [
        _card("lapsed", review=_review(lapses=4), front="Lapsed"),
        _card("poor", review=_review(lapses=1), front="Poor"),
        _card("fine", review=_review(lapses=1), front="Fine"),
        _card("young", review=_review(lapses=1), front="Young"),
    ]
    outcomes = {
        "lapsed": (5, 4),
        "poor": (10, 3),
        "fine": (10, 8),
        "young": (4, 0),
    }
    leeches = _stats(cards=cards, outcomes=outcomes)["leechCards"]
    assert [leech["cardId"] for leech in leeches] == ["lapsed", "poor"]
    assert leeches[1] == {
        "cardId": "poor",
        "prompt": "Poor",
        "deckTitle": "Deck",
        "lapses": 1,
        "reviews": 10,
        "successRate": 30,
    }


async def test_the_service_maps_database_rows_into_the_stats(monkeypatch):
    from types import SimpleNamespace as NS

    from app.services.srs import service

    deck = NS(id="d1", title="Cells")
    card = NS(
        id="c1", deck_id="d1", payload={"front": "What is a cell?"}, difficulty=None
    )
    review = NS(
        flashcard_id="c1",
        state="REVIEW",
        difficulty=5.0,
        stability=10.0,
        interval_days=10.0,
        lapses=4,
        due_at=NOW - timedelta(hours=1),
        last_reviewed_at=NOW - timedelta(days=1),
    )
    log = NS(flashcard_id="c1", reviewed_at=NOW, rating="GOOD")

    async def owned_by(_, user_id):
        return [deck]

    async def enrolled_for(_, user_id):
        return [deck]

    async def list_where(_, *criteria, **kwargs):
        return [card]

    async def for_cards(_, user_id, ids):
        return [review]

    async def since(_, user_id, moment):
        return [log]

    async def outcomes(_, user_id):
        return {"c1": (5, 4)}

    monkeypatch.setattr(service, "utcnow", lambda: NOW)
    monkeypatch.setattr(service.decks_repository, "owned_by", owned_by)
    monkeypatch.setattr(service.decks_repository, "enrolled_for", enrolled_for)
    monkeypatch.setattr(service.cards_repository, "list_where", list_where)
    monkeypatch.setattr(service.reviews_repository, "for_cards", for_cards)
    monkeypatch.setattr(service.review_logs_repository, "since", since)
    monkeypatch.setattr(service.review_logs_repository, "outcomes", outcomes)

    stats = (await service.GetFlashcardStatsService(None, "s1").process())["stats"]

    assert stats["activity"][-1]["reviews"] == 1
    assert stats["decks"] == [
        {
            "deckId": "d1",
            "title": "Cells",
            "total": 1,
            "due": 1,
            "fresh": 0,
            "reviewed": 1,
            "retention": stats["decks"][0]["retention"],
        }
    ]
    assert stats["leechCards"][0]["prompt"] == "What is a cell?"


def test_a_response_time_is_stored_clamped_and_never_negative():
    from app.services.srs.service import MAX_RESPONSE_MS, _clean_response_time

    assert _clean_response_time(None) is None
    assert _clean_response_time(-5) == 0
    assert _clean_response_time(2500) == 2500
    assert _clean_response_time(60 * 60 * 1000) == MAX_RESPONSE_MS
