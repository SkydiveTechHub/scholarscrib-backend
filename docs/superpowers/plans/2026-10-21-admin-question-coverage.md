# Admin Question Coverage and Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin see which exam years are populated per subject on the Questions page, preview a paper's questions, and download a paper (CSV/JSON) or a whole subject (zip).

**Architecture:** Two new admin endpoints (`GET /admin/api/questions/coverage`, `GET /admin/api/questions/export`) backed by pure serialisation helpers, SQL statement builders and thin services. The frontend splits the Questions page into a default **Coverage** tab and the existing list (**All questions**), with a preview drawer and authenticated blob downloads.

**Tech Stack:** FastAPI, SQLAlchemy async (PostgreSQL), pytest + pytest-asyncio (`asyncio_mode = "auto"`); Next.js 16 / React 19, TanStack Query, axios, `node:test` via `tsx`.

**Spec:** `docs/superpowers/specs/2026-10-20-admin-question-coverage-design.md`

Two repos are involved. Backend tasks (1-3) run in `C:\Users\user\Desktop\Scholars\scholarscrib-backend`; frontend tasks (4-8) in `C:\Users\user\Desktop\Scholars\scholarscrib-frontend`.

**Spec deviations (decided while planning):**
- Services live in a new `app/services/question_coverage.py`, not `console.py` (already ~1k lines). Pure helpers are in `app/services/question_export.py`.
- Because the admin list is a plain id-ordered list, the list endpoint now orders by question number when `examYear` is filtered, and list rows gain `questionNumber`, so a paper previews in paper order.

## Global Constraints

- Formats: `csv` and `json` only. Download scope: one paper, or one subject as a zip with one file per `(examType, examYear)`.
- Export row cap: `20,000` per request; over it returns 400 asking the admin to narrow the filter. Empty selection returns 404 "No questions to export". Unknown subject 404. Unknown format 400.
- CSV is UTF-8 **with BOM**; cells beginning with `=`, `+`, `-`, `@`, tab or CR are prefixed with `'`. `options` is a JSON string in one CSV cell and a real object in JSON.
- Export fields, in order: `questionNumber, examType, examYear, topic, questionType, questionText, options, correctAnswer, explanation, difficulty, marks`.
- Coverage excludes questions with `exam_year IS NULL`. `fetchStatus` is `PENDING | SATURATED | FAILED | null`; if several fetch rows match a paper, the most recently checkpointed one wins.
- Every export writes an audit entry, action `question.export`.
- Both endpoints use `require_admin` and are registered before `/{question_id}`.
- The Coverage tab is the default; deep links carrying `subjectId`, `examType`, `examYear` or `difficulty` and no `tab` open **All questions**.
- Frontend: this Next.js version has breaking changes (see `AGENTS.md`). Before writing Task 6-7 code, read `node_modules/next/dist/docs/01-app/03-api-reference/04-functions/use-search-params.md` and `.../use-router.md`.
- **Commits:** the backend index already holds unrelated staged work (`git status` shows staged `provider` changes). Always commit with an explicit path list so only this plan's files go in: `git add <new files>` then `git commit -m "..." -- <all paths>`. Never run a bare `git commit`.

## Review Focus

Inputs the spec implies but no happy-path test would exercise; each has a test in the named task.

1. Question text, explanation or topic starting with `=`, `@`, `+`, `-` (spreadsheet formula injection) → prefixed in CSV (Task 1).
2. A subject slug or exam type with quotes/CR/LF/odd characters → `Content-Disposition` filename stays header-safe (Task 1).
3. `options` null, or a non-object (string/list) → CSV cell empty / JSON-encoded, preview doesn't crash (Tasks 1, 7).
4. Subject with no dated questions, or a filter matching nothing → 404 from export, an empty state in the UI, and the subject simply absent from coverage (Tasks 2, 7).
5. Export refused (over cap / 404) arrives as a **Blob** body, so the admin must still see the backend's message, not "Network error" (Task 5).

## File Structure

Backend (`scholarscrib-backend`):
- Create `app/services/question_export.py`: pure helpers (`export_row`, `safe_cell`, `to_csv`, `to_json`, `render`, `paper_filename`, `group_papers`, `build_zip`, `build_coverage`, constants).
- Create `app/services/question_coverage.py`: `GetQuestionCoverageService`, `ExportQuestionsService`.
- Modify `app/database/repositories/question.py`: `coverage_statement`, `export_statement`, `list_order`; repository methods `coverage_counts`, `for_export`; `list_filtered` uses `list_order`.
- Modify `app/database/repositories/provider.py`: `paper_status_statement`, `ProviderFetchRepository.paper_statuses`.
- Modify `app/services/admin.py`: `question_row` gains `questionNumber`.
- Modify `app/api/responses.py`: `QuestionCoverageOut`.
- Modify `app/api/admin/questions.py`: two routes.
- Modify `main.py`: expose `Content-Disposition` over CORS.
- Create tests: `tests/test_question_export.py`, `tests/test_question_coverage_service.py`, `tests/test_question_coverage_queries.py`.

Frontend (`scholarscrib-frontend`):
- Modify `src/lib/api/endpoints.ts`, `src/lib/api/types.ts`, `src/lib/api/query-keys.ts`, `src/lib/api/http.ts`.
- Create `src/lib/api/error-body.ts`, `src/lib/admin-coverage.ts`, `src/lib/save-blob.ts`.
- Modify `src/hooks/api/use-admin-questions.ts`.
- Move `questions-client.tsx` → `all-questions-tab.tsx`; create new `questions-client.tsx` (shell), `coverage-tab.tsx`, `export-buttons.tsx`, `paper-preview.tsx` (all under `src/app/admin/(console)/questions/`).
- Create tests: `scripts/test-admin-coverage.mts`, `scripts/test-error-body.mts`.

---

### Task 1: Pure export and coverage helpers

**Files:**
- Create: `app/services/question_export.py`
- Test: `tests/test_question_export.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces (used by Task 2):
  - `EXPORT_ROW_CAP: int = 20_000`, `EXPORT_FORMATS = ("csv", "json")`, `EXPORT_FIELDS: list[str]`, `MEDIA_TYPES: dict[str, str]`
  - `export_row(question, topic_title: str | None) -> dict`
  - `safe_cell(value) -> str`
  - `to_csv(rows: list[dict]) -> bytes`, `to_json(rows: list[dict]) -> bytes`, `render(rows, fmt: str) -> bytes`
  - `paper_filename(subject_slug: str, exam_type: str, exam_year: int, ext: str) -> str`
  - `group_papers(rows: list[dict]) -> dict[tuple[str, int], list[dict]]`
  - `build_zip(subject_slug: str, papers: dict[tuple[str, int], list[dict]], fmt: str) -> bytes`
  - `build_coverage(counts, statuses) -> list[dict]` where `counts` is an iterable of `(subject_id, name, code, exam_type, exam_year, count)` and `statuses` an iterable of `(subject_id, exam_type, exam_year, status, checkpoint_at)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_question_export.py`:

```python
import csv
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
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
    rows = [export_row(_q(question_text="=HYPERLINK(\"x\")", explanation="@a"), "-t")]
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


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_question_export.py -v`
Expected: collection error / FAIL, `ModuleNotFoundError: No module named 'app.services.question_export'`.

- [ ] **Step 3: Write the implementation**

Create `app/services/question_export.py`:

```python
"""Pure helpers behind the admin question coverage and export endpoints."""

import csv
import io
import json
import re
import zipfile
from datetime import datetime, timezone

EXPORT_ROW_CAP = 20_000
EXPORT_FORMATS = ("csv", "json")
EXPORT_FIELDS = [
    "questionNumber",
    "examType",
    "examYear",
    "topic",
    "questionType",
    "questionText",
    "options",
    "correctAnswer",
    "explanation",
    "difficulty",
    "marks",
]
MEDIA_TYPES = {"csv": "text/csv; charset=utf-8", "json": "application/json"}

# A spreadsheet runs a cell that starts with one of these as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


def export_row(question, topic_title: str | None) -> dict:
    return {
        "questionNumber": question.question_number,
        "examType": question.exam_type,
        "examYear": question.exam_year,
        "topic": topic_title,
        "questionType": question.question_type,
        "questionText": question.question_text,
        "options": question.options,
        "correctAnswer": question.correct_answer,
        "explanation": question.explanation,
        "difficulty": question.difficulty,
        "marks": question.marks,
    }


def safe_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, (dict, list)):
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
                "fetchStatus": latest.get((subject_id, exam_type, exam_year), (None, None))[1],
            }
        )
    for entry in subjects.values():
        entry["papers"].sort(key=lambda paper: (-paper["examYear"], paper["examType"]))
    return sorted(subjects.values(), key=lambda entry: entry["name"].lower())
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv\Scripts\python -m pytest tests/test_question_export.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/question_export.py tests/test_question_export.py
git commit -m "feat: pure helpers for question coverage and export" -- app/services/question_export.py tests/test_question_export.py
```

---

### Task 2: Repository queries, services, endpoints, CORS

**Files:**
- Modify: `app/database/repositories/question.py` (add statement builders + methods)
- Modify: `app/database/repositories/provider.py` (add `paper_status_statement` + `paper_statuses`)
- Create: `app/services/question_coverage.py`
- Modify: `app/api/responses.py` (after `UsageOut`, ~line 656)
- Modify: `app/api/admin/questions.py`
- Modify: `main.py:45` (CORS `expose_headers`)
- Test: `tests/test_question_coverage_queries.py`, `tests/test_question_coverage_service.py`

**Interfaces:**
- Consumes (Task 1): `EXPORT_ROW_CAP`, `EXPORT_FORMATS`, `MEDIA_TYPES`, `export_row`, `render`, `paper_filename`, `group_papers`, `build_zip`, `build_coverage`.
- Produces:
  - `question.py`: `coverage_statement()`, `export_statement(subject_id, exam_type, exam_year, limit)` (module functions returning SQLAlchemy `Select`); `QuestionRepository.coverage_counts(session) -> list[tuple]`, `QuestionRepository.for_export(session, subject_id, exam_type, exam_year, limit) -> list[tuple[Question, str | None]]`.
  - `provider.py`: `paper_status_statement()`; `ProviderFetchRepository.paper_statuses(session) -> list[tuple]`.
  - `GetQuestionCoverageService(session).process() -> {"subjects": [...]}`.
  - `ExportQuestionsService(session, actor_id, *, subject_id, exam_type, exam_year, fmt).process() -> tuple[bytes, str, str]` = `(content, media_type, filename)`.
  - Routes `GET /admin/api/questions/coverage` and `GET /admin/api/questions/export?subjectId=&examType=&examYear=&format=`.

- [ ] **Step 1: Write the failing query tests**

Create `tests/test_question_coverage_queries.py`:

```python
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
    sql = _sql(paper_status_statement())
    assert '"ProviderFetch"."subjectId" IS NOT NULL' in sql
    assert '"ProviderFetch"."examYear" IS NOT NULL' in sql
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_question_coverage_queries.py -v`
Expected: FAIL, `ImportError: cannot import name 'coverage_statement'`.

- [ ] **Step 3: Implement the statement builders and repository methods**

In `app/database/repositories/question.py`, change the model import to include `Subject`:

```python
from app.database.models import (
    AssessmentAttempt,
    AssessmentQuestion,
    CurriculumLevel,
    Question,
    QuestionResponse,
    Subject,
    Topic,
)
```

Add above `class QuestionRepository` (after the imports):

```python
def coverage_statement():
    """Question counts per subject, exam type and year, dated papers only."""
    return (
        select(
            Question.subject_id,
            Subject.name,
            Subject.code,
            Question.exam_type,
            Question.exam_year,
            func.count(),
        )
        .join(Subject, Subject.id == Question.subject_id)
        .where(Question.exam_year.is_not(None))
        .group_by(
            Question.subject_id,
            Subject.name,
            Subject.code,
            Question.exam_type,
            Question.exam_year,
        )
    )


def export_statement(
    subject_id: str, exam_type: str | None, exam_year: int | None, limit: int
):
    """A subject's dated questions with their topic title, in paper order."""
    statement = (
        select(Question, Topic.title)
        .outerjoin(Topic, Topic.id == Question.topic_id)
        .where(Question.subject_id == subject_id, Question.exam_year.is_not(None))
    )
    if exam_type:
        statement = statement.where(Question.exam_type == exam_type)
    if exam_year is not None:
        statement = statement.where(Question.exam_year == exam_year)
    return statement.order_by(
        Question.exam_type,
        Question.exam_year,
        Question.question_number.asc().nulls_last(),
        Question.id,
    ).limit(limit)
```

Add methods inside `QuestionRepository` (after `list_filtered`):

```python
    async def coverage_counts(self, session: AsyncSession) -> list[tuple]:
        result = await session.execute(coverage_statement())
        return [tuple(row) for row in result.all()]

    async def for_export(
        self,
        session: AsyncSession,
        subject_id: str,
        exam_type: str | None,
        exam_year: int | None,
        limit: int,
    ) -> list[tuple[Question, str | None]]:
        result = await session.execute(
            export_statement(subject_id, exam_type, exam_year, limit)
        )
        return [(row[0], row[1]) for row in result.all()]
```

In `app/database/repositories/provider.py` add above `class ProviderFetchRepository`:

```python
def paper_status_statement():
    """Every dated fetch row's status, for the admin coverage view."""
    return select(
        ProviderFetch.subject_id,
        ProviderFetch.exam_type,
        ProviderFetch.exam_year,
        ProviderFetch.status,
        ProviderFetch.checkpoint_at,
    ).where(
        ProviderFetch.subject_id.is_not(None),
        ProviderFetch.exam_year.is_not(None),
    )
```

and inside `ProviderFetchRepository`:

```python
    async def paper_statuses(self, session: AsyncSession) -> list[tuple]:
        result = await session.execute(paper_status_statement())
        return [tuple(row) for row in result.all()]
```

- [ ] **Step 4: Run to verify the query tests pass**

Run: `.venv\Scripts\python -m pytest tests/test_question_coverage_queries.py -v`
Expected: PASS. (If a `sql` substring differs because of quoting, print `_sql(...)` and adjust the assertion to the real compiled text, keeping the intent: grouping, `IS NOT NULL`, join, `NULLS LAST`, `LIMIT`.)

- [ ] **Step 5: Write the failing service and route tests**

Create `tests/test_question_coverage_service.py`:

```python
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
    """Patches the repositories; returns the list of for_export calls."""
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
    async def counts(_session):
        return [("s1", "Mathematics", "MTH", "JAMB", 2019, 60)]

    async def statuses(_session):
        return [("s1", "JAMB", 2019, "SATURATED", None)]

    monkeypatch.setattr(mod.questions_repository, "coverage_counts", counts)
    monkeypatch.setattr(mod.fetches_repository, "paper_statuses", statuses)
    body = await mod.GetQuestionCoverageService(SESSION).process()
    paper = body["subjects"][0]["papers"][0]
    assert (paper["count"], paper["fetchStatus"]) == (60, "SATURATED")


def test_new_routes_come_before_the_id_route_and_need_an_admin():
    paths = [route.path for route in router.routes]
    id_index = paths.index("/questions/{question_id}")
    for path in ("/questions/coverage", "/questions/export"):
        assert paths.index(path) < id_index
        route = router.routes[paths.index(path)]
        assert any(dep.call is require_admin for dep in route.dependant.dependencies)
```

- [ ] **Step 6: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_question_coverage_service.py -v`
Expected: FAIL, `ImportError: cannot import name 'question_coverage'`.

- [ ] **Step 7: Implement the services**

Create `app/services/question_coverage.py`:

```python
"""Admin question coverage (what is populated) and export (download it)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.database.repositories.curriculum import subjects_repository
from app.database.repositories.provider import fetches_repository
from app.database.repositories.question import questions_repository
from app.services.admin import RecordAuditService
from app.services.question_export import (
    EXPORT_FORMATS,
    EXPORT_ROW_CAP,
    MEDIA_TYPES,
    build_coverage,
    build_zip,
    export_row,
    group_papers,
    paper_filename,
    render,
)


class GetQuestionCoverageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def process(self) -> dict:
        counts = await questions_repository.coverage_counts(self.session)
        statuses = await fetches_repository.paper_statuses(self.session)
        return {"subjects": build_coverage(counts, statuses)}


class ExportQuestionsService:
    """One paper as a file, or a whole subject as a zip of papers."""

    def __init__(
        self,
        session: AsyncSession,
        actor_id: str,
        *,
        subject_id: str,
        exam_type: str | None,
        exam_year: int | None,
        fmt: str,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.subject_id = subject_id
        self.exam_type = exam_type
        self.exam_year = exam_year
        self.fmt = fmt

    async def process(self) -> tuple[bytes, str, str]:
        self._validate()
        subject = await subjects_repository.by_id(self.session, self.subject_id)
        if subject is None:
            raise ApiError(404, "Subject not found")
        pairs = await questions_repository.for_export(
            self.session,
            self.subject_id,
            self.exam_type,
            self.exam_year,
            EXPORT_ROW_CAP + 1,
        )
        if len(pairs) > EXPORT_ROW_CAP:
            raise ApiError(
                400,
                "Too many questions to export at once. Choose an exam type or year.",
            )
        if not pairs:
            raise ApiError(404, "No questions to export")
        rows = [export_row(question, topic) for question, topic in pairs]
        if self.exam_year is not None:
            content = render(rows, self.fmt)
            media_type = MEDIA_TYPES[self.fmt]
            filename = paper_filename(
                subject.slug, self.exam_type, self.exam_year, self.fmt
            )
        else:
            content = build_zip(subject.slug, group_papers(rows), self.fmt)
            media_type = "application/zip"
            filename = f"{subject.slug}.zip"
        await RecordAuditService(
            self.session,
            self.actor_id,
            "question.export",
            "Subject",
            f"{subject.slug} {self.exam_type or 'all'} "
            f"{self.exam_year or 'all years'} {self.fmt} ({len(rows)} rows)",
            subject.id,
        ).process()
        return content, media_type, filename

    def _validate(self) -> None:
        if self.fmt not in EXPORT_FORMATS:
            raise ApiError(400, "Format must be csv or json")
        if self.exam_year is not None and not self.exam_type:
            raise ApiError(400, "Choose an exam type to export a single year")
```

- [ ] **Step 8: Add the response model, routes and CORS exposure**

In `app/api/responses.py`, after `class UsageOut` add:

```python
class QuestionCoverageOut(ApiOut):
    subjects: list[dict]
```

In `app/api/admin/questions.py`: change imports to add `Query`, `Response` and the new names, then add the routes **after** `question_form_options` and **before** `create_question`:

```python
from fastapi import APIRouter, Body, Depends, Query, Request, Response
```
```python
from app.api.responses import (
    AdminQuestionsOut,
    DeleteQuestionsOut,
    FormOptionsOut,
    IdOut,
    ImportQuestionsOut,
    QuestionCoverageOut,
    QuestionOut,
    UsageOut,
)
```
```python
from app.services.question_coverage import (
    ExportQuestionsService,
    GetQuestionCoverageService,
)
```
```python
@router.get("/coverage", response_model=QuestionCoverageOut)
async def question_coverage(
    session: AnSession, admin: Annotated[Admin, Depends(require_admin)]
):
    return await GetQuestionCoverageService(session).process()


@router.get("/export")
async def export_questions(
    session: AnSession,
    admin: Annotated[Admin, Depends(require_admin)],
    subjectId: str,
    fmt: Annotated[str, Query(alias="format")],
    examType: ExamType | None = None,
    examYear: int | None = None,
):
    content, media_type, filename = await ExportQuestionsService(
        session,
        admin.id,
        subject_id=subjectId,
        exam_type=examType,
        exam_year=examYear,
        fmt=fmt,
    ).process()
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
```

In `main.py`, change line 45:

```python
        # pdf.js reads the first three cross-origin to decide whether it can
        # range-load; the admin export download reads the filename from the last.
        expose_headers=[
            "Accept-Ranges",
            "Content-Range",
            "Content-Length",
            "Content-Disposition",
        ],
```

- [ ] **Step 9: Run the backend tests**

Run: `.venv\Scripts\python -m pytest tests/test_question_coverage_service.py tests/test_question_coverage_queries.py tests/test_question_export.py -v`
Expected: all PASS.
Then the whole suite: `.venv\Scripts\python -m pytest -q` — expected no new failures versus before this task.

- [ ] **Step 10: Commit**

```bash
git add app/services/question_coverage.py tests/test_question_coverage_service.py tests/test_question_coverage_queries.py
git commit -m "feat: admin question coverage and export endpoints" -- app/services/question_coverage.py tests/test_question_coverage_service.py tests/test_question_coverage_queries.py app/database/repositories/question.py app/database/repositories/provider.py app/api/responses.py app/api/admin/questions.py main.py
```

---

### Task 3: List rows in paper order, with the question number

**Files:**
- Modify: `app/database/repositories/question.py` (`list_filtered`, new `list_order`)
- Modify: `app/services/admin.py:659` (`question_row`)
- Test: `tests/test_question_coverage_queries.py` (append)

**Interfaces:**
- Produces: `list_order(filters: dict) -> list` (SQLAlchemy order-by clauses); `question_row(...)["questionNumber"]`. The frontend preview (Task 7) relies on both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_question_coverage_queries.py`:

```python
from types import SimpleNamespace

from sqlalchemy import select

from app.database.models import Question
from app.database.repositories.question import list_order
from app.services.admin import question_row


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_question_coverage_queries.py -v`
Expected: FAIL, `ImportError: cannot import name 'list_order'`.

- [ ] **Step 3: Implement**

In `app/database/repositories/question.py`, add beside the other builders:

```python
def list_order(filters: dict) -> list:
    """Paper order once a year is chosen; otherwise the stable id order."""
    if filters.get("examYear"):
        return [Question.question_number.asc().nulls_last(), Question.id]
    return [Question.id]
```

and in `list_filtered` replace `.order_by(Question.id)` with `.order_by(*list_order(filters))`.

In `app/services/admin.py` `question_row`, add after `"examYear"`:

```python
        "questionNumber": question.question_number,
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv\Scripts\python -m pytest tests/test_question_coverage_queries.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: admin question list in paper order with question numbers" -- app/database/repositories/question.py app/services/admin.py tests/test_question_coverage_queries.py
```

---

### Task 4: Frontend contracts and pure helpers

Work in `C:\Users\user\Desktop\Scholars\scholarscrib-frontend`.

**Files:**
- Modify: `src/lib/api/endpoints.ts` (inside `admin.questions`, after `formOptions`)
- Modify: `src/lib/api/types.ts` (after `AdminQuestionsOut`, ~line 522)
- Modify: `src/lib/api/query-keys.ts` (inside `admin`, after `questionUsage`)
- Create: `src/lib/admin-coverage.ts`
- Test: `scripts/test-admin-coverage.mts`

**Interfaces:**
- Produces (used by Tasks 5-7):
  - `endpoints.admin.questions.coverage`, `endpoints.admin.questions.export`
  - types `CoverageFetchStatus`, `AdminCoveragePaperOut`, `AdminCoverageSubjectOut`, `AdminQuestionCoverageOut`
  - `queryKeys.admin.questionCoverage()`
  - from `admin-coverage.ts`: `type ExportFormat = "csv" | "json"`, `type QuestionsTab = "coverage" | "all"`, `paperKey(subjectId, examType, examYear): string`, `fillBadge(status): { label: string; tone: "amber" | "red" } | null`, `filenameFromDisposition(header, fallback): string`, `exportParams(input): Record<string, string>`, `resolveTab(params): QuestionsTab`, `tabHref(tab): string`, `listHref(filters): string`, `optionEntries(options: unknown): [string, string][]`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-admin-coverage.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  exportParams,
  fillBadge,
  filenameFromDisposition,
  listHref,
  optionEntries,
  paperKey,
  resolveTab,
  tabHref,
} from "../src/lib/admin-coverage";

const params = (query: string) => new URLSearchParams(query);

test("the coverage tab is the default", () => {
  assert.equal(resolveTab(params("")), "coverage");
});

test("an explicit tab wins", () => {
  assert.equal(resolveTab(params("tab=all")), "all");
  assert.equal(resolveTab(params("tab=coverage&subjectId=s1")), "coverage");
});

test("a link carrying list filters and no tab opens the list", () => {
  for (const q of ["subjectId=s1", "examType=JAMB", "examYear=2019", "difficulty=BASIC"]) {
    assert.equal(resolveTab(params(q)), "all", q);
  }
});

test("an unknown tab value falls back as if absent", () => {
  assert.equal(resolveTab(params("tab=bogus")), "coverage");
  assert.equal(resolveTab(params("tab=bogus&examYear=2019")), "all");
});

test("hrefs", () => {
  assert.equal(tabHref("coverage"), "/admin/questions?tab=coverage");
  assert.equal(
    listHref({ subjectId: "s 1", examType: "JAMB", examYear: 2019 }),
    "/admin/questions?tab=all&subjectId=s+1&examType=JAMB&examYear=2019",
  );
});

test("paperKey is stable per subject, exam and year", () => {
  assert.equal(paperKey("s1", "JAMB", 2019), "s1|JAMB|2019");
});

test("fillBadge flags only papers that are not complete", () => {
  assert.deepEqual(fillBadge("PENDING"), { label: "Filling", tone: "amber" });
  assert.deepEqual(fillBadge("FAILED"), { label: "Fetch failed", tone: "red" });
  assert.equal(fillBadge("SATURATED"), null);
  assert.equal(fillBadge(null), null);
});

test("filenameFromDisposition reads quoted and bare names", () => {
  assert.equal(
    filenameFromDisposition('attachment; filename="maths-JAMB-2019.csv"', "x.csv"),
    "maths-JAMB-2019.csv",
  );
  assert.equal(filenameFromDisposition("attachment; filename=maths.zip", "x.csv"), "maths.zip");
});

test("filenameFromDisposition falls back when absent or unusable", () => {
  assert.equal(filenameFromDisposition(undefined, "export.csv"), "export.csv");
  assert.equal(filenameFromDisposition("attachment", "export.csv"), "export.csv");
  assert.equal(filenameFromDisposition('attachment; filename=""', "export.csv"), "export.csv");
});

test("filenameFromDisposition never lets a path through", () => {
  assert.equal(
    filenameFromDisposition('attachment; filename="../../etc/passwd"', "export.csv"),
    "passwd",
  );
  assert.equal(filenameFromDisposition('attachment; filename="a\\b.csv"', "x.csv"), "b.csv");
});

test("exportParams sends only what was chosen", () => {
  assert.deepEqual(exportParams({ subjectId: "s1", format: "csv" }), {
    subjectId: "s1",
    format: "csv",
  });
  assert.deepEqual(
    exportParams({ subjectId: "s1", examType: "JAMB", examYear: 2019, format: "json" }),
    { subjectId: "s1", examType: "JAMB", examYear: "2019", format: "json" },
  );
});

test("optionEntries copes with every shape the options column can hold", () => {
  assert.deepEqual(optionEntries({ A: "one", B: "two" }), [
    ["A", "one"],
    ["B", "two"],
  ]);
  assert.deepEqual(optionEntries({ A: 1, B: null }), [
    ["A", "1"],
    ["B", ""],
  ]);
  assert.deepEqual(optionEntries(null), []);
  assert.deepEqual(optionEntries("free text"), []);
  assert.deepEqual(optionEntries(["x", "y"]), []);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-admin-coverage.mts`
Expected: FAIL, cannot find module `../src/lib/admin-coverage`.

- [ ] **Step 3: Add the contracts**

`src/lib/api/endpoints.ts`, inside `admin.questions` after `formOptions`:

```ts
      coverage: "/admin/api/questions/coverage",
      export: "/admin/api/questions/export",
```

`src/lib/api/types.ts`, after `AdminQuestionsOut`:

```ts
export type CoverageFetchStatus = "PENDING" | "SATURATED" | "FAILED";

/** One populated paper in `GET /admin/api/questions/coverage`. */
export type AdminCoveragePaperOut = {
  examType: string;
  examYear: number;
  count: number;
  /** Provider fill status; null for papers added by hand or imported. */
  fetchStatus: CoverageFetchStatus | null;
};

export type AdminCoverageSubjectOut = {
  subjectId: string;
  name: string;
  code: string;
  total: number;
  /** Newest year first. */
  papers: AdminCoveragePaperOut[];
};

export type AdminQuestionCoverageOut = { subjects: AdminCoverageSubjectOut[] };
```

`src/lib/api/query-keys.ts`, inside `admin` after `questionUsage`:

```ts
    /** Under the admin question prefix, so any question write marks it stale. */
    questionCoverage: () => [...queryKeys.admin.all, "questions", "coverage"] as const,
```

- [ ] **Step 4: Implement the helpers**

Create `src/lib/admin-coverage.ts`:

```ts
import type { CoverageFetchStatus } from "@/lib/api/types";

export type ExportFormat = "csv" | "json";
export type QuestionsTab = "coverage" | "all";

const LIST_FILTER_KEYS = ["subjectId", "examType", "examYear", "difficulty"] as const;

/** Which tab the URL asks for. Old list deep links (filters, no `tab`) open the list. */
export function resolveTab(params: { get(name: string): string | null }): QuestionsTab {
  const tab = params.get("tab");
  if (tab === "all" || tab === "coverage") return tab;
  return LIST_FILTER_KEYS.some((key) => params.get(key)) ? "all" : "coverage";
}

export function tabHref(tab: QuestionsTab): string {
  return `/admin/questions?tab=${tab}`;
}

/** The All questions tab, pre-filtered to one paper. */
export function listHref(filters: {
  subjectId: string;
  examType: string;
  examYear: number;
}): string {
  const query = new URLSearchParams({
    tab: "all",
    subjectId: filters.subjectId,
    examType: filters.examType,
    examYear: String(filters.examYear),
  });
  return `/admin/questions?${query.toString()}`;
}

export function paperKey(subjectId: string, examType: string, examYear: number): string {
  return `${subjectId}|${examType}|${examYear}`;
}

/** A badge for a paper the provider is still filling or failed to fill; none when complete or manual. */
export function fillBadge(
  status: CoverageFetchStatus | null,
): { label: string; tone: "amber" | "red" } | null {
  if (status === "PENDING") return { label: "Filling", tone: "amber" };
  if (status === "FAILED") return { label: "Fetch failed", tone: "red" };
  return null;
}

/** The name in a `Content-Disposition` header, stripped of any path, or `fallback`. */
export function filenameFromDisposition(
  header: string | null | undefined,
  fallback: string,
): string {
  const match = header?.match(/filename=(?:"([^"]*)"|([^;]+))/i);
  const raw = (match?.[1] ?? match?.[2] ?? "").trim();
  const name = raw.split(/[\\/]/).pop()?.trim() ?? "";
  return name || fallback;
}

export function exportParams(input: {
  subjectId: string;
  examType?: string;
  examYear?: number;
  format: ExportFormat;
}): Record<string, string> {
  const params: Record<string, string> = { subjectId: input.subjectId, format: input.format };
  if (input.examType) params.examType = input.examType;
  if (input.examYear !== undefined) params.examYear = String(input.examYear);
  return params;
}

/**
 * A question's options as [key, text] pairs. The column is a nullable JSON
 * blob, so anything but a plain object yields no options rather than a crash.
 */
export function optionEntries(options: unknown): [string, string][] {
  if (!options || typeof options !== "object" || Array.isArray(options)) return [];
  return Object.entries(options as Record<string, unknown>).map(([key, value]) => [
    key,
    value == null ? "" : String(value),
  ]);
}
```

- [ ] **Step 5: Run to verify it passes, then type-check**

Run: `node --import tsx --test --test-force-exit scripts/test-admin-coverage.mts` → PASS.
Run: `npm run typecheck:tests` → no errors.

- [ ] **Step 6: Commit**

```bash
git add src/lib/admin-coverage.ts scripts/test-admin-coverage.mts
git commit -m "feat: admin coverage contracts and helpers" -- src/lib/admin-coverage.ts scripts/test-admin-coverage.mts src/lib/api/endpoints.ts src/lib/api/types.ts src/lib/api/query-keys.ts
```

---

### Task 5: Error bodies from blob requests, blob saving, and the hooks

**Files:**
- Create: `src/lib/api/error-body.ts`, `src/lib/save-blob.ts`
- Modify: `src/lib/api/http.ts:58-66` (interceptor becomes async and uses `readErrorBody`)
- Modify: `src/hooks/api/use-admin-questions.ts` (add two hooks)
- Test: `scripts/test-error-body.mts`

**Interfaces:**
- Consumes (Task 4): `exportParams`, `filenameFromDisposition`, `ExportFormat`, `endpoints.admin.questions.{coverage,export}`, `queryKeys.admin.questionCoverage`, `AdminQuestionCoverageOut`.
- Produces:
  - `readErrorBody(data: unknown): Promise<ApiErrorBody>`
  - `saveBlob(blob: Blob, filename: string): void`
  - `useQuestionCoverage()` → TanStack query of `AdminQuestionCoverageOut`
  - `useExportQuestions()` → mutation; `mutateAsync({ subjectId, examType?, examYear?, format }): Promise<string>` resolves to the saved filename.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-error-body.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { readErrorBody } from "../src/lib/api/error-body";

test("a JSON object body passes through", async () => {
  assert.deepEqual(await readErrorBody({ error: "nope" }), { error: "nope" });
});

test("a plain string body becomes the error text", async () => {
  assert.deepEqual(await readErrorBody("gateway down"), { error: "gateway down" });
  assert.deepEqual(await readErrorBody(""), { error: undefined });
  assert.deepEqual(await readErrorBody(undefined), { error: undefined });
});

// A download is requested with responseType "blob", so the backend's JSON
// refusal ("too many questions", "no questions to export") arrives as a Blob.
test("a blob holding JSON is read back as the error body", async () => {
  const blob = new Blob([JSON.stringify({ error: "No questions to export" })], {
    type: "application/json",
  });
  assert.deepEqual(await readErrorBody(blob), { error: "No questions to export" });
});

test("a blob that is not JSON yields an empty body", async () => {
  assert.deepEqual(await readErrorBody(new Blob(["<html>502</html>"])), {});
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-error-body.mts`
Expected: FAIL, cannot find module `../src/lib/api/error-body`.

- [ ] **Step 3: Implement the error-body reader and wire the interceptor**

Create `src/lib/api/error-body.ts`:

```ts
import type { ApiErrorBody } from "./types";

/**
 * The error payload of a failed response. Requests made with
 * `responseType: "blob"` (file downloads) deliver the backend's JSON error as
 * a Blob, so it is read back here instead of being lost.
 */
export async function readErrorBody(data: unknown): Promise<ApiErrorBody> {
  let body = data;
  if (typeof Blob !== "undefined" && body instanceof Blob) {
    try {
      body = JSON.parse(await body.text());
    } catch {
      return {};
    }
  }
  if (body && typeof body === "object") return body as ApiErrorBody;
  return { error: typeof body === "string" && body ? body : undefined };
}
```

In `src/lib/api/http.ts`, add `import { readErrorBody } from "./error-body";` with the other local imports, remove the now-unused `ApiErrorBody` type import if nothing else uses it, and replace lines 58-66:

```ts
http.interceptors.response.use(undefined, async (error: unknown) => {
  if (!(error instanceof AxiosError) || !error.response) throw error;

  const { data, status } = error.response;
  const body = await readErrorBody(data);
  const apiError = new ApiError(status, body);
```

(The remainder of the handler is unchanged.)

- [ ] **Step 4: Run to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-error-body.mts` → PASS.

- [ ] **Step 5: Add `saveBlob`**

Create `src/lib/save-blob.ts`:

```ts
/** Hands an in-memory file to the browser as a download. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoking straight away can cancel the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
```

- [ ] **Step 6: Add the hooks**

In `src/hooks/api/use-admin-questions.ts`: change `import { request } from "@/lib/api/http";` to `import { http, request } from "@/lib/api/http";`, add `AdminQuestionCoverageOut,` to the existing `import type { … } from "@/lib/api/types";` list, and add these two imports:

```ts
import { exportParams, filenameFromDisposition, type ExportFormat } from "@/lib/admin-coverage";
import { saveBlob } from "@/lib/save-blob";
```

Then append the hooks:

```ts
/** What is populated: question counts per subject, exam type and year. */
export function useQuestionCoverage() {
  return useQuery({
    queryKey: queryKeys.admin.questionCoverage(),
    queryFn: ({ signal }) =>
      request<AdminQuestionCoverageOut>({
        method: "GET",
        url: endpoints.admin.questions.coverage,
        realm: "admin",
        signal,
      }),
  });
}

/**
 * Downloads one paper (exam type + year) or, without a year, a subject as a
 * zip. A plain link cannot carry the admin bearer token, so the file is
 * fetched as a blob and saved from memory.
 */
export function useExportQuestions() {
  return useMutation({
    mutationFn: async (input: {
      subjectId: string;
      examType?: string;
      examYear?: number;
      format: ExportFormat;
    }) => {
      const res = await http.request<Blob>({
        method: "GET",
        url: endpoints.admin.questions.export,
        params: exportParams(input),
        responseType: "blob",
        realm: "admin",
      });
      const filename = filenameFromDisposition(
        res.headers["content-disposition"] as string | undefined,
        `questions.${input.format}`,
      );
      saveBlob(res.data, filename);
      return filename;
    },
  });
}
```

- [ ] **Step 7: Verify and commit**

Run: `npm run typecheck:tests` and `npx tsc --noEmit` → no errors; `npm run lint -- src/lib src/hooks` → clean.

```bash
git add src/lib/api/error-body.ts src/lib/save-blob.ts scripts/test-error-body.mts
git commit -m "feat: blob downloads with readable errors, coverage and export hooks" -- src/lib/api/error-body.ts src/lib/save-blob.ts scripts/test-error-body.mts src/lib/api/http.ts src/hooks/api/use-admin-questions.ts
```

---

### Task 6: Split the page into tabs (list unchanged)

**Files:**
- Move: `src/app/admin/(console)/questions/questions-client.tsx` → `all-questions-tab.tsx` (use `git mv`)
- Modify: `all-questions-tab.tsx`
- Create: new `src/app/admin/(console)/questions/questions-client.tsx` (the shell; `page.tsx` keeps importing `QuestionsClient` from it, unchanged)
- Create (stub for this task): `coverage-tab.tsx`

**Interfaces:**
- Consumes (Task 4): `resolveTab`, `tabHref`, `QuestionsTab`.
- Produces: `AllQuestionsTab()` (no props; reads initial filters from `useSearchParams`), `CoverageTab()` (no props), `QuestionsClient()` (unchanged public export).

- [ ] **Step 1: Read the Next docs named in Global Constraints**, then confirm `useSearchParams` still needs a Suspense boundary in this version.

- [ ] **Step 2: Move the list**

```bash
git mv "src/app/admin/(console)/questions/questions-client.tsx" "src/app/admin/(console)/questions/all-questions-tab.tsx"
```

In `all-questions-tab.tsx`:
1. Line 3: `import { Suspense, useCallback, useMemo, useState } from "react";` → `import { useCallback, useMemo, useState } from "react";`
2. Remove the `PageHeader` import (line 10).
3. Delete the block from the `// useSearchParams requires a Suspense boundary…` comment through the end of `QuestionsPageFallback` (lines 86-107).
4. Change `function AdminQuestionsPageInner() {` → `export function AllQuestionsTab() {`.
5. Replace the `<PageHeader … />` element (lines 379-392) and its wrapper so the return begins:

```tsx
  return (
    <div>
      <p className="mb-4 text-sm text-muted">
        {pagination ? `${pagination.total} total` : "Loading…"}
      </p>
```

(`Link` and `buttonClass` stay: the Edit link uses them.)

- [ ] **Step 3: Add a stub coverage tab** so the shell compiles

`coverage-tab.tsx`:

```tsx
"use client";

export function CoverageTab() {
  return <p className="text-sm text-muted">Coverage loads here.</p>;
}
```

- [ ] **Step 4: Write the shell**

New `questions-client.tsx`:

```tsx
"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { buttonClass } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { resolveTab, tabHref, type QuestionsTab } from "@/lib/admin-coverage";
import { cn } from "@/lib/utils";
import { AllQuestionsTab } from "./all-questions-tab";
import { CoverageTab } from "./coverage-tab";

const TABS: { id: QuestionsTab; label: string }[] = [
  { id: "coverage", label: "Coverage" },
  { id: "all", label: "All questions" },
];

// `useSearchParams` requires a Suspense boundary around the Client Component
// that calls it, or a static build fails ("Missing Suspense boundary with
// useSearchParams"). See node_modules/next/dist/docs/01-app/03-api-reference/
// 04-functions/use-search-params.md.
export function QuestionsClient() {
  return (
    <Suspense fallback={<QuestionsPageFallback />}>
      <QuestionsShell />
    </Suspense>
  );
}

function QuestionsPageFallback() {
  return (
    <div>
      <PageHeader title="Questions" description="Loading…" />
      <div className="flex items-center justify-center py-20">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    </div>
  );
}

function QuestionsShell() {
  const tab = resolveTab(useSearchParams());

  return (
    <div>
      <PageHeader
        title="Questions"
        description="What is populated for each subject and year, and the full question list."
        action={
          <div className="flex flex-wrap gap-2">
            <Link href="/admin/questions/import" className={buttonClass("secondary", "md")}>
              Import
            </Link>
            <Link href="/admin/questions/new" className={buttonClass("primary", "md")}>
              New question
            </Link>
          </div>
        }
      />

      <div role="tablist" aria-label="Questions views" className="mb-6 flex gap-1 border-b border-border-strong">
        {TABS.map((item) => (
          <Link
            key={item.id}
            id={`questions-tab-${item.id}`}
            href={tabHref(item.id)}
            replace
            role="tab"
            aria-selected={tab === item.id}
            aria-controls="questions-tabpanel"
            className={cn(
              "-mb-px border-b-2 px-4 py-2 text-sm font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
              tab === item.id
                ? "border-primary text-foreground"
                : "border-transparent text-muted hover:text-foreground",
            )}
          >
            {item.label}
          </Link>
        ))}
      </div>

      {/* Only the active tab is mounted, so opening the list from a coverage
          chip starts it with that paper's filters read from the URL. */}
      <div id="questions-tabpanel" role="tabpanel" aria-labelledby={`questions-tab-${tab}`}>
        {tab === "coverage" ? <CoverageTab /> : <AllQuestionsTab />}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Verify**

Run: `npx tsc --noEmit` and `npm run lint` → clean. Run `npm run dev`, sign in to the admin console, open `/admin/questions`: it shows the Coverage stub; `/admin/questions?tab=all` and `/admin/questions?subjectId=<id>&examYear=2019` show the unchanged list with filters applied, bulk-select and delete dialogs still working.

- [ ] **Step 6: Commit**

```bash
git add "src/app/admin/(console)/questions/questions-client.tsx" "src/app/admin/(console)/questions/all-questions-tab.tsx" "src/app/admin/(console)/questions/coverage-tab.tsx"
git commit -m "refactor: questions page tabs, list moved to its own tab" -- "src/app/admin/(console)/questions"
```

---

### Task 7: Coverage tab, downloads and preview drawer

**Files:**
- Create: `src/app/admin/(console)/questions/export-buttons.tsx`
- Create: `src/app/admin/(console)/questions/paper-preview.tsx`
- Modify (replace stub): `src/app/admin/(console)/questions/coverage-tab.tsx`

**Interfaces:**
- Consumes: `useQuestionCoverage`, `useExportQuestions`, `useAdminQuestions` (Task 5 / existing); `fillBadge`, `paperKey`, `listHref`, `optionEntries`, `ExportFormat` (Task 4); `AdminCoverageSubjectOut`, `AdminCoveragePaperOut` (Task 4); list rows from `GET /admin/api/questions` carry `id, questionNumber, questionText, options, correctAnswer, difficulty` (Task 3).
- Produces: `ExportButtons({ subjectId, examType?, examYear?, allYears?, onError })`, `PaperPreview({ subject, paper, onClose, onError })`, `CoverageTab()`.

- [ ] **Step 1: ExportButtons**

`export-buttons.tsx`:

```tsx
"use client";

import { LuDownload } from "react-icons/lu";
import { Button } from "@/components/ui/button";
import { useExportQuestions } from "@/hooks/api/use-admin-questions";
import type { ExportFormat } from "@/lib/admin-coverage";

/** CSV and JSON downloads for one paper, or (allYears) a zip of a whole subject. */
export function ExportButtons({
  subjectId,
  examType,
  examYear,
  allYears = false,
  onError,
}: {
  subjectId: string;
  examType?: string;
  examYear?: number;
  allYears?: boolean;
  onError: (message: string) => void;
}) {
  const exportQuestions = useExportQuestions();
  const busyFormat = exportQuestions.isPending ? exportQuestions.variables?.format : null;

  async function download(format: ExportFormat) {
    try {
      await exportQuestions.mutateAsync({ subjectId, examType, examYear, format });
    } catch (error) {
      // The backend's message ("No questions to export", "Too many questions…")
      // is more useful than a generic failure.
      onError(
        error instanceof Error && error.message
          ? error.message
          : "Could not reach the server. Nothing was downloaded.",
      );
    }
  }

  const formats: { format: ExportFormat; label: string }[] = [
    { format: "csv", label: allYears ? "All years · CSV (zip)" : "CSV" },
    { format: "json", label: allYears ? "All years · JSON (zip)" : "JSON" },
  ];

  return (
    <div className="flex flex-wrap items-center gap-2">
      {formats.map(({ format, label }) => (
        <Button
          key={format}
          variant="outline"
          size="sm"
          onClick={() => void download(format)}
          disabled={exportQuestions.isPending}
          aria-label={`Download ${label}`}
        >
          <LuDownload className="mr-1.5 h-4 w-4" aria-hidden />
          {busyFormat === format ? "Preparing…" : label}
        </Button>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: PaperPreview**

`paper-preview.tsx`:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { LuChevronLeft, LuChevronRight, LuCircleAlert, LuX } from "react-icons/lu";
import { StatusBanner } from "@/components/admin/status-banner";
import { Button, buttonClass } from "@/components/ui/button";
import { RichText } from "@/components/ui/rich-text";
import { useAdminQuestions } from "@/hooks/api/use-admin-questions";
import { listHref, optionEntries } from "@/lib/admin-coverage";
import type { AdminCoveragePaperOut, AdminCoverageSubjectOut } from "@/lib/api/types";
import { ExportButtons } from "./export-buttons";

interface PreviewQuestion {
  id: string;
  questionNumber: number | null;
  questionText: string;
  options: unknown;
  correctAnswer: string;
  difficulty: string;
}

interface PreviewPagination {
  page: number;
  totalPages: number;
  total: number;
}

const PAGE_SIZE = "20";

/** Side panel previewing one paper's questions, in paper order. Mount it with a `key` per paper. */
export function PaperPreview({
  subject,
  paper,
  onClose,
  onError,
}: {
  subject: AdminCoverageSubjectOut;
  paper: AdminCoveragePaperOut;
  onClose: () => void;
  onError: (message: string) => void;
}) {
  const [page, setPage] = useState(1);
  // The tab behind the overlay also gets the message, but this one is visible.
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const query = useAdminQuestions({
    page: String(page),
    pageSize: PAGE_SIZE,
    subjectId: subject.subjectId,
    examType: paper.examType,
    examYear: String(paper.examYear),
  });

  useEffect(() => {
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const questions = (query.data?.questions ?? []) as unknown as PreviewQuestion[];
  const pagination = (query.data?.pagination ?? null) as unknown as PreviewPagination | null;
  const loading = query.isPending || query.isPlaceholderData;
  const title = `${subject.name} · ${paper.examType} ${paper.examYear}`;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`Preview ${title}`}
        className="relative flex h-full w-full max-w-2xl flex-col border-l border-border-strong bg-card shadow-xl"
      >
        <header className="flex items-start justify-between gap-3 border-b border-border-strong p-4">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold text-foreground">{title}</h2>
            <p className="text-sm text-muted">
              <span className="tabular-nums">{paper.count}</span> questions
            </p>
          </div>
          <Button
            ref={closeRef}
            variant="ghost"
            size="icon-sm"
            onClick={onClose}
            aria-label="Close preview"
          >
            <LuX className="h-4 w-4" aria-hidden />
          </Button>
        </header>

        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-strong px-4 py-3">
          <ExportButtons
            subjectId={subject.subjectId}
            examType={paper.examType}
            examYear={paper.examYear}
            onError={(message) => {
              setDownloadError(message);
              onError(message);
            }}
          />
          <Link
            href={listHref({
              subjectId: subject.subjectId,
              examType: paper.examType,
              examYear: paper.examYear,
            })}
            className={buttonClass("ghost", "sm")}
          >
            Open in list
          </Link>
        </div>

        {downloadError && (
          <StatusBanner
            tone="error"
            title="Download failed"
            message={downloadError}
            className="mx-4 mt-4"
          />
        )}

        <div className="flex-1 overflow-y-auto p-4" aria-busy={loading}>
          {query.isError ? (
            <StatusBanner
              tone="error"
              title="Could not load this paper"
              action={
                <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
                  Retry
                </Button>
              }
            />
          ) : loading ? (
            <div className="flex items-center justify-center py-20">
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
          ) : questions.length === 0 ? (
            <div className="py-16 text-center">
              <LuCircleAlert className="mx-auto mb-3 h-10 w-10 text-muted" aria-hidden />
              <p className="text-muted">No questions found for this paper.</p>
            </div>
          ) : (
            <ol className="space-y-5">
              {questions.map((question, index) => (
                <li key={question.id} className="text-sm">
                  <div className="flex gap-2">
                    <span className="w-7 shrink-0 text-right tabular-nums text-muted">
                      {question.questionNumber ?? (page - 1) * Number(PAGE_SIZE) + index + 1}.
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="break-words text-foreground">
                        <RichText text={question.questionText} />
                      </div>
                      <ul className="mt-2 space-y-1">
                        {optionEntries(question.options).map(([key, text]) => (
                          <li
                            key={key}
                            className={
                              key.toUpperCase() === String(question.correctAnswer).toUpperCase()
                                ? "font-medium text-tone-green-ink"
                                : "text-muted"
                            }
                          >
                            <span className="mr-1.5">{key}.</span>
                            <RichText text={text} />
                          </li>
                        ))}
                      </ul>
                      <p className="mt-1.5 text-xs text-muted">
                        Answer: {question.correctAnswer} · {question.difficulty}
                      </p>
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>

        {pagination && pagination.totalPages > 1 && (
          <nav
            aria-label="Preview pagination"
            className="flex items-center justify-between border-t border-border-strong px-4 py-3"
          >
            <p className="text-sm text-muted">
              Page <span className="tabular-nums">{pagination.page}</span> of{" "}
              <span className="tabular-nums">{pagination.totalPages}</span>
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="icon"
                aria-label="Previous page"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={pagination.page <= 1}
              >
                <LuChevronLeft className="h-4 w-4" aria-hidden />
              </Button>
              <Button
                variant="outline"
                size="icon"
                aria-label="Next page"
                onClick={() => setPage((p) => Math.min(pagination.totalPages, p + 1))}
                disabled={pagination.page >= pagination.totalPages}
              >
                <LuChevronRight className="h-4 w-4" aria-hidden />
              </Button>
            </div>
          </nav>
        )}
      </aside>
    </div>
  );
}
```

Check `RichText`'s prop is `text` (it is used as `<RichText text={q.questionText} />` in the list) and that `StatusBanner` accepts a missing `message` (it is optional in its props).

- [ ] **Step 3: CoverageTab**

Replace `coverage-tab.tsx`:

```tsx
"use client";

import { useCallback, useState } from "react";
import { LuCircleAlert } from "react-icons/lu";
import { StatusBanner } from "@/components/admin/status-banner";
import { Button } from "@/components/ui/button";
import { useQuestionCoverage } from "@/hooks/api/use-admin-questions";
import { fillBadge, paperKey } from "@/lib/admin-coverage";
import type { AdminCoveragePaperOut, AdminCoverageSubjectOut } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { ExportButtons } from "./export-buttons";
import { PaperPreview } from "./paper-preview";

export function CoverageTab() {
  const coverage = useQuestionCoverage();
  const [selected, setSelected] = useState<{
    subject: AdminCoverageSubjectOut;
    paper: AdminCoveragePaperOut;
  } | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const closePreview = useCallback(() => setSelected(null), []);

  const subjects = coverage.data?.subjects ?? [];

  if (coverage.isError) {
    return (
      <StatusBanner
        tone="error"
        title="Could not load coverage"
        message={
          coverage.error instanceof Error
            ? coverage.error.message
            : "Could not reach the server. Check your connection and retry."
        }
        action={
          <Button variant="outline" size="sm" onClick={() => void coverage.refetch()}>
            Retry
          </Button>
        }
      />
    );
  }

  if (coverage.isPending) {
    return (
      <div className="flex items-center justify-center py-20" role="status" aria-label="Loading coverage">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  return (
    <div>
      {downloadError && (
        <StatusBanner
          tone="error"
          title="Download failed"
          message={downloadError}
          className="mb-4"
        />
      )}

      {subjects.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-12 text-center">
          <LuCircleAlert className="mx-auto mb-3 h-10 w-10 text-muted" aria-hidden />
          <p className="text-muted">No past-paper questions have been populated yet.</p>
        </div>
      ) : (
        <ul className="space-y-4">
          {subjects.map((subject) => (
            <li
              key={subject.subjectId}
              className="rounded-lg border border-border-strong bg-card p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="text-base font-semibold text-foreground">{subject.name}</h2>
                  <p className="text-sm text-muted">
                    <span className="tabular-nums">{subject.total}</span> questions across{" "}
                    <span className="tabular-nums">{subject.papers.length}</span> papers
                  </p>
                </div>
                <ExportButtons
                  subjectId={subject.subjectId}
                  allYears
                  onError={setDownloadError}
                />
              </div>

              <ul className="mt-3 flex flex-wrap gap-2">
                {subject.papers.map((paper) => {
                  const badge = fillBadge(paper.fetchStatus);
                  return (
                    <li key={paperKey(subject.subjectId, paper.examType, paper.examYear)}>
                      <button
                        type="button"
                        onClick={() => {
                          setDownloadError(null);
                          setSelected({ subject, paper });
                        }}
                        aria-label={`Preview ${subject.name} ${paper.examType} ${paper.examYear}, ${paper.count} questions`}
                        className="flex items-center gap-2 rounded-lg border border-border bg-secondary/40 px-3 py-1.5 text-sm hover:bg-secondary focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
                      >
                        <span className="font-medium text-foreground">
                          {paper.examType} {paper.examYear}
                        </span>
                        <span className="tabular-nums text-muted">{paper.count}</span>
                        {badge && (
                          <span
                            className={cn(
                              "rounded px-1.5 py-0.5 text-[11px] font-medium",
                              badge.tone === "amber"
                                ? "bg-tone-amber-soft text-tone-amber-ink"
                                : "bg-tone-red-soft text-tone-red-ink",
                            )}
                          >
                            {badge.label}
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
        </ul>
      )}

      {selected && (
        <PaperPreview
          key={paperKey(selected.subject.subjectId, selected.paper.examType, selected.paper.examYear)}
          subject={selected.subject}
          paper={selected.paper}
          onClose={closePreview}
          onError={setDownloadError}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 4: Verify types and lint**

Run: `npx tsc --noEmit`, `npm run lint`, `npm run typecheck:tests`, `npm test` → clean / all pass.

- [ ] **Step 5: Commit**

```bash
git add "src/app/admin/(console)/questions/export-buttons.tsx" "src/app/admin/(console)/questions/paper-preview.tsx"
git commit -m "feat: coverage tab with paper preview and downloads" -- "src/app/admin/(console)/questions"
```

---

### Task 8: End-to-end verification

No new files. Do not claim completion without this evidence (superpowers:verification-before-completion).

- [ ] **Step 1: Backend full suite.** `.venv\Scripts\python -m pytest -q` → all pass; note counts.
- [ ] **Step 2: Frontend gates.** `npm run lint`, `npx tsc --noEmit`, `npm run typecheck:tests`, `npm test`, `npm run build` → all succeed (the build catches a missing Suspense boundary).
- [ ] **Step 3: Run both apps** (backend uvicorn on :8000, `npm run dev`), sign in as an admin, and check in a real browser:
  - `/admin/questions` opens **Coverage**; counts per chip match `All questions` filtered to that subject/exam/year.
  - A paper still being filled shows **Filling**; a fully drawn one shows no badge.
  - Clicking a chip opens the drawer in paper order with options and the green correct answer; paging works; Esc and the overlay close it; focus lands on the close button.
  - **Open in list** lands on **All questions** with subject, exam and year pre-filtered.
  - Download a paper as CSV (open in Excel: accents and `π` intact, a cell starting `=` shows with a leading `'`) and as JSON; download a subject zip and list its entries.
  - The saved filename is `mathematics-JAMB-2019.csv`, not the fallback `questions.csv` (proves `Content-Disposition` is exposed over CORS).
  - Export a subject/filter with nothing to export (or temporarily lower `EXPORT_ROW_CAP`): the banner shows the backend's message, not a generic error.
  - Expire the admin token (delete the cookie) and click download: redirected to `/admin/login?reason=session`.
  - Check the admin audit log shows `question.export` entries.
- [ ] **Step 4: Report** results honestly, including anything not exercised (for example, no real provider-filling paper available to see the badge).
