from types import SimpleNamespace

import pytest

from app.api.schemas import CoverageImportQuestionIn, ImportCoverageQuestionsIn
from app.core.errors import ApiError
from app.services import coverage_import as mod

SUBJECT = SimpleNamespace(id="s1")
TOPIC = SimpleNamespace(id="t1")

ROW = {
    "questionNumber": 4,
    "subjectCode": "BIO",
    "topicSlug": "classification-of-living-things",
    "examType": "JAMB",
    "examYear": 2012,
    "country": "NG",
    "questionType": "OBJECTIVE",
    "hasPassage": False,
    "passage": None,
    "questionText": "The fungi are a distinct group of eukaryotes because they have",
    "questionImage": None,
    "options": {"A": "spores", "B": "no chlorophyll", "C": "fruit", "D": "sexual"},
    "correctAnswer": "B",
    "explanation": "Fungi lack chlorophyll.",
    "detailedExplanation": "Longer explanation.",
    "possibleMistakes": ["Choosing spores"],
    "solutionImage": None,
    "difficulty": "INTERMEDIATE",
    "marks": 1,
}


@pytest.fixture
def env(monkeypatch):
    added = []
    state = {"duplicate": None}

    async def by_code(session, code):
        return SUBJECT if code == "BIO" else None

    async def by_slug(session, subject_id, slug):
        return TOPIC if slug == "classification-of-living-things" else None

    async def duplicate_id(session, **kwargs):
        return state["duplicate"]

    async def add(session, entity, *, flush=False):
        added.append(entity)
        return entity

    class FakeAudit:
        def __init__(self, *args):
            pass

        async def process(self):
            return None

    monkeypatch.setattr(mod.subjects_repository, "by_code", by_code)
    monkeypatch.setattr(mod.topics_repository, "by_slug", by_slug)
    monkeypatch.setattr(mod.coverage_questions_repository, "duplicate_id", duplicate_id)
    monkeypatch.setattr(mod.coverage_questions_repository, "add", add)
    monkeypatch.setattr(mod, "RecordAuditService", FakeAudit)
    return SimpleNamespace(added=added, state=state)


def _run(rows, skip=True):
    body = ImportCoverageQuestionsIn(
        questions=[CoverageImportQuestionIn(**row) for row in rows],
        skipDuplicates=skip,
    )
    return mod.ImportCoverageQuestionsService(object(), "a1", body).process()


async def test_imports_full_row(env):
    result = await _run([ROW])
    assert result["imported"] == 1 and result["errors"] == []
    saved = env.added[0]
    assert saved.subject_id == "s1" and saved.topic_id == "t1"
    assert saved.correct_answer == "B"
    assert saved.possible_mistakes == ["Choosing spores"]
    assert saved.detailed_explanation == "Longer explanation."
    assert saved.country == "NG"


async def test_skips_duplicates(env):
    env.state["duplicate"] = "q1"
    result = await _run([ROW])
    assert result["skipped"] == 1 and not env.added
    result = await _run([ROW], skip=False)
    assert result["imported"] == 1


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({"subjectCode": "XXX"}, "Unknown subject code"),
        ({"topicSlug": "nope"}, "Topic does not belong to that subject"),
        ({"examType": "SAT"}, "Unknown exam type"),
        ({"correctAnswer": "E"}, "Objective questions need"),
        ({"hasPassage": True}, "hasPassage is true"),
    ],
)
async def test_row_errors_are_reported(env, patch, reason):
    result = await _run([{**ROW, **patch}])
    assert result["imported"] == 0
    assert result["errors"][0]["index"] == 0
    assert reason in result["errors"][0]["reason"]


async def test_empty_payload_rejected(env):
    with pytest.raises(ApiError):
        await mod.ImportCoverageQuestionsService(
            object(), "a1", ImportCoverageQuestionsIn(questions=[])
        ).process()


async def test_overview_groups_counts_by_subject_and_paper(monkeypatch):
    async def counts(session):
        return [
            ("s1", "Biology", "BIO", "JAMB", 2012, 3),
            ("s1", "Biology", "BIO", "JAMB", 2014, 2),
        ]

    monkeypatch.setattr(mod.coverage_questions_repository, "coverage_counts", counts)
    result = await mod.GetCoverageOverviewService(object()).process()
    subject = result["subjects"][0]
    assert subject["total"] == 5
    assert [p["examYear"] for p in subject["papers"]] == [2014, 2012]
    assert all(p["fetchStatus"] is None for p in subject["papers"])
