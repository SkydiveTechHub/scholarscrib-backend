# Admin question coverage and export

## Goal

On the admin console Questions page, let an admin see which exam years are
populated for each subject (including questions ingested from the provider),
preview a given paper's questions, and download them.

## Decisions (agreed)

- "Populated" means rows in our own `Question` table, not what the provider
  offers (the student-side `/api/questions/coverage/*` endpoints report the
  provider and are unrelated).
- Download formats: CSV and JSON.
- Download scope: one paper (subject + exam type + year), or a whole subject
  as a zip with one file per exam-type/year.
- Each paper shows its provider fill status as a badge.
- The page gets two tabs: **Coverage** (default) and **All questions** (the
  current list, unchanged).

## Out of scope

- PDF / printable export.
- Editing from the coverage view (the existing list and form keep that).
- Questions with no `exam_year` (topic-bank questions) in coverage; they stay
  in the All questions tab.

## Backend (`scholarscrib-backend`)

Both endpoints live in `app/api/admin/questions.py` under `require_admin`.
They must be registered before `/{question_id}` so `coverage` and `export`
are not captured as ids.

### `GET /admin/api/questions/coverage`

One `GROUP BY Question.subject_id, exam_type, exam_year` query (served by
`Question_subject_exam_year_idx`), joined to `Subject`, with a left join to
`ProviderFetch` on `(subject_id, exam_type, exam_year)` for the status.
Rows with `exam_year IS NULL` are excluded.

```json
{
  "subjects": [
    {
      "subjectId": "...", "name": "Mathematics", "code": "MTH",
      "total": 1234,
      "papers": [
        {"examType": "JAMB", "examYear": 2019, "count": 60,
         "fetchStatus": "SATURATED"}
      ]
    }
  ]
}
```

- `papers` sorted by `examYear` desc, then `examType`.
- `fetchStatus` is `PENDING`, `SATURATED`, `FAILED`, or `null` when the paper
  was added by hand or imported (no fetch row) or only fetched by a provider
  other than the configured one (`settings.question_provider`); a paper can
  have a fetch row per provider, so only the active provider's row counts.
- Subjects with no dated questions are omitted.

### `GET /admin/api/questions/export`

Query: `subjectId` (required), `examType`, `examYear` (optional),
`format=csv|json` (required).

- `examYear` given (with `examType`): one file, `Content-Type` `text/csv` or
  `application/json`, `Content-Disposition: attachment` named
  `{subject-slug}-{examType}-{year}.{ext}`.
- `examYear` omitted: `application/zip`, one file per `(examType, examYear)`
  present, same naming, archive named `{subject-slug}.zip`.
- Fields per question: `questionNumber`, `examType`, `examYear`, `topic`
  (title), `questionType`, `questionText`, `questionImageUrl`, `options`,
  `correctAnswer`, `explanation`, `explanationImageUrl`, `difficulty`, `marks`
  (the image URLs keep diagram questions meaningful). Ordered by `question_number`, then id.
- CSV: UTF-8 with BOM (so Excel reads it correctly); `options` is a JSON
  string in one cell; any cell beginning with `=`, `+`, `-`, `@`, tab or CR is
  prefixed with `'` to prevent formula injection. JSON: an array of objects
  with `options` as an object (key to text), as stored.
- Row cap of 20,000 per request; over it returns 400 asking the admin to
  narrow the filter. The zip is built in memory.
- 404 if the subject does not exist; 400 for an unknown format; an empty
  selection returns 404 "No questions to export".
- Records an audit entry (`question.export`) through `RecordAuditService`
  with subject, filters, format and row count.

### Code placement

- `app/database/repositories/question.py`: `coverage_rows(session)` and
  `for_export(session, subject_id, exam_type, exam_year)`.
- `app/services/question_coverage.py`: `GetQuestionCoverageService`,
  `ExportQuestionsService` (not `console.py`, which is already large);
  serialisation and grouping helpers in `app/services/question_export.py`.
- `main.py`: add `Content-Disposition` to the CORS `expose_headers`, or the
  browser cannot read the download filename.
- The admin list orders by question number once a year is filtered, and its
  rows gain `questionNumber`, so the preview shows a paper in paper order.
- `app/api/responses.py`: `QuestionCoverageOut` and nested models.

### Backend tests

`tests/test_admin_question_coverage.py`, in the style of
`tests/test_provider_bank_page.py`:

- grouping and counts across subjects, exam types and years; undated
  questions excluded; ordering
- `fetchStatus` for a provider-ingested paper and `null` for a manual one
- CSV and JSON shapes for one paper; formula-injection prefixing; BOM
- zip contents and filenames for a whole subject
- 401/403 without an admin; 404 unknown subject; 404 empty; 400 bad format
  and over-cap
- audit entry written

## Frontend (`scholarscrib-frontend`)

Before writing any code, read the relevant guide in
`node_modules/next/dist/docs/` (per the repo's `AGENTS.md`; this Next.js
version has breaking changes).

- `src/lib/api/endpoints.ts`: add `admin.questions.coverage` and
  `admin.questions.export`; `src/lib/api/types.ts`: coverage types;
  `src/lib/api/query-keys.ts`: a coverage key under `queryKeys.admin`.
- `src/hooks/api/use-admin-questions.ts`: `useQuestionCoverage()` and
  `useExportQuestions()`. The export hook calls `request` with
  `responseType: "blob"` (the bearer token comes from a cookie, so a plain
  `<a href>` would be unauthenticated), reads the filename from
  `Content-Disposition`, and saves via a temporary object URL. Creating,
  updating, deleting or importing questions invalidates the coverage key.
- `questions-client.tsx` becomes a thin shell with two tabs; the current list
  moves, unchanged, into the **All questions** tab (new file, e.g.
  `all-questions-tab.tsx`). Tab and filter params stay in the URL so the
  existing `?subjectId=&examType=&examYear=` deep links keep working and open
  the All questions tab.
- **Coverage tab** (`coverage-tab.tsx`): a card per subject with total count, a
  chip per paper (`JAMB · 2019 · 60`), a badge for `PENDING` ("Filling") and
  `FAILED`, and a **Download all** menu (CSV / JSON zip). Loading, empty and
  error states use the existing `StatusBanner` / spinner patterns.
- **Preview panel** (`paper-preview.tsx`): clicking a chip opens a panel that
  lists that paper's questions via the existing `GET /admin/api/questions`
  (subject, exam type, year filters, paginated), rendered with `RichText`.
  It has **CSV** and **JSON** download buttons and an "Open in list" link.
- Download buttons show a busy state and surface failures (including the
  over-cap 400 message) in a `StatusBanner`.

### Frontend checks

The repo's lint and type-check; manual verification in the browser of: tab
default and deep links, chip counts matching the list, preview paging, each
download type opening correctly (CSV in Excel, JSON, zip contents), and an
unauthenticated/expired-session download failing cleanly.

## Risks

- Large subjects: the cap bounds memory, but a subject with many years may hit
  it; the message tells the admin to pick a single exam type or year.
- `ProviderFetch` is unique per `(provider, cacheKey)`, so a paper has at most
  one row for the configured provider; fetch rows of other providers are
  ignored. A paper fetched only by a previous provider shows no badge.
