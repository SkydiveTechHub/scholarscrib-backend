from sqlalchemy.dialects import postgresql

from app.database.repositories.provider import paper_status_statement
from app.database.repositories.question import coverage_statement, export_statement


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def test_coverage_groups_dated_questions_by_subject_exam_and_year():
    sql = _sql(coverage_statement())
    assert 'GROUP BY "Question"."subjectId"' in sql
    assert '"Question"."examYear" IS NOT NULL' in sql
    assert 'JOIN "Subject"' in sql


def test_export_is_scoped_to_the_subject_and_bounded():
    sql = _sql(export_statement("s1", None, None, 21))
    assert '"Question"."subjectId" = ' in sql
    assert '"Question"."examYear" IS NOT NULL' in sql
    assert "examType" in sql.split("ORDER BY")[1]
    assert "NULLS LAST" in sql
    assert "LIMIT" in sql
    assert 'LEFT OUTER JOIN "Topic"' in sql


def test_export_narrows_by_exam_type_and_year_when_given():
    sql = _sql(export_statement("s1", "JAMB", 2019, 21))
    where = sql.split("WHERE")[1].split("ORDER BY")[0]
    assert '"Question"."examType" = ' in where
    assert '"Question"."examYear" = ' in where


def test_paper_statuses_only_cover_dated_fetches():
    sql = _sql(paper_status_statement("ALOC"))
    assert '"ProviderFetch"."subjectId" IS NOT NULL' in sql
    assert '"ProviderFetch"."examYear" IS NOT NULL' in sql


def test_paper_statuses_are_scoped_to_one_provider():
    # A paper can have a fetch row per provider; mixing them made the badge
    # depend on whichever row the database returned last.
    statement = paper_status_statement("SDASH")
    assert '"ProviderFetch".provider = ' in _sql(statement)
    assert "SDASH" in statement.compile().params.values()


from types import SimpleNamespace  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.database.models import Question  # noqa: E402
from app.database.repositories.question import list_order  # noqa: E402
from app.services.admin import question_row  # noqa: E402


def test_a_year_filtered_list_is_in_paper_order():
    sql = _sql(select(Question).order_by(*list_order({"examYear": 2019})))
    order = sql.split("ORDER BY")[1]
    assert order.index("questionNumber") < order.index('"Question".id')
    assert "NULLS LAST" in order


def test_an_unfiltered_list_keeps_ordering_by_id():
    sql = _sql(select(Question).order_by(*list_order({})))
    assert "questionNumber" not in sql.split("ORDER BY")[1]


def test_question_row_carries_the_question_number():
    question = SimpleNamespace(
        id="q1",
        subject_id="s1",
        topic_id=None,
        exam_type="JAMB",
        exam_year=2019,
        question_number=7,
        question_text="t",
        question_type="OBJECTIVE",
        options=None,
        correct_answer="A",
        explanation=None,
        difficulty="BASIC",
        marks=1,
    )
    assert question_row(question)["questionNumber"] == 7
