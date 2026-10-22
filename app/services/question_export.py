"""Pure helpers behind the admin question coverage and export endpoints."""

import csv
import io
import json
import re
import zipfile
from datetime import UTC, datetime

EXPORT_ROW_CAP = 20_000
EXPORT_FORMATS = ("csv", "json")
EXPORT_FIELDS = [
    "questionNumber",
    "examType",
    "examYear",
    "topic",
    "questionType",
    "questionText",
    "questionImageUrl",
    "options",
    "correctAnswer",
    "explanation",
    "explanationImageUrl",
    "difficulty",
    "marks",
]
MEDIA_TYPES = {"csv": "text/csv; charset=utf-8", "json": "application/json"}

# A spreadsheet runs a cell that starts with one of these as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_EARLIEST = datetime.min.replace(tzinfo=UTC)


def export_row(question, topic_title: str | None) -> dict:
    return {
        "questionNumber": question.question_number,
        "examType": question.exam_type,
        "examYear": question.exam_year,
        "topic": topic_title,
        "questionType": question.question_type,
        "questionText": question.question_text,
        "questionImageUrl": question.question_image_url,
        "options": question.options,
        "correctAnswer": question.correct_answer,
        "explanation": question.explanation,
        "explanationImageUrl": question.explanation_image_url,
        "difficulty": question.difficulty,
        "marks": question.marks,
    }


def safe_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict | list):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def to_csv(rows: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(EXPORT_FIELDS)
    for row in rows:
        writer.writerow([safe_cell(row[field]) for field in EXPORT_FIELDS])
    # The BOM is what makes Excel read the file as UTF-8.
    return buffer.getvalue().encode("utf-8-sig")


def to_json(rows: list[dict]) -> bytes:
    return json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")


def render(rows: list[dict], fmt: str) -> bytes:
    return to_csv(rows) if fmt == "csv" else to_json(rows)


def paper_filename(subject_slug: str, exam_type: str, exam_year: int, ext: str) -> str:
    stem = _UNSAFE_NAME.sub("-", f"{subject_slug}-{exam_type}-{exam_year}")
    return f"{stem}.{ext}"


def group_papers(rows: list[dict]) -> dict[tuple[str, int], list[dict]]:
    papers: dict[tuple[str, int], list[dict]] = {}
    for row in rows:
        papers.setdefault((row["examType"], row["examYear"]), []).append(row)
    return papers


def build_zip(
    subject_slug: str, papers: dict[tuple[str, int], list[dict]], fmt: str
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for (exam_type, exam_year), rows in papers.items():
            name = paper_filename(subject_slug, exam_type, exam_year, fmt)
            archive.writestr(name, render(rows, fmt))
    return buffer.getvalue()


def build_coverage(counts, statuses) -> list[dict]:
    latest: dict[tuple, tuple] = {}
    for subject_id, exam_type, exam_year, status, checkpoint_at in statuses:
        key = (subject_id, exam_type, exam_year)
        stamp = checkpoint_at or _EARLIEST
        if key not in latest or stamp >= latest[key][0]:
            latest[key] = (stamp, status)
    subjects: dict[str, dict] = {}
    for subject_id, name, code, exam_type, exam_year, count in counts:
        entry = subjects.setdefault(
            subject_id,
            {
                "subjectId": subject_id,
                "name": name,
                "code": code,
                "total": 0,
                "papers": [],
            },
        )
        entry["total"] += count
        entry["papers"].append(
            {
                "examType": exam_type,
                "examYear": exam_year,
                "count": count,
                "fetchStatus": latest.get(
                    (subject_id, exam_type, exam_year), (None, None)
                )[1],
            }
        )
    for entry in subjects.values():
        entry["papers"].sort(key=lambda paper: (-paper["examYear"], paper["examType"]))
    return sorted(subjects.values(), key=lambda entry: entry["name"].lower())
