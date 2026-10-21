import io
import zipfile
from types import SimpleNamespace

import pytest

from app.api.admin.questions import router
from app.api.deps import require_admin
from app.core.errors import ApiError
from app.services import question_coverage as mod
from app.services.question_export import EXPORT_ROW_CAP

SUBJECT = SimpleNamespace(id="s1", slug="mathematics", name="Mathematics")
SESSION = object()


def _q(year=2019, number=1, exam="JAMB"):
    return SimpleNamespace(
        question_number=number,
        exam_type=exam,
        exam_year=year,
        question_type="OBJECTIVE",
        question_text=f"Q{number}",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_answer="A",
        explanation=None,
        difficulty="BASIC",
        marks=1,
        question_image_url=None,
        explanation_image_url=None,
    )


@pytest.fixture
def audits(monkeypatch):
    calls = []

    class FakeAudit:
        def __init__(self, session, actor_id, action, entity, summary, entity_id=None):
            calls.append((actor_id, action, entity, summary, entity_id))

        async def process(self):
            return None

    monkeypatch.setattr(mod, "RecordAuditService", FakeAudit)
    return calls


@pytest.fixture
def bank(monkeypatch):
    """Patches the repositories; returns the for_export calls and shared state."""
    calls = []
    state = {"subject": SUBJECT, "pairs": []}

    async def by_id(_session, subject_id):
        return state["subject"]

    async def for_export(_session, subject_id, exam_type, exam_year, limit):
        calls.append((subject_id, exam_type, exam_year, limit))
        return state["pairs"]

    monkeypatch.setattr(mod.subjects_repository, "by_id", by_id)
    monkeypatch.setattr(mod.questions_repository, "for_export", for_export)
    return SimpleNamespace(calls=calls, state=state)


def _service(**over):
    args = dict(subject_id="s1", exam_type="JAMB", exam_year=2019, fmt="csv")
    args.update(over)
    return mod.ExportQuestionsService(SESSION, "admin-1", **args)


async def test_single_paper_export_returns_a_named_csv(bank, audits):
    bank.state["pairs"] = [(_q(number=1), "Algebra"), (_q(number=2), None)]
    content, media_type, filename = await _service().process()
    assert filename == "mathematics-JAMB-2019.csv"
    assert media_type.startswith("text/csv")
    assert content.startswith(b"\xef\xbb\xbf")
    assert bank.calls == [("s1", "JAMB", 2019, EXPORT_ROW_CAP + 1)]
    assert audits == [
        (
            "admin-1",
            "question.export",
            "Subject",
            "mathematics JAMB 2019 csv (2 rows)",
            "s1",
        )
    ]


async def test_single_paper_json_export(bank, audits):
    bank.state["pairs"] = [(_q(), "Algebra")]
    content, media_type, filename = await _service(fmt="json").process()
    assert (media_type, filename) == ("application/json", "mathematics-JAMB-2019.json")
    assert b'"topic": "Algebra"' in content


async def test_whole_subject_export_is_a_zip_with_a_file_per_paper(bank, audits):
    bank.state["pairs"] = [
        (_q(year=2018), None),
        (_q(year=2019), None),
        (_q(year=2019, exam="WAEC"), None),
    ]
    content, media_type, filename = await _service(
        exam_type=None, exam_year=None
    ).process()
    assert (media_type, filename) == ("application/zip", "mathematics.zip")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert sorted(archive.namelist()) == [
            "mathematics-JAMB-2018.csv",
            "mathematics-JAMB-2019.csv",
            "mathematics-WAEC-2019.csv",
        ]
    assert audits[0][3] == "mathematics all all years csv (3 rows)"


async def test_year_without_exam_type_is_rejected(bank, audits):
    with pytest.raises(ApiError) as caught:
        await _service(exam_type=None).process()
    assert caught.value.status_code == 400
    assert bank.calls == []


async def test_unknown_format_is_rejected(bank, audits):
    with pytest.raises(ApiError) as caught:
        await _service(fmt="xml").process()
    assert caught.value.status_code == 400


async def test_unknown_subject_is_404(bank, audits):
    bank.state["subject"] = None
    with pytest.raises(ApiError) as caught:
        await _service().process()
    assert caught.value.status_code == 404


async def test_nothing_to_export_is_404_and_not_audited(bank, audits):
    with pytest.raises(ApiError) as caught:
        await _service().process()
    assert caught.value.status_code == 404
    assert audits == []


async def test_more_than_the_cap_is_refused(bank, audits):
    bank.state["pairs"] = [(_q(), None)] * (EXPORT_ROW_CAP + 1)
    with pytest.raises(ApiError) as caught:
        await _service().process()
    assert caught.value.status_code == 400
    assert "exam type or year" in str(caught.value.payload)
    assert audits == []


async def test_coverage_merges_counts_with_fetch_status(monkeypatch):
    asked = []

    async def counts(_session):
        return [("s1", "Mathematics", "MTH", "JAMB", 2019, 60)]

    async def statuses(_session, provider):
        asked.append(provider)
        return [("s1", "JAMB", 2019, "SATURATED", None)]

    monkeypatch.setattr(mod.settings, "question_provider", "SDASH")
    monkeypatch.setattr(mod.questions_repository, "coverage_counts", counts)
    monkeypatch.setattr(mod.fetches_repository, "paper_statuses", statuses)
    body = await mod.GetQuestionCoverageService(SESSION).process()
    paper = body["subjects"][0]["papers"][0]
    assert (paper["count"], paper["fetchStatus"]) == (60, "SATURATED")
    # Only the provider in use decides a paper's badge.
    assert asked == ["SDASH"]


def test_new_routes_come_before_the_id_route_and_need_an_admin():
    paths = [route.path for route in router.routes]
    id_index = paths.index("/questions/{question_id}")
    for path in ("/questions/coverage", "/questions/export"):
        assert paths.index(path) < id_index
        route = router.routes[paths.index(path)]
        assert any(dep.call is require_admin for dep in route.dependant.dependencies)
