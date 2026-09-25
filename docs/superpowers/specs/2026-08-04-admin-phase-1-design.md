# Admin Console — Phase 1: Foundation, Overview & Question Management

Date: 2026-08-04
Status: Draft
Phase: 1 of 5 (see "Roadmap" below)

## Problem

The admin section is two files — `src/app/admin/layout.tsx` and
`src/app/admin/questions/page.tsx` — and it is neither honest about system
state nor operable without a mouse.

**It lies about failure.** A failed list fetch falls through to the
`questions.length === 0` branch and renders "No questions found."
(`src/app/admin/questions/page.tsx:123`), so a 500 is indistinguishable from an
empty database. A failed delete is swallowed entirely: the `res.ok` check has no
`else` (`:62`), so the row simply stays and nothing is said.

**The delete button silently fails for the questions that matter most.**
`AssessmentQuestion.question` (`prisma/schema.prisma:470`) and
`QuestionResponse.question` (`:505`) are required relations declared with no
`onDelete`, so Prisma defaults to `Restrict`. Any question sitting in an
assessment, or that a student has ever answered, cannot be deleted — the
database rejects it, the route returns a generic
`"Failed to delete question"` 500 (`src/app/api/admin/questions/route.ts:109`),
and the UI shows nothing at all.

**Its filters do not work as written.** `search` is a dependency of the
`fetchQuestions` callback (`src/app/admin/questions/page.tsx:48`), which the
effect re-runs on every change (`:55`). The list therefore refetches on every
keystroke, and the `handleSearch` submit handler (`:70`) is dead code.

**It hides content on mobile.** The bottom nav is `fixed`
(`src/app/admin/layout.tsx:68`) but `<main>` carries no bottom padding (`:60`),
so the nav covers the last table row. The dashboard layout gets this right
(`pb-20 lg:pb-0`, `src/app/(dashboard)/layout.tsx:45`).

**It is not accessible.** No `aria-current` on nav links (the student sidebar
has it — `src/components/ui/sidebar.tsx:65`), no skip link, no label on the exam
`<select>` (`:100`) or the search input (`:91`), no table `caption` or
`scope="col"`, no accessible name on the icon-only delete and pagination
buttons, no live region announcing result counts, and no `focus-visible` styling
anywhere.

**Half the backend is unreachable.** `POST /api/admin/questions/import` accepts
up to 500 questions (`src/lib/validators.ts:126`) and has no UI. There is no way
to create or edit a single question at all — the only writes available are bulk
import and delete.

## Goal

A **grounded, accessible admin console** whose Phase 1 covers the shell, a
real-data overview, and complete question management. Grounded means every
control maps to a real capability, every failure is visible, and every number
is queried rather than invented. Accessible means fully operable by keyboard and
comprehensible to a screen reader.

## Principles

| # | Principle | How Phase 1 honours it |
|---|---|---|
| 1 | **Never report success or emptiness you have not verified.** | Fetch failure renders an error banner with the server's message and a Retry action, never the empty state. Delete failure restores the row and names the reason. |
| 2 | **Refuse destructive work you cannot do safely.** | Deletes are dependency-checked *before* they are attempted; the dialog states exact counts of dependent responses and assessments. |
| 3 | **One source of truth for validation.** | The same zod schema validates in the browser, in the API route, and in the import parser. |
| 4 | **Enforce the invariants the data model cannot.** | `correctAnswer` must be a key of `options`; `topicId` must belong to `subjectId`. |
| 5 | **Every mutation is attributable.** | An `AdminAudit` row is written for every create, update, delete and import. |
| 6 | **Density is the admin's visual language.** | Distinct from the student app through structure — tighter rows, stronger rules, tabular figures — using only existing design tokens. |

## Non-goals for Phase 1

Curriculum CRUD (subjects, topics, edges, subtopics), lesson authoring, user and
role management, and operational read screens. These are Phases 2–5 and get
their own specs.

## Architecture

### Shared server modules

**`src/lib/admin-guard.ts`** — `requireAdmin()` returns either the admin user or
a `NextResponse` to return immediately. The identical session-then-role-lookup
block is currently copy-pasted into three handlers
(`src/app/api/admin/questions/route.ts:12`, `:87`,
`src/app/api/admin/questions/import/route.ts:14`) and every new route below would
repeat it a fourth through ninth time.

**`src/lib/admin-audit.ts`** — `recordAudit({ actorId, action, entity, entityId, summary })`,
called by every mutation.

### Pure logic modules (no Prisma, no React — unit tested)

Following the repository convention of `node:test` suites over pure modules
(`package.json:11`):

| Module | Responsibility | Suite |
|---|---|---|
| `src/lib/admin-question.ts` | Question invariants: correct-answer-in-options, topic-belongs-to-subject, option-key normalization | `scripts/test-admin-question.mts` |
| `src/lib/admin-import.ts` | Parse pasted/uploaded JSON, normalize rows, per-row error report, 500-row cap | `scripts/test-admin-import.mts` |
| `src/lib/admin-stats.ts` | Shape overview aggregates into display rows, including zero-question subjects and percentage-of-total | `scripts/test-admin-stats.mts` |

The topic-belongs-to-subject check currently lives inline in the import route
(`src/app/api/admin/questions/import/route.ts:97`); it moves into
`admin-question.ts` and the import route calls it, so create, update and import
cannot diverge.

### Validation schemas

Added to `src/lib/validators.ts` beneath the empty
`// ─── Questions (Admin) ───` header already present at line 100:

- `adminQuestionCreateSchema` — id-based (`subjectId`, `topicId`), unlike the
  import schema which is code/slug-based (`bulkImportQuestionSchema:104`),
  because the form works from populated selects.
- `adminQuestionUpdateSchema` — the create schema, partial, with at least one
  field required.
- `adminQuestionDeleteSchema` — `{ ids: string[] }`, 1–100 ids.

Both create and update are refined by the shared invariants from
`admin-question.ts`.

### Schema change

```prisma
model AdminAudit {
  id        String   @id @default(cuid())
  actorId   String
  actor     User     @relation(fields: [actorId], references: [id])
  action    String   // "question.create" | "question.update" | "question.delete" | "question.import"
  entity    String   // "Question"
  entityId  String?
  summary   String   @db.Text
  createdAt DateTime @default(now())

  @@index([entity, entityId])
  @@index([actorId, createdAt])
}
```

Requires a migration and a back-relation on `User`. No existing model is
altered; nothing student-facing changes.

### API routes

| Route | Behaviour |
|---|---|
| `POST /api/admin/questions` | Create. Validates with `adminQuestionCreateSchema`, writes audit, revalidates `CATALOGUE_TAG`. |
| `GET /api/admin/questions/[id]` | Single question with subject and topic, for the edit form. |
| `PATCH /api/admin/questions/[id]` | Partial update, same validation, audit, revalidate. |
| `GET /api/admin/questions/[id]/usage` | `{ responseCount, assessmentCount, deletable }` — powers the confirm dialog. |
| `DELETE /api/admin/questions` | Accepts `?id=` (unchanged, for compatibility) or a JSON body of `ids`. Counts dependents first and refuses those with any, returning `{ deleted: string[], refused: [{ id, responseCount, assessmentCount }] }`. |

`GET /api/admin/questions` is unchanged except for the shared guard.

Every mutating route calls `revalidateTag(CATALOGUE_TAG, "max")`. Delete and
import already do (`route.ts:105`, `import/route.ts:158`); create and update must,
or the cached per-subject question counts go stale.

## Screens

### `/admin` — overview (new, server component)

Queried, never estimated:

- Total questions; total subjects; total topics.
- Questions per subject, with percentage of total, and **subjects with zero
  questions** listed explicitly as a gap.
- Questions per exam type, per difficulty, and the set of exam years covered.
- Questions with no `topicId` — unlinked content, a real coverage gap.

Every figure is a link into `/admin/questions` with the corresponding filter
applied. When the database is empty the page says so and links to import; it
never renders zeroes as if they were measurements.

### `/admin/questions` — list

Retains browse, search, exam filter and single delete. Changes:

- Split `query` (input state) from `appliedQuery` (fetch key) so the submit
  handler is meaningful and typing does not refetch. An `AbortController`
  discards responses from superseded requests.
- Error banner (`role="alert"`) with the server message and Retry on any fetch
  failure. The empty state is reserved for a genuinely empty result.
- Row checkboxes, a header select-all bound to the current page, and a bulk
  delete action. The result panel names refused ids and why.
- Delete opens `ConfirmDialog`. For a **single** question the dialog fetches
  `/usage` first and shows its dependent counts; when the question is
  undeletable it says so and offers no confirm button that would fail. For a
  **bulk** selection the dialog does not pre-fetch per row — it confirms the
  count, submits, and reports the server's `refused` list afterwards, since the
  `DELETE` route performs the same dependency check authoritatively.
- Subject and difficulty filters added alongside exam type — the API already
  supports both (`route.ts:28`, `:31`).

### `/admin/questions/new` and `/admin/questions/[id]/edit`

One `QuestionForm` component in both routes. Fields mirror the `Question` model
(`prisma/schema.prisma:405`): subject select, topic select filtered to the
chosen subject, exam type, exam year, question number, question text, question
type, dynamic option rows (minimum four for `OBJECTIVE`), correct-answer picker
bound to the entered option keys, explanation, difficulty, marks, time estimate,
and optional image URLs. Client-side validation uses the same zod schema as the
route, so the two cannot disagree. Errors render per-field with
`aria-describedby` and the form summary is a live region.

### `/admin/questions/import`

Paste JSON or choose a `.json` file → parsed and validated by
`admin-import.ts` → preview showing row count, first rows, and per-row errors
*before* anything is sent. The 500-row cap is stated and enforced in the UI
rather than surfacing as a 400. A `skipDuplicates` toggle maps to the existing
body field. The result panel reports imported / skipped / errored counts and
each error's index and reason verbatim from the API, in a live region. The
expected JSON shape is documented inline from the real schema fields.

## Shell and accessibility

- Nav definitions move to `src/lib/admin-nav.ts`: Overview, Questions, Import.
- `AdminNav` client component using `usePathname` to set `aria-current="page"`,
  mirroring `src/components/ui/sidebar.tsx:65`. The layout stays a server
  component and keeps its role guard.
- Skip link to `<main id="admin-main" tabIndex={-1}>`; `aria-label` on both the
  desktop and mobile `<nav>` elements.
- `pb-20 lg:pb-0` on `<main>` so the fixed mobile nav stops covering content.
- Labels for the search input and every filter select; `<caption class="sr-only">`
  and `scope="col"` on the table; accessible names on icon-only buttons
  (`Delete question: <first 60 chars>`); `aria-busy` on the results region;
  `role="status"` announcing "Showing 20 of 481 questions"; pagination wrapped in
  `<nav aria-label="Pagination">` with text labels, not bare chevrons.
- `ConfirmDialog` in `src/components/admin/`, modelled on the existing dialog
  pattern (`src/components/path/pretest-dialog.tsx:167`) and adding a focus
  trap, Escape-to-close, and focus restoration to the invoking control.
- `focus-visible` rings on every interactive element; there are none today.

## Visual direction

Admin diverges from the student app through density and structure, using only
existing CSS custom properties (`src/app/globals.css:19`) so both colour schemes
follow automatically: `rounded-lg` rather than `rounded-xl`/`2xl`,
`--app-border-strong` for table rules, `tabular-nums` on every count, year and
page number, 11px uppercase tracked column headings, flat `bg-card` surfaces
without `shadow-lift`, and tighter row height. Nav active state is a left rule
rather than the dashboard's soft pill.

No new tokens, no new colours.

## Testing

- Three `node:test` suites over the pure modules, registered in the `test`
  script in `package.json:11`.
- `admin-question` covers: correct answer absent from options; correct answer
  present; theory questions with no options; topic from a different subject;
  duplicate option keys.
- `admin-import` covers: valid batch; malformed JSON; non-array root; a row
  failing each required field; a batch over 500 rows; `skipDuplicates` default.
- `admin-stats` covers: empty database; a subject with zero questions; unlinked
  questions counted; percentages summing correctly with rounding.
- Manual verification: `npm run lint`, `npx tsc --noEmit`, `npm test`, and a
  keyboard-only walkthrough of overview → create → edit → bulk delete → import
  in the running application.

## Roadmap

Phase 1 is this document. Later phases each get their own spec:

2. **Curriculum tree** — Subject, CurriculumLevel, Topic, Subtopic and the
   `TopicEdge` prerequisite graph, with cycle detection.
3. **Lessons and resources** — Lesson, LessonResource, SubjectResource,
   including a structured content editor.
4. **People** — user search, role changes, school assignment, deactivation, plus
   School CRUD. Highest privilege risk; lands after the patterns are proven.
5. **Operations and configuration** — read screens over attempts, progress and
   flashcard decks; Achievement definitions; and the audit log viewer over the
   `AdminAudit` rows Phase 1 begins writing.

The dependency-checked delete policy and the audit trail are established here in
Phase 1 so later phases inherit them rather than retrofit them.
