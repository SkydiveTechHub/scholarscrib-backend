from app.services.provider import mapping
from app.services.provider.aloc import AlocProvider
from app.services.provider.mapping import (
    difficulty_from_score,
    exam_type_for,
    map_item,
    map_topic,
    subject_slug_candidates,
)

# The item ALOC returned for JAMB Mathematics 2001, question 1.
ITEM = {
    "id": "078a9ffc-d5ae-4d4b-bedd-a1b8fcc54d35",
    "text": "Evaluate 21.05347 - 1.6324 x 0.43 to 3 decimal places",
    "options": {"A": "20.98", "B": "20.351", "C": "20.981", "D": "20.352"},
    "correctAnswer": "D",
    "examType": "jamb",
    "subject": "mathematics",
    "year": 2001,
    "section": None,
    "imageUrl": None,
    "questionNumber": 1,
    "category": "others",
    "metadata": {
        "topic": "number-theory",
        "subtopic": "fractions-decimals",
        "difficultyScore": 2,
        "estimatedTime": 60,
        "classificationConfidence": 0.9,
        "needsReview": False,
    },
}


def _map(item=ITEM, *, topics=frozenset(), exam="JAMB", year=2001):
    return map_item(
        item,
        AlocProvider().normalize(item),
        subject_slug="mathematics",
        expected_exam=exam,
        expected_year=year,
        topic_slugs=set(topics),
    )


def test_maps_the_real_item():
    mapped = _map()
    assert mapped.ok
    assert (mapped.exam_type, mapped.exam_year) == ("JAMB", 2001)
    assert mapped.difficulty == "INTERMEDIATE"
    assert mapped.time_estimate_seconds == 60
    assert mapped.question_number == 1
    # number-theory is not one of our topics, and no reviewed mapping exists.
    assert mapped.topic_slug is None


def test_rejects_a_row_from_another_year_rather_than_rewriting_it():
    mapped = _map(year=2002)
    assert not mapped.ok
    assert "drawn for 2002 but the item says 2001" in mapped.rejection_reasons


def test_rejects_a_row_from_another_exam():
    mapped = _map(exam="WAEC")
    assert not mapped.ok


def test_rejects_an_answer_that_is_not_an_option():
    mapped = _map({**ITEM, "correctAnswer": "E"})
    assert not mapped.ok


def test_post_utme_is_not_recorded():
    assert exam_type_for("post_utme") is None
    assert exam_type_for("jamb") == "JAMB"
    assert exam_type_for("WAEC") == "WAEC"


def test_difficulty_follows_alocs_three_point_scale():
    assert [difficulty_from_score(s) for s in (1, 2, 3)] == [
        "BASIC",
        "INTERMEDIATE",
        "ADVANCED",
    ]
    # Off-scale values clamp rather than guess.
    assert difficulty_from_score(0) == "BASIC"
    assert difficulty_from_score(5) == "ADVANCED"
    assert difficulty_from_score(None) == "INTERMEDIATE"
    assert difficulty_from_score(True) == "INTERMEDIATE"


def test_topic_needs_confidence_and_no_review_flag():
    meta = {**ITEM["metadata"], "topic": "probability", "subtopic": ""}
    assert map_topic("mathematics", meta, {"probability"}) == "probability"
    low = {**meta, "classificationConfidence": 0.5}
    assert map_topic("mathematics", low, {"probability"}) is None
    flagged = {**meta, "needsReview": True}
    assert map_topic("mathematics", flagged, {"probability"}) is None
    missing = {k: v for k, v in meta.items() if k != "classificationConfidence"}
    assert map_topic("mathematics", missing, {"probability"}) is None


def test_reviewed_table_wins_and_subtopic_is_most_specific(monkeypatch):
    monkeypatch.setattr(
        mapping,
        "TOPIC_MAP",
        {
            "mathematics": {
                "number-theory/fractions-decimals": "indices-and-logarithms",
                "number-theory": "number-bases",
            }
        },
    )
    topics = {"indices-and-logarithms", "number-bases"}
    assert (
        map_topic("mathematics", ITEM["metadata"], topics) == "indices-and-logarithms"
    )
    other = {**ITEM["metadata"], "subtopic": "something-else"}
    assert map_topic("mathematics", other, topics) == "number-bases"
    # A table entry naming a topic we don't have is ignored, never invented.
    assert map_topic("mathematics", ITEM["metadata"], set()) is None


def test_subject_resolution_uses_alias_then_alocs_own_names():
    assert subject_slug_candidates("english")[0] == "english-language"
    assert subject_slug_candidates("mathematics") == ["mathematics"]
    found = subject_slug_candidates(
        "englishlit",
        {"displayName": "Literature in English", "aliases": ["lit"]},
    )
    assert "literature-in-english" in found


def _curriculum_slugs() -> dict[str, set[str]]:
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "scripts"
    codes = {
        mapping.slugify(s["name"]): s["code"]
        for s in json.loads((root / "subject.json").read_text())
    }
    curriculum = json.loads((root / "curriculum.json").read_text())
    return {
        slug: {
            mapping.slugify(topic["title"])
            for level in curriculum.get(code, [])
            for topic in level["topics"]
        }
        for slug, code in codes.items()
    }


def test_every_mapped_topic_exists_in_our_curriculum():
    ours = _curriculum_slugs()
    missing = [
        (subject, key, target)
        for subject, table in mapping.TOPIC_MAP.items()
        for key, target in table.items()
        if target not in ours.get(subject, set())
    ]
    assert missing == []


def test_exact_subtopic_beats_a_broad_topic_entry():
    ours = _curriculum_slugs()
    meta = {"classificationConfidence": 0.95}
    # A subtopic identical to one of our slugs wins over the broad bucket.
    assert (
        map_topic(
            "chemistry",
            {**meta, "topic": "inorganic-chemistry", "subtopic": "electrolysis"},
            ours["chemistry"],
        )
        == "electrolysis"
    )
    # A reviewed subtopic entry routes within a broad topic.
    assert (
        map_topic(
            "mathematics",
            {**meta, "topic": "calculus", "subtopic": "maxima-minima"},
            ours["mathematics"],
        )
        == "application-of-differentiation"
    )
    # A broad bucket we chose not to map stays unmapped.
    assert (
        map_topic(
            "physics", {**meta, "topic": "mechanics", "subtopic": ""}, ours["physics"]
        )
        is None
    )


def test_provider_topic_filters_reverse_the_reviewed_table():
    from app.services.provider.mapping import provider_topic_filters

    assert provider_topic_filters("physics", "gas-laws") == [
        ("heat", "gas-laws-thermal"),
        ("gas-laws-thermal", None),
    ]
    # No reviewed ALOC counterpart: never ask the provider for something else.
    assert provider_topic_filters("physics", "optical-instruments") == []
    assert provider_topic_filters("unknown-subject", "gas-laws") == []
