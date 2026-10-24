from types import SimpleNamespace

from app.database.models import AssessmentQuestion
from app.services.assessments import service as mod
from app.services.assessments.coverage_paper import as_paper_question, is_coverage


def _coverage_question(qid="c1"):
    return SimpleNamespace(
        id=qid,
        subject_id="s1",
        topic_id="t1",
        exam_type="JAMB",
        exam_year=2012,
        question_number=4,
        question_text="Fungi lack what?",
        question_image="img.png",
        question_type="OBJECTIVE",
        options={"A": "a", "B": "b", "C": "c", "D": "d"},
        correct_answer="B",
        explanation="Because.",
        solution_image="sol.png",
        difficulty="BASIC",
        marks=1,
    )


def test_coverage_question_reads_like_a_bank_question():
    paper = as_paper_question(_coverage_question())
    assert is_coverage(paper)
    assert paper.question_image_url == "img.png"
    assert paper.explanation_image_url == "sol.png"
    assert paper.correct_answer == "B"
    assert not is_coverage(SimpleNamespace(id="q1"))


async def test_paper_mixes_in_order_and_keeps_bank_only_papers_unchanged(monkeypatch):
    bank_question = SimpleNamespace(id="q1")
    links = [
        SimpleNamespace(order_index=0, question_id="q1", coverage_question_id=None),
        SimpleNamespace(order_index=1, question_id=None, coverage_question_id="c1"),
    ]

    async def list_where(session, *criteria, order_by=()):
        return links

    async def for_assessment(session, assessment_id):
        return [bank_question]

    async def by_ids(session, ids):
        assert ids == ["c1"]
        return [_coverage_question("c1")]

    monkeypatch.setattr(mod.assessment_questions_repository, "list_where", list_where)
    monkeypatch.setattr(mod.questions_repository, "for_assessment", for_assessment)
    monkeypatch.setattr(mod.coverage_questions_repository, "by_ids", by_ids)

    paper = await mod._paper_questions(object(), "a1")
    assert [q.id for q in paper] == ["q1", "c1"]

    links[:] = [links[0]]
    assert await mod._paper_questions(object(), "a1") == [bank_question]


async def test_persist_links_coverage_questions_to_their_own_column(monkeypatch):
    added = []

    async def add(session, entity, *args, **kwargs):
        added.append(entity)
        return entity

    class Session:
        async def flush(self):
            return None

    monkeypatch.setattr(mod.assessments_repository, "add", add)
    monkeypatch.setattr(mod.attempts_repository, "add", add)
    monkeypatch.setattr(mod.assessment_questions_repository, "add", add)

    await mod._persist(
        Session(),
        student_id="u1",
        subject=SimpleNamespace(id="s1"),
        questions=[as_paper_question(_coverage_question())],
        assessment_type="MOCK_EXAM",
        exam_type=None,
        exam_year=None,
        title="Biology mock",
        untimed=False,
    )
    link = next(item for item in added if isinstance(item, AssessmentQuestion))
    assert link.question_id is None
    assert link.coverage_question_id == "c1"


def test_mock_options_response_allows_no_board():
    from app.api.responses import MockOptionsOut

    out = MockOptionsOut.model_validate({"examType": None, "subjects": []})
    assert out.examType is None
