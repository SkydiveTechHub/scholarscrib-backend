import csv
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services.question_export import (
    EXPORT_FIELDS,
    build_coverage,
    build_zip,
    export_row,
    group_papers,
    paper_filename,
    safe_cell,
    to_csv,
    to_json,
)


def _q(**over):
    base = dict(
        question_number=1,
        exam_type="JAMB",
        exam_year=2019,
        question_type="OBJECTIVE",
        question_text="What is 2 + 2?",
        options={"A": "1", "B": "4", "C": "3", "D": "5"},
        correct_answer="B",
        explanation=None,
        difficulty="BASIC",
        marks=1,
        question_image_url=None,
        explanation_image_url=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _parse_csv(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


def test_export_row_maps_every_field_in_order():
    row = export_row(_q(), "Algebra")
    assert list(row) == EXPORT_FIELDS
    assert row["topic"] == "Algebra"
    assert row["questionNumber"] == 1
    assert row["correctAnswer"] == "B"


def test_export_keeps_the_image_references_of_diagram_questions():
    question = _q(
        question_image_url="https://cdn.example/q1.png",
        explanation_image_url="https://cdn.example/e1.png",
    )
    row = export_row(question, None)
    assert row["questionImageUrl"] == "https://cdn.example/q1.png"
    assert row["explanationImageUrl"] == "https://cdn.example/e1.png"
    header, first = _parse_csv(to_csv([row]))
    assert first[header.index("questionImageUrl")] == "https://cdn.example/q1.png"
    assert json.loads(to_json([row]))[0]["explanationImageUrl"].endswith("e1.png")


def test_csv_starts_with_bom_header_and_json_encoded_options():
    data = to_csv([export_row(_q(), None)])
    assert data.startswith(b"\xef\xbb\xbf")
    header, first = _parse_csv(data)
    assert header == EXPORT_FIELDS
    assert json.loads(first[EXPORT_FIELDS.index("options")]) == {
        "A": "1",
        "B": "4",
        "C": "3",
        "D": "5",
    }


def test_csv_prefixes_cells_that_a_spreadsheet_would_run():
    for danger in ["=1+1", "+cmd", "-2+3", "@SUM(A1)", "\tx", "\rx"]:
        assert safe_cell(danger) == "'" + danger
    rows = [export_row(_q(question_text='=HYPERLINK("x")', explanation="@a"), "-t")]
    _, first = _parse_csv(to_csv(rows))
    assert first[EXPORT_FIELDS.index("questionText")].startswith("'=")
    assert first[EXPORT_FIELDS.index("explanation")] == "'@a"
    assert first[EXPORT_FIELDS.index("topic")] == "'-t"


def test_safe_cell_leaves_numbers_and_plain_text_alone():
    assert safe_cell(5) == "5"
    assert safe_cell(0) == "0"
    assert safe_cell(True) == "True"
    assert safe_cell("Find x") == "Find x"
    assert safe_cell("") == ""


def test_csv_empty_for_null_and_non_object_options():
    rows = [
        export_row(_q(options=None, explanation=None), None),
        export_row(_q(options="free text"), None),
        export_row(_q(options=["x", "y"]), None),
    ]
    _, a, b, c = _parse_csv(to_csv(rows))
    idx = EXPORT_FIELDS.index("options")
    assert a[idx] == ""
    assert b[idx] == "free text"
    assert json.loads(c[idx]) == ["x", "y"]


def test_json_is_an_array_with_real_options():
    data = json.loads(to_json([export_row(_q(), "Algebra")]))
    assert isinstance(data, list)
    assert data[0]["options"]["B"] == "4"
    assert data[0]["topic"] == "Algebra"


def test_json_keeps_non_ascii_text_readable():
    raw = to_json([export_row(_q(question_text="Find π"), None)])
    assert "π".encode() in raw


def test_paper_filename_is_header_safe():
    assert paper_filename("mathematics", "JAMB", 2019, "csv") == (
        "mathematics-JAMB-2019.csv"
    )
    name = paper_filename('math"s\r\nx', "JAMB", 2019, "json")
    assert '"' not in name and "\r" not in name and "\n" not in name
    assert name.endswith("-JAMB-2019.json")


def test_group_papers_keeps_row_order_per_paper():
    rows = [
        export_row(_q(question_number=1, exam_year=2018), None),
        export_row(_q(question_number=2, exam_year=2018), None),
        export_row(_q(question_number=1, exam_year=2019), None),
    ]
    papers = group_papers(rows)
    assert list(papers) == [("JAMB", 2018), ("JAMB", 2019)]
    assert [r["questionNumber"] for r in papers[("JAMB", 2018)]] == [1, 2]


def test_zip_has_one_file_per_paper_in_the_chosen_format():
    rows = [
        export_row(_q(exam_year=2018), None),
        export_row(_q(exam_year=2019), None),
        export_row(_q(exam_type="WAEC", exam_year=2019), None),
    ]
    blob = build_zip("mathematics", group_papers(rows), "csv")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert sorted(archive.namelist()) == [
            "mathematics-JAMB-2018.csv",
            "mathematics-JAMB-2019.csv",
            "mathematics-WAEC-2019.csv",
        ]
        header = _parse_csv(archive.read("mathematics-JAMB-2018.csv"))[0]
        assert header == EXPORT_FIELDS
    blob = build_zip("mathematics", group_papers(rows[:1]), "json")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert archive.namelist() == ["mathematics-JAMB-2018.json"]
        assert json.loads(archive.read("mathematics-JAMB-2018.json"))[0]["marks"] == 1


NOW = datetime(2026, 10, 1, tzinfo=UTC)


def test_coverage_groups_counts_and_orders_papers():
    counts = [
        ("s2", "Physics", "PHY", "WAEC", 2018, 50),
        ("s1", "Mathematics", "MTH", "JAMB", 2018, 40),
        ("s1", "Mathematics", "MTH", "WAEC", 2020, 10),
        ("s1", "Mathematics", "MTH", "JAMB", 2020, 60),
    ]
    subjects = build_coverage(counts, [])
    assert [s["name"] for s in subjects] == ["Mathematics", "Physics"]
    maths = subjects[0]
    assert maths["total"] == 110
    assert [(p["examYear"], p["examType"]) for p in maths["papers"]] == [
        (2020, "JAMB"),
        (2020, "WAEC"),
        (2018, "JAMB"),
    ]
    assert all(p["fetchStatus"] is None for p in maths["papers"])


def test_coverage_takes_the_latest_checkpointed_fetch_status():
    counts = [("s1", "Mathematics", "MTH", "JAMB", 2019, 60)]
    statuses = [
        ("s1", "JAMB", 2019, "PENDING", NOW - timedelta(days=2)),
        ("s1", "JAMB", 2019, "SATURATED", NOW),
        ("s1", "JAMB", 2019, "FAILED", None),
        ("s1", "WAEC", 2019, "PENDING", NOW),
    ]
    paper = build_coverage(counts, statuses)[0]["papers"][0]
    assert paper["fetchStatus"] == "SATURATED"


def test_coverage_of_nothing_is_empty():
    assert build_coverage([], []) == []
