# Admin Console Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a grounded, accessible admin console covering the shell, a real-data overview, and complete question management (create, edit, bulk delete, import), replacing a surface that silently fails and cannot be operated by keyboard.

**Architecture:** Pure logic modules (`src/lib/admin-*.ts`) hold every rule and are unit-tested with `node:test`; thin route handlers under `src/app/api/admin/` compose those modules behind a single `requireAdmin()` guard and write an `AdminAudit` row per mutation; React screens under `src/app/admin/` consume the routes and share primitives in `src/components/admin/`. Validation schemas live once in `src/lib/validators.ts` and are used by browser, route, and import parser alike.

**Tech Stack:** Next.js 16.2.11 (App Router), React 19.2.4, TypeScript 5, Prisma 6, zod 4, Tailwind with CSS custom properties, `node:test` via `tsx`.

**Spec:** `docs/superpowers/specs/2026-08-04-admin-phase-1-design.md`

## Global Constraints

- **Read the Next.js docs before writing route or page code.** Per `AGENTS.md`, this is not the Next.js in your training data. Relevant files: `node_modules/next/dist/docs/01-app/01-getting-started/15-route-handlers.md`, `.../07-mutating-data.md`, `.../09-revalidating.md`.
- **Dynamic route handlers take `params` as a Promise.** Use either the repo's existing form `{ params }: { params: Promise<{ id: string }> }` (see `src/app/api/learning-path/topics/[topicId]/pretest/route.ts:40`) or the global `RouteContext<'/api/admin/questions/[id]'>` helper. Always `await` it.
- **No new design tokens and no new colours.** Use only the CSS custom properties already defined in `src/app/globals.css:19-115`. Both colour schemes must work.
- **No new dependencies.** Everything needed is already installed.
- **Pure modules import neither Prisma nor React.** `src/lib/admin-question.ts`, `src/lib/admin-import.ts` and `src/lib/admin-stats.ts` may import zod and nothing else from the app.
- **Every mutating route calls `revalidateTag(CATALOGUE_TAG, "max")`** — imported from `@/lib/catalogue`. The per-subject question counts are cached (`src/lib/catalogue.ts:15`).
- **Every mutating route writes an audit row** via `recordAudit()`.
- **Admin visual language:** `rounded-lg` (not `xl`/`2xl`), `border-strong` for table rules, `tabular-nums` on every numeral, `text-[11px] font-semibold uppercase tracking-wider` column headings, flat `bg-card` without `shadow-lift`.
- **There is no React component test harness in this repository** (no vitest, jest, or testing-library — see `package.json:11`). Automated tests in this plan cover the pure modules only. UI tasks are verified by `npx tsc --noEmit`, `npm run lint`, and the explicit manual keyboard/screen-reader checks written into each task. Do not add a test framework as part of this plan.
- **Commit after every task.** Branch is `admin-console-phase-1`.

---

### Task 1: Admin guard, audit model, and audit helper

Removes the session-then-role block copy-pasted into three handlers, and lays the audit trail that Phases 2–5 inherit.

**Files:**
- Create: `src/lib/admin-guard.ts`
- Create: `src/lib/admin-audit.ts`
- Modify: `prisma/schema.prisma` (add `AdminAudit`, add back-relation to `User` at `:156`)
- Modify: `src/app/api/admin/questions/route.ts:12-23`, `:87-94`
- Modify: `src/app/api/admin/questions/import/route.ts:14-26`

**Interfaces:**
- Produces: `requireAdmin(): Promise<{ ok: true; actor: { id: string } } | { ok: false; response: NextResponse }>`
- Produces: `recordAudit(entry: { actorId: string; action: AuditAction; entity: string; entityId?: string | null; summary: string }): Promise<void>`
- Produces: `type AuditAction = "question.create" | "question.update" | "question.delete" | "question.import"`

- [ ] **Step 1: Add the `AdminAudit` model**

In `prisma/schema.prisma`, append at the end of the file:

```prisma
// ─── Admin ────────────────────────────────────────────────

model AdminAudit {
  id        String   @id @default(cuid())
  actorId   String
  actor     User     @relation(fields: [actorId], references: [id], onDelete: Cascade)
  action    String // "question.create" | "question.update" | "question.delete" | "question.import"
  entity    String // "Question"
  entityId  String?
  summary   String   @db.Text
  createdAt DateTime @default(now())

  @@index([entity, entityId])
  @@index([actorId, createdAt])
}
```

Then add the back-relation to the `User` model (`prisma/schema.prisma:156`), alongside its other relation fields:

```prisma
  adminAudits AdminAudit[]
```

- [ ] **Step 2: Generate the migration**

Run: `npx prisma migrate dev --name add_admin_audit`
Expected: a new folder under `prisma/migrations/`, and `prisma generate` runs automatically so `db.adminAudit` exists on the client.

If the dev database is unreachable, run `npx prisma generate` and create the migration later — but do not proceed past Task 6 without it, since the routes write audit rows.

- [ ] **Step 3: Write the guard**

Create `src/lib/admin-guard.ts`:

```ts
import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/lib/db";

export type AdminActor = { id: string };

export type AdminGuardResult =
  | { ok: true; actor: AdminActor }
  | { ok: false; response: NextResponse };

/**
 * Resolves the signed-in admin, or the response the caller should return
 * instead. The session lookup and the role lookup were previously duplicated
 * verbatim in every admin handler, so a fix to one never reached the others.
 */
export async function requireAdmin(): Promise<AdminGuardResult> {
  const session = await auth();
  if (!session?.user?.id) {
    return {
      ok: false,
      response: NextResponse.json({ error: "Unauthorized" }, { status: 401 }),
    };
  }

  const user = await db.user.findUnique({
    where: { id: session.user.id },
    select: { role: true },
  });
  if (user?.role !== "ADMIN") {
    return {
      ok: false,
      response: NextResponse.json(
        { error: "Admin access required" },
        { status: 403 },
      ),
    };
  }

  return { ok: true, actor: { id: session.user.id } };
}
```

- [ ] **Step 4: Write the audit helper**

Create `src/lib/admin-audit.ts`:

```ts
import { db } from "@/lib/db";

export type AuditAction =
  | "question.create"
  | "question.update"
  | "question.delete"
  | "question.import";

export type AuditEntry = {
  actorId: string;
  action: AuditAction;
  entity: string;
  entityId?: string | null;
  summary: string;
};

/**
 * Records an admin mutation. Deliberately swallows its own failures: losing an
 * audit row must never turn a successful edit into an error the admin sees.
 */
export async function recordAudit(entry: AuditEntry): Promise<void> {
  try {
    await db.adminAudit.create({
      data: {
        actorId: entry.actorId,
        action: entry.action,
        entity: entry.entity,
        entityId: entry.entityId ?? null,
        summary: entry.summary,
      },
    });
  } catch (error) {
    console.error("Failed to record admin audit entry:", error);
  }
}
```

- [ ] **Step 5: Adopt the guard in the three existing handlers**

In `src/app/api/admin/questions/route.ts`, replace the block at `:12-23` (and the identical one at `:87-94`) with:

```ts
    const guard = await requireAdmin();
    if (!guard.ok) return guard.response;
```

Add `import { requireAdmin } from "@/lib/admin-guard";` and drop the now-unused `auth` import if nothing else in the file uses it. Do the same in `src/app/api/admin/questions/import/route.ts:14-26`, keeping `guard.actor.id` available — the import handler will use it in Task 6.

- [ ] **Step 6: Verify nothing broke**

Run: `npx tsc --noEmit`
Expected: no errors. `db.adminAudit` resolving proves the Prisma client regenerated.

Run: `npm run lint`
Expected: no new warnings.

- [ ] **Step 7: Commit**

```bash
git add prisma/schema.prisma prisma/migrations src/lib/admin-guard.ts src/lib/admin-audit.ts src/app/api/admin
git commit -m "feat(admin): add shared admin guard and audit trail"
```

---

### Task 2: Question invariants module

The two rules the database cannot express. Import enforces the second one inline today (`src/app/api/admin/questions/import/route.ts:97`); this centralises it so create, update and import cannot diverge.

**Files:**
- Create: `src/lib/admin-question.ts`
- Create: `scripts/test-admin-question.mts`
- Modify: `package.json:11` (register the suite)

**Interfaces:**
- Produces: `type QuestionOptions = Record<string, string>`
- Produces: `type InvariantIssue = { field: string; message: string }`
- Produces: `normalizeOptions(options: QuestionOptions | null | undefined): { options: QuestionOptions | null; issues: InvariantIssue[] }`
- Produces: `checkQuestionInvariants(input: { questionType: "OBJECTIVE" | "THEORY" | "FILL_IN_BLANK"; options?: QuestionOptions | null; correctAnswer: string }): InvariantIssue[]`
- Produces: `checkTopicOwnership(input: { topicRef: string | null; topicSubjectId: string | null; subjectId: string }): InvariantIssue | null`
- Produces: `const MIN_OBJECTIVE_OPTIONS = 4`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-admin-question.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  MIN_OBJECTIVE_OPTIONS,
  checkQuestionInvariants,
  checkTopicOwnership,
  normalizeOptions,
} from "../src/lib/admin-question";

const FOUR = { A: "one", B: "two", C: "three", D: "four" };

test("an objective question whose correct answer is a real option passes", () => {
  const issues = checkQuestionInvariants({
    questionType: "OBJECTIVE",
    options: FOUR,
    correctAnswer: "B",
  });
  assert.deepEqual(issues, []);
});

test("an objective question whose correct answer is not an option is rejected", () => {
  // The database happily stores this today, and the quiz engine then marks
  // every student wrong.
  const issues = checkQuestionInvariants({
    questionType: "OBJECTIVE",
    options: FOUR,
    correctAnswer: "E",
  });
  assert.equal(issues.length, 1);
  assert.equal(issues[0].field, "correctAnswer");
});

test("the correct answer matches its option key case-insensitively", () => {
  const issues = checkQuestionInvariants({
    questionType: "OBJECTIVE",
    options: FOUR,
    correctAnswer: "b",
  });
  assert.deepEqual(issues, []);
});

test("an objective question needs at least four options", () => {
  const issues = checkQuestionInvariants({
    questionType: "OBJECTIVE",
    options: { A: "one", B: "two", C: "three" },
    correctAnswer: "A",
  });
  assert.equal(issues.length, 1);
  assert.equal(issues[0].field, "options");
  assert.match(issues[0].message, new RegExp(String(MIN_OBJECTIVE_OPTIONS)));
});

test("an objective question with no options at all is rejected", () => {
  const issues = checkQuestionInvariants({
    questionType: "OBJECTIVE",
    options: null,
    correctAnswer: "A",
  });
  assert.equal(issues.length, 1);
  assert.equal(issues[0].field, "options");
});

test("a theory question needs no options", () => {
  const issues = checkQuestionInvariants({
    questionType: "THEORY",
    options: null,
    correctAnswer: "See marking scheme",
  });
  assert.deepEqual(issues, []);
});

test("duplicate option keys are reported rather than silently collapsed", () => {
  const { issues } = normalizeOptions({ A: "one", a: "ONE", B: "two" });
  assert.equal(issues.length, 1);
  assert.equal(issues[0].field, "options");
});

test("option keys are upper-cased and values trimmed", () => {
  const { options } = normalizeOptions({ a: "  one  ", b: "two" });
  assert.deepEqual(options, { A: "one", B: "two" });
});

test("a topic belonging to the chosen subject is accepted", () => {
  assert.equal(
    checkTopicOwnership({
      topicRef: "algebra",
      topicSubjectId: "subj_1",
      subjectId: "subj_1",
    }),
    null,
  );
});

test("a topic from another subject is rejected", () => {
  const issue = checkTopicOwnership({
    topicRef: "algebra",
    topicSubjectId: "subj_2",
    subjectId: "subj_1",
  });
  assert.equal(issue?.field, "topicId");
});

test("an unresolved topic reference is rejected", () => {
  const issue = checkTopicOwnership({
    topicRef: "does-not-exist",
    topicSubjectId: null,
    subjectId: "subj_1",
  });
  assert.equal(issue?.field, "topicId");
});

test("no topic at all is allowed — topicId is nullable", () => {
  assert.equal(
    checkTopicOwnership({ topicRef: null, topicSubjectId: null, subjectId: "subj_1" }),
    null,
  );
});
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-admin-question.mts`
Expected: FAIL — cannot find module `../src/lib/admin-question`.

- [ ] **Step 3: Implement the module**

Create `src/lib/admin-question.ts`:

```ts
// Invariants the schema cannot express.
//
// `Question.correctAnswer` is a bare String and `Question.options` a nullable
// Json blob (prisma/schema.prisma:417-418), so nothing stops an objective
// question from declaring a correct answer that is not one of its options —
// which marks every student wrong, silently. Likewise `topicId` is only
// constrained to *a* topic, not to a topic under the question's subject.

export type QuestionOptions = Record<string, string>;

export type InvariantIssue = { field: string; message: string };

export const MIN_OBJECTIVE_OPTIONS = 4;

/** Upper-cases keys, trims values, and reports keys that collide once cased. */
export function normalizeOptions(
  options: QuestionOptions | null | undefined,
): { options: QuestionOptions | null; issues: InvariantIssue[] } {
  if (!options) return { options: null, issues: [] };

  const normalized: QuestionOptions = {};
  const seen = new Set<string>();
  const duplicates: string[] = [];

  for (const [rawKey, rawValue] of Object.entries(options)) {
    const key = rawKey.trim().toUpperCase();
    if (seen.has(key)) {
      duplicates.push(key);
      continue;
    }
    seen.add(key);
    normalized[key] = String(rawValue).trim();
  }

  const issues: InvariantIssue[] = [];
  if (duplicates.length > 0) {
    issues.push({
      field: "options",
      message: `Duplicate option keys: ${[...new Set(duplicates)].join(", ")}`,
    });
  }

  return { options: normalized, issues };
}

export function checkQuestionInvariants(input: {
  questionType: "OBJECTIVE" | "THEORY" | "FILL_IN_BLANK";
  options?: QuestionOptions | null;
  correctAnswer: string;
}): InvariantIssue[] {
  const { options, issues } = normalizeOptions(input.options);

  // Only objective questions are auto-marked against an option key.
  if (input.questionType !== "OBJECTIVE") return issues;

  const keys = options ? Object.keys(options) : [];

  if (keys.length < MIN_OBJECTIVE_OPTIONS) {
    issues.push({
      field: "options",
      message: `An objective question needs at least ${MIN_OBJECTIVE_OPTIONS} options.`,
    });
    return issues;
  }

  const answer = input.correctAnswer.trim().toUpperCase();
  if (!keys.includes(answer)) {
    issues.push({
      field: "correctAnswer",
      message: `The correct answer must be one of the option keys (${keys.join(", ")}).`,
    });
  }

  return issues;
}

/**
 * `topicSubjectId` is the subject of the resolved topic, or null when the
 * reference did not resolve at all. `topicRef` is whatever the caller was
 * given — an id from the form, a slug from an import row — and is used only
 * for the message.
 */
export function checkTopicOwnership(input: {
  topicRef: string | null;
  topicSubjectId: string | null;
  subjectId: string;
}): InvariantIssue | null {
  if (!input.topicRef) return null;

  if (!input.topicSubjectId) {
    return { field: "topicId", message: `Unknown topic: "${input.topicRef}".` };
  }

  if (input.topicSubjectId !== input.subjectId) {
    return {
      field: "topicId",
      message: `Topic "${input.topicRef}" belongs to a different subject.`,
    };
  }

  return null;
}
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `node --import tsx --test --test-force-exit scripts/test-admin-question.mts`
Expected: PASS, 12 tests.

- [ ] **Step 5: Register the suite**

In `package.json:11`, append ` scripts/test-admin-question.mts` to the end of the `test` script's file list.

Run: `npm test`
Expected: the whole suite passes, including the new file.

- [ ] **Step 6: Commit**

```bash
git add src/lib/admin-question.ts scripts/test-admin-question.mts package.json
git commit -m "feat(admin): add question invariant module with tests"
```

---

### Task 3: Import parsing module

Lets the browser validate a batch and show per-row errors *before* anything is posted, using the same schema the route uses.

**Files:**
- Create: `src/lib/admin-import.ts`
- Create: `scripts/test-admin-import.mts`
- Modify: `package.json:11`

**Interfaces:**
- Consumes: `bulkImportQuestionSchema` from `src/lib/validators.ts:104`
- Produces: `const MAX_IMPORT_ROWS = 500`
- Produces: `type ImportRowError = { index: number; field: string; message: string }`
- Produces: `type ImportParseResult = { ok: true; rows: BulkImportQuestion[]; errors: ImportRowError[]; total: number } | { ok: false; fatal: string }`
- Produces: `parseImportPayload(raw: string): ImportParseResult`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-admin-import.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { MAX_IMPORT_ROWS, parseImportPayload } from "../src/lib/admin-import";

function validRow(overrides: Record<string, unknown> = {}) {
  return {
    subjectCode: "MTH",
    examType: "WAEC",
    questionText: "What is 2 + 2?",
    options: { A: "3", B: "4", C: "5", D: "6" },
    correctAnswer: "B",
    explanation: "Two plus two is four.",
    ...overrides,
  };
}

test("a valid batch parses with no errors", () => {
  const result = parseImportPayload(JSON.stringify([validRow(), validRow()]));
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal(result.total, 2);
  assert.deepEqual(result.errors, []);
});

test("a { questions: [...] } wrapper is accepted as well as a bare array", () => {
  const result = parseImportPayload(JSON.stringify({ questions: [validRow()] }));
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal(result.total, 1);
});

test("malformed JSON fails fatally rather than per-row", () => {
  const result = parseImportPayload("{ not json");
  assert.equal(result.ok, false);
});

test("a root that is neither an array nor a questions object fails fatally", () => {
  const result = parseImportPayload(JSON.stringify({ foo: 1 }));
  assert.equal(result.ok, false);
});

test("an empty array fails fatally — there is nothing to import", () => {
  const result = parseImportPayload("[]");
  assert.equal(result.ok, false);
});

test("one bad row is reported by index without rejecting the good ones", () => {
  const raw = JSON.stringify([
    validRow(),
    validRow({ correctAnswer: undefined }),
    validRow(),
  ]);
  const result = parseImportPayload(raw);
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal(result.rows.length, 2);
  assert.equal(result.errors.length, 1);
  assert.equal(result.errors[0].index, 1);
  assert.equal(result.errors[0].field, "correctAnswer");
});

test("an objective row whose correct answer is not an option is caught here", () => {
  // Same invariant as the single-question form — checked before the network,
  // so the admin sees it next to the row that caused it.
  const result = parseImportPayload(
    JSON.stringify([validRow({ correctAnswer: "Z" })]),
  );
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal(result.errors.length, 1);
  assert.equal(result.errors[0].field, "correctAnswer");
});

test("a batch over the row cap fails fatally and names the cap", () => {
  const raw = JSON.stringify(
    Array.from({ length: MAX_IMPORT_ROWS + 1 }, () => validRow()),
  );
  const result = parseImportPayload(raw);
  assert.equal(result.ok, false);
  if (result.ok) return;
  assert.match(result.fatal, new RegExp(String(MAX_IMPORT_ROWS)));
});

test("a batch exactly at the cap is accepted", () => {
  const raw = JSON.stringify(
    Array.from({ length: MAX_IMPORT_ROWS }, () => validRow()),
  );
  const result = parseImportPayload(raw);
  assert.equal(result.ok, true);
});

test("every row failing still returns ok with an empty row set", () => {
  const result = parseImportPayload(JSON.stringify([{ subjectCode: "MTH" }]));
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal(result.rows.length, 0);
  assert.equal(result.errors.length > 0, true);
});
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-admin-import.mts`
Expected: FAIL — cannot find module `../src/lib/admin-import`.

- [ ] **Step 3: Implement the module**

Create `src/lib/admin-import.ts`:

```ts
import { z } from "zod";
import { bulkImportQuestionSchema } from "@/lib/validators";
import { checkQuestionInvariants } from "@/lib/admin-question";

export type BulkImportQuestion = z.infer<typeof bulkImportQuestionSchema>;

// Mirrors the server cap in bulkImportSchema (src/lib/validators.ts:126). The
// browser enforces it so an oversized paste is explained, not 400'd.
export const MAX_IMPORT_ROWS = 500;

export type ImportRowError = { index: number; field: string; message: string };

export type ImportParseResult =
  | {
      ok: true;
      rows: BulkImportQuestion[];
      errors: ImportRowError[];
      total: number;
    }
  | { ok: false; fatal: string };

function readRoot(raw: string): unknown[] | { fatal: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { fatal: "That is not valid JSON. Check for a trailing comma or a missing bracket." };
  }

  const rows = Array.isArray(parsed)
    ? parsed
    : parsed && typeof parsed === "object" && Array.isArray((parsed as { questions?: unknown }).questions)
      ? ((parsed as { questions: unknown[] }).questions)
      : null;

  if (!rows) {
    return {
      fatal: 'Expected either an array of questions or an object shaped { "questions": [ ... ] }.',
    };
  }

  if (rows.length === 0) return { fatal: "The file contains no questions." };

  if (rows.length > MAX_IMPORT_ROWS) {
    return {
      fatal: `${rows.length} rows exceeds the ${MAX_IMPORT_ROWS}-row limit. Split the file and import it in batches.`,
    };
  }

  return rows;
}

/**
 * Validates a pasted or uploaded batch row by row, so a single bad row is
 * reported against its index instead of rejecting the whole file.
 */
export function parseImportPayload(raw: string): ImportParseResult {
  const root = readRoot(raw);
  if (!Array.isArray(root)) return { ok: false, fatal: root.fatal };

  const rows: BulkImportQuestion[] = [];
  const errors: ImportRowError[] = [];

  root.forEach((row, index) => {
    const parsed = bulkImportQuestionSchema.safeParse(row);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        errors.push({
          index,
          field: issue.path.join(".") || "row",
          message: issue.message,
        });
      }
      return;
    }

    const invariants = checkQuestionInvariants({
      questionType: parsed.data.questionType,
      options: parsed.data.options ?? null,
      correctAnswer: parsed.data.correctAnswer,
    });
    if (invariants.length > 0) {
      for (const issue of invariants) {
        errors.push({ index, field: issue.field, message: issue.message });
      }
      return;
    }

    rows.push(parsed.data);
  });

  return { ok: true, rows, errors, total: root.length };
}
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `node --import tsx --test --test-force-exit scripts/test-admin-import.mts`
Expected: PASS, 10 tests.

If the `@/lib/...` path alias does not resolve under `tsx`, check how the existing suites import (`scripts/test-exam-target.mts:3` uses a relative path). Use relative imports in `src/lib/admin-import.ts` only if the alias genuinely fails; prefer the alias, which the rest of `src/lib` uses.

- [ ] **Step 5: Register the suite and commit**

Append ` scripts/test-admin-import.mts` to the `test` script in `package.json:11`.

Run: `npm test`
Expected: PASS.

```bash
git add src/lib/admin-import.ts scripts/test-admin-import.mts package.json
git commit -m "feat(admin): add import parsing module with per-row errors"
```

---

### Task 4: Overview statistics module

Shapes raw counts into display rows. Pure, so the division-by-zero case an empty database produces is provably handled.

**Files:**
- Create: `src/lib/admin-stats.ts`
- Create: `scripts/test-admin-stats.mts`
- Modify: `package.json:11`

**Interfaces:**
- Produces: `type CountedSubject = { id: string; name: string; code: string; questionCount: number }`
- Produces: `type StatRow = { key: string; label: string; count: number; percent: number }`
- Produces: `toStatRows(counts: Array<{ key: string; label: string; count: number }>, total: number): StatRow[]`
- Produces: `summariseSubjects(subjects: CountedSubject[]): { rows: StatRow[]; empty: CountedSubject[]; total: number }`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-admin-stats.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { summariseSubjects, toStatRows } from "../src/lib/admin-stats";

test("an empty database yields no rows and a zero total", () => {
  const summary = summariseSubjects([]);
  assert.deepEqual(summary.rows, []);
  assert.equal(summary.total, 0);
});

test("percentages are zero rather than NaN when the total is zero", () => {
  // The naive count/total renders "NaN%" on a fresh install.
  const rows = toStatRows([{ key: "WAEC", label: "WAEC", count: 0 }], 0);
  assert.equal(rows[0].percent, 0);
  assert.equal(Number.isNaN(rows[0].percent), false);
});

test("percentages are rounded to whole numbers", () => {
  const rows = toStatRows(
    [
      { key: "a", label: "A", count: 1 },
      { key: "b", label: "B", count: 2 },
    ],
    3,
  );
  assert.equal(rows[0].percent, 33);
  assert.equal(rows[1].percent, 67);
});

test("subjects are ordered by question count, descending", () => {
  const summary = summariseSubjects([
    { id: "1", name: "Maths", code: "MTH", questionCount: 5 },
    { id: "2", name: "Physics", code: "PHY", questionCount: 20 },
  ]);
  assert.deepEqual(
    summary.rows.map((r) => r.key),
    ["2", "1"],
  );
});

test("subjects with no questions are separated out as gaps", () => {
  const summary = summariseSubjects([
    { id: "1", name: "Maths", code: "MTH", questionCount: 5 },
    { id: "2", name: "Civic Education", code: "CIV", questionCount: 0 },
  ]);
  assert.deepEqual(summary.empty.map((s) => s.code), ["CIV"]);
  assert.deepEqual(summary.rows.map((r) => r.key), ["1"]);
  assert.equal(summary.total, 5);
});

test("the total is the sum of every subject's questions", () => {
  const summary = summariseSubjects([
    { id: "1", name: "Maths", code: "MTH", questionCount: 5 },
    { id: "2", name: "Physics", code: "PHY", questionCount: 7 },
  ]);
  assert.equal(summary.total, 12);
});
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-admin-stats.mts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the module**

Create `src/lib/admin-stats.ts`:

```ts
// Display shaping for the admin overview. Kept free of Prisma so the
// empty-database and rounding behaviour can be tested directly.

export type CountedSubject = {
  id: string;
  name: string;
  code: string;
  questionCount: number;
};

export type StatRow = {
  key: string;
  label: string;
  count: number;
  /** Whole-number percentage of the total; 0 when the total is 0. */
  percent: number;
};

export function toStatRows(
  counts: Array<{ key: string; label: string; count: number }>,
  total: number,
): StatRow[] {
  return counts.map((entry) => ({
    ...entry,
    percent: total > 0 ? Math.round((entry.count / total) * 100) : 0,
  }));
}

/**
 * Splits subjects into those with content and those without. A subject with
 * zero questions is a coverage gap worth surfacing, not a row reading "0".
 */
export function summariseSubjects(subjects: CountedSubject[]): {
  rows: StatRow[];
  empty: CountedSubject[];
  total: number;
} {
  const total = subjects.reduce((sum, s) => sum + s.questionCount, 0);
  const empty = subjects.filter((s) => s.questionCount === 0);
  const populated = subjects
    .filter((s) => s.questionCount > 0)
    .sort((a, b) => b.questionCount - a.questionCount);

  return {
    rows: toStatRows(
      populated.map((s) => ({ key: s.id, label: s.name, count: s.questionCount })),
      total,
    ),
    empty,
    total,
  };
}
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `node --import tsx --test --test-force-exit scripts/test-admin-stats.mts`
Expected: PASS, 6 tests.

- [ ] **Step 5: Register the suite and commit**

Append ` scripts/test-admin-stats.mts` to the `test` script in `package.json:11`.

Run: `npm test`
Expected: PASS.

```bash
git add src/lib/admin-stats.ts scripts/test-admin-stats.mts package.json
git commit -m "feat(admin): add overview statistics module with tests"
```

---

### Task 5: Admin question validation schemas

**Files:**
- Modify: `src/lib/validators.ts:100` (the empty `// ─── Questions (Admin) ───` header already there)

**Interfaces:**
- Consumes: `checkQuestionInvariants` from Task 2
- Produces: `adminQuestionCreateSchema`, `adminQuestionUpdateSchema`, `adminQuestionDeleteSchema`
- Produces: `type AdminQuestionCreateInput`, `type AdminQuestionUpdateInput`

- [ ] **Step 1: Add the schemas**

In `src/lib/validators.ts`, replace the bare header at line 100 with:

```ts
// ─── Questions (Admin) ────────────────────────────

// Id-based, unlike bulkImportQuestionSchema below, which is code/slug-based:
// the admin form works from populated selects, an import file from human-typed
// subject codes.
export const adminQuestionCreateSchema = z
  .object({
    subjectId: z.string().min(1, "Choose a subject."),
    topicId: z.string().min(1).nullish(),
    examType: z.enum(["WAEC", "JAMB", "NECO", "CUSTOM"]),
    examYear: z.number().int().min(1990).max(2030).nullish(),
    questionNumber: z.number().int().min(1).nullish(),
    questionText: z.string().min(5, "The question text is too short."),
    questionImageUrl: z.string().url().nullish(),
    questionType: z.enum(["OBJECTIVE", "THEORY", "FILL_IN_BLANK"]).default("OBJECTIVE"),
    options: z.record(z.string(), z.string()).nullish(),
    correctAnswer: z.string().min(1, "A correct answer is required."),
    explanation: z.string().min(5, "An explanation is required."),
    explanationImageUrl: z.string().url().nullish(),
    difficulty: z.enum(["BASIC", "INTERMEDIATE", "ADVANCED"]).default("INTERMEDIATE"),
    marks: z.number().int().min(1).default(1),
    timeEstimateSeconds: z.number().int().min(10).default(90),
  })
  .superRefine((value, ctx) => {
    for (const issue of checkQuestionInvariants({
      questionType: value.questionType,
      options: value.options ?? null,
      correctAnswer: value.correctAnswer,
    })) {
      ctx.addIssue({
        code: "custom",
        path: [issue.field],
        message: issue.message,
      });
    }
  });

// `.partial()` cannot be called on a refined schema, so the shape is declared
// once and refined twice.
export const adminQuestionUpdateSchema = z
  .object(adminQuestionCreateSchema.innerType().shape)
  .partial()
  .refine(
    (value) => Object.keys(value).length > 0,
    "Provide at least one field to update.",
  );

export const adminQuestionDeleteSchema = z.object({
  ids: z.array(z.string().min(1)).min(1).max(100),
});

export type AdminQuestionCreateInput = z.infer<typeof adminQuestionCreateSchema>;
export type AdminQuestionUpdateInput = z.infer<typeof adminQuestionUpdateSchema>;
```

Add `import { checkQuestionInvariants } from "@/lib/admin-question";` at the top of the file.

> **CORRECTION — verified during implementation, 2026-08-04.** The code above is zod-3 shaped and does not work on this project's zod 4.4.3. Two facts were confirmed empirically:
>
> 1. `.innerType()` throws `TypeError: ... is not a function` on a `superRefine`'d schema. The fallback below is mandatory, not optional.
> 2. **`.partial()` does NOT suppress `.default()` in zod 4.** `z.object({ b: z.number().default(1) }).partial().safeParse({})` returns `{ b: 1 }`, not `{}`. So putting defaults inside a shared shape means the update schema's "at least one field" refine can never fire — a `PATCH` with an empty body would be accepted.
>
> **Do this instead:** declare the field shape once as `const adminQuestionShape = { ... }` with **no `.default()` on any field**. Build the create schema as `z.object({ ...adminQuestionShape, questionType: adminQuestionShape.questionType.default("OBJECTIVE"), /* …other defaults… */ }).superRefine(...)`, layering defaults onto the shared field schemas rather than re-typing their constraints. Build the update schema as `z.object(adminQuestionShape).partial().refine(...)`. The field list is still declared exactly once.

The update path re-checks invariants inside the route rather than in the schema, because a partial update may change `options` without resending `correctAnswer`; the route merges the stored row with the patch before checking. That merge is written in Task 6.

- [ ] **Step 2: Verify it type-checks**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/lib/validators.ts
git commit -m "feat(admin): add admin question validation schemas"
```

---

### Task 6: Question mutation routes

**Files:**
- Modify: `src/app/api/admin/questions/route.ts` (add `POST`, rewrite `DELETE`)
- Create: `src/app/api/admin/questions/[id]/route.ts` (`GET`, `PATCH`)
- Create: `src/app/api/admin/questions/[id]/usage/route.ts` (`GET`)
- Modify: `src/app/api/admin/questions/import/route.ts` (audit + shared ownership check)

**Interfaces:**
- Consumes: `requireAdmin`, `recordAudit` (Task 1); `checkQuestionInvariants`, `checkTopicOwnership`, `normalizeOptions` (Task 2); the schemas from Task 5
- Produces: `DELETE` response `{ deleted: string[]; refused: Array<{ id: string; responseCount: number; assessmentCount: number }> }`
- Produces: `GET /[id]/usage` response `{ responseCount: number; assessmentCount: number; deletable: boolean }`

- [ ] **Step 1: Read the docs**

Read `node_modules/next/dist/docs/01-app/01-getting-started/15-route-handlers.md` (params are a Promise; `RouteContext<'/api/admin/questions/[id]'>` is available) and `.../09-revalidating.md` for the current `revalidateTag` signature. The existing calls pass a second argument (`src/app/api/admin/questions/route.ts:105`) — match whatever the installed version documents.

- [ ] **Step 2: Add `POST` to the collection route**

In `src/app/api/admin/questions/route.ts`, add:

```ts
export async function POST(req: NextRequest) {
  try {
    const guard = await requireAdmin();
    if (!guard.ok) return guard.response;

    const parsed = adminQuestionCreateSchema.safeParse(await req.json());
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Validation failed", details: parsed.error.flatten() },
        { status: 400 },
      );
    }
    const input = parsed.data;

    const subject = await db.subject.findUnique({
      where: { id: input.subjectId },
      select: { id: true, code: true },
    });
    if (!subject) {
      return NextResponse.json({ error: "Unknown subject" }, { status: 400 });
    }

    // The topic must hang off the chosen subject — the FK alone permits any
    // topic in the database.
    const topic = input.topicId
      ? await db.topic.findUnique({
          where: { id: input.topicId },
          select: { id: true, subjectId: true },
        })
      : null;
    const ownership = checkTopicOwnership({
      topicRef: input.topicId ?? null,
      topicSubjectId: topic?.subjectId ?? null,
      subjectId: input.subjectId,
    });
    if (ownership) {
      return NextResponse.json(
        { error: ownership.message, field: ownership.field },
        { status: 400 },
      );
    }

    const { options } = normalizeOptions(input.options);

    const created = await db.question.create({
      data: {
        subjectId: input.subjectId,
        topicId: input.topicId ?? null,
        examType: input.examType,
        examYear: input.examYear ?? null,
        questionNumber: input.questionNumber ?? null,
        questionText: input.questionText,
        questionImageUrl: input.questionImageUrl ?? null,
        questionType: input.questionType,
        // A bare null is a type error on a nullable Json column; Prisma needs
        // the DbNull sentinel (same reason as import/route.ts:139).
        options: options ?? Prisma.DbNull,
        correctAnswer: input.correctAnswer.trim().toUpperCase(),
        explanation: input.explanation,
        explanationImageUrl: input.explanationImageUrl ?? null,
        difficulty: input.difficulty,
        marks: input.marks,
        timeEstimateSeconds: input.timeEstimateSeconds,
      },
      select: { id: true },
    });

    await recordAudit({
      actorId: guard.actor.id,
      action: "question.create",
      entity: "Question",
      entityId: created.id,
      summary: `Created ${subject.code} ${input.examType} question`,
    });

    revalidateTag(CATALOGUE_TAG, "max");

    return NextResponse.json({ id: created.id }, { status: 201 });
  } catch (error) {
    console.error("Error creating question:", error);
    return NextResponse.json({ error: "Failed to create question" }, { status: 500 });
  }
}
```

Add the imports it needs: `Prisma` from `@prisma/client`, `adminQuestionCreateSchema` from `@/lib/validators`, `checkTopicOwnership`/`normalizeOptions` from `@/lib/admin-question`, `recordAudit` from `@/lib/admin-audit`.

Note `correctAnswer` is stored upper-cased to match the normalized option keys — the invariant check is case-insensitive, but the stored value must match what the quiz engine compares against.

- [ ] **Step 3: Rewrite `DELETE` to be dependency-checked**

Replace the body of `DELETE` in the same file. This is the core fix: `AssessmentQuestion.question` (`prisma/schema.prisma:470`) and `QuestionResponse.question` (`:505`) are `Restrict` by default, so today's `db.question.delete()` throws a foreign-key error that surfaces as an opaque 500.

```ts
export async function DELETE(req: NextRequest) {
  try {
    const guard = await requireAdmin();
    if (!guard.ok) return guard.response;

    // Single-id query param kept for compatibility; a body of ids is the
    // bulk form.
    const { searchParams } = new URL(req.url);
    const singleId = searchParams.get("id");

    let ids: string[];
    if (singleId) {
      ids = [singleId];
    } else {
      const parsed = adminQuestionDeleteSchema.safeParse(await req.json());
      if (!parsed.success) {
        return NextResponse.json(
          { error: "Provide an id query parameter or a body of ids." },
          { status: 400 },
        );
      }
      ids = parsed.data.ids;
    }

    // Count dependents in two grouped queries rather than one per id.
    const [responses, assessments] = await Promise.all([
      db.questionResponse.groupBy({
        by: ["questionId"],
        where: { questionId: { in: ids } },
        _count: { questionId: true },
      }),
      db.assessmentQuestion.groupBy({
        by: ["questionId"],
        where: { questionId: { in: ids } },
        _count: { questionId: true },
      }),
    ]);

    const responseCounts = new Map(
      responses.map((r) => [r.questionId, r._count.questionId]),
    );
    const assessmentCounts = new Map(
      assessments.map((r) => [r.questionId, r._count.questionId]),
    );

    const refused = ids
      .map((id) => ({
        id,
        responseCount: responseCounts.get(id) ?? 0,
        assessmentCount: assessmentCounts.get(id) ?? 0,
      }))
      .filter((row) => row.responseCount > 0 || row.assessmentCount > 0);

    const refusedIds = new Set(refused.map((r) => r.id));
    const deletable = ids.filter((id) => !refusedIds.has(id));

    if (deletable.length > 0) {
      await db.question.deleteMany({ where: { id: { in: deletable } } });

      await recordAudit({
        actorId: guard.actor.id,
        action: "question.delete",
        entity: "Question",
        entityId: deletable.length === 1 ? deletable[0] : null,
        summary: `Deleted ${deletable.length} question(s); refused ${refused.length} with dependents`,
      });

      revalidateTag(CATALOGUE_TAG, "max");
    }

    return NextResponse.json({ deleted: deletable, refused });
  } catch (error) {
    console.error("Error deleting questions:", error);
    return NextResponse.json({ error: "Failed to delete questions" }, { status: 500 });
  }
}
```

- [ ] **Step 4: Create the single-question route**

Create `src/app/api/admin/questions/[id]/route.ts`.

`GET` guards, loads the question with `subject: { select: { id: true, name: true, code: true } }` and `topic: { select: { id: true, title: true } }`, returns 404 when missing, and returns the row as JSON.

`PATCH` is the subtle one — the invariants must be checked against the **merged** record, not the patch. A patch that rewrites `options` without resending `correctAnswer` would otherwise sail through and leave a question whose correct answer no longer exists:

```ts
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const guard = await requireAdmin();
    if (!guard.ok) return guard.response;

    const { id } = await params;

    const parsed = adminQuestionUpdateSchema.safeParse(await req.json());
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Validation failed", details: parsed.error.flatten() },
        { status: 400 },
      );
    }
    const patch = parsed.data;

    const existing = await db.question.findUnique({
      where: { id },
      select: {
        id: true,
        subjectId: true,
        topicId: true,
        questionType: true,
        options: true,
        correctAnswer: true,
      },
    });
    if (!existing) {
      return NextResponse.json({ error: "Question not found" }, { status: 404 });
    }

    // Merge before checking: the patch is partial, the invariants are not.
    const mergedType = patch.questionType ?? existing.questionType;
    const mergedOptions =
      patch.options !== undefined
        ? (patch.options ?? null)
        : ((existing.options as Record<string, string> | null) ?? null);
    const mergedAnswer = patch.correctAnswer ?? existing.correctAnswer;
    const mergedSubjectId = patch.subjectId ?? existing.subjectId;
    const mergedTopicId =
      patch.topicId !== undefined ? (patch.topicId ?? null) : existing.topicId;

    const issues = checkQuestionInvariants({
      questionType: mergedType,
      options: mergedOptions,
      correctAnswer: mergedAnswer,
    });
    if (issues.length > 0) {
      return NextResponse.json(
        { error: issues[0].message, field: issues[0].field, issues },
        { status: 400 },
      );
    }

    // Only re-check ownership when either side of the pair moved.
    if (patch.subjectId !== undefined || patch.topicId !== undefined) {
      const topic = mergedTopicId
        ? await db.topic.findUnique({
            where: { id: mergedTopicId },
            select: { subjectId: true },
          })
        : null;
      const ownership = checkTopicOwnership({
        topicRef: mergedTopicId,
        topicSubjectId: topic?.subjectId ?? null,
        subjectId: mergedSubjectId,
      });
      if (ownership) {
        return NextResponse.json(
          { error: ownership.message, field: ownership.field },
          { status: 400 },
        );
      }
    }

    const { options: normalizedOptions } = normalizeOptions(mergedOptions);

    await db.question.update({
      where: { id },
      data: {
        ...patch,
        // Nulls on these two columns need explicit handling rather than the
        // spread's undefined-vs-null ambiguity.
        options:
          patch.options !== undefined
            ? (normalizedOptions ?? Prisma.DbNull)
            : undefined,
        correctAnswer:
          patch.correctAnswer !== undefined
            ? patch.correctAnswer.trim().toUpperCase()
            : undefined,
      },
    });

    await recordAudit({
      actorId: guard.actor.id,
      action: "question.update",
      entity: "Question",
      entityId: id,
      summary: `Updated fields: ${Object.keys(patch).join(", ")}`,
    });

    revalidateTag(CATALOGUE_TAG, "max");

    return NextResponse.json({ id });
  } catch (error) {
    console.error("Error updating question:", error);
    return NextResponse.json({ error: "Failed to update question" }, { status: 500 });
  }
}
```

- [ ] **Step 5: Create the usage route**

Create `src/app/api/admin/questions/[id]/usage/route.ts`:

```ts
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const guard = await requireAdmin();
  if (!guard.ok) return guard.response;

  const { id } = await params;
  const [responseCount, assessmentCount] = await Promise.all([
    db.questionResponse.count({ where: { questionId: id } }),
    db.assessmentQuestion.count({ where: { questionId: id } }),
  ]);

  return NextResponse.json({
    responseCount,
    assessmentCount,
    deletable: responseCount === 0 && assessmentCount === 0,
  });
}
```

- [ ] **Step 6: Audit the import route and share the ownership check**

In `src/app/api/admin/questions/import/route.ts`, replace the inline topic-subject comparison at `:97-103` with a call to `checkTopicOwnership`, and after the result tally (`:158`) add:

```ts
    await recordAudit({
      actorId: guard.actor.id,
      action: "question.import",
      entity: "Question",
      summary: `Imported ${results.imported}, skipped ${results.skipped}, ${results.errors.length} errors`,
    });
```

- [ ] **Step 7: Verify**

Run: `npx tsc --noEmit` — expected: no errors.
Run: `npm run lint` — expected: no new warnings.
Run: `npm test` — expected: PASS (the pure modules are unaffected, but confirm nothing regressed).

Manual check with the dev server running (`npm run dev`), signed in as an admin:

```bash
# Expect 401 when signed out, 403 as a student, 400 with a bad body as admin.
curl -i -X POST http://localhost:3000/api/admin/questions -H "Content-Type: application/json" -d '{}'
```

- [ ] **Step 8: Commit**

```bash
git add src/app/api/admin
git commit -m "feat(admin): add question create/update routes and dependency-checked deletes"
```

---

### Task 7: Admin shell — navigation, accessibility, density

**Files:**
- Create: `src/lib/admin-nav.ts`
- Create: `src/components/admin/admin-nav.tsx`
- Modify: `src/app/admin/layout.tsx` (whole file)

**Interfaces:**
- Produces: `ADMIN_NAV: Array<{ name: string; href: string; icon: IconType }>`
- Produces: `<AdminNav variant="sidebar" | "mobile" />`

- [ ] **Step 1: Extract the nav definition**

Create `src/lib/admin-nav.ts`:

```ts
import { LuDatabase, LuLayoutDashboard, LuUpload } from "react-icons/lu";

// Every entry must have a page behind it. An earlier version listed Subjects,
// Users and Lessons with no routes — three links straight to a 404.
export const ADMIN_NAV = [
  { name: "Overview", href: "/admin", icon: LuLayoutDashboard },
  { name: "Questions", href: "/admin/questions", icon: LuDatabase },
  { name: "Import", href: "/admin/questions/import", icon: LuUpload },
] as const;
```

- [ ] **Step 2: Build the nav client component**

Create `src/components/admin/admin-nav.tsx`. It must mark the active route with `aria-current="page"`, matching `src/components/ui/sidebar.tsx:65`. Note the exact-match rule: `/admin` must not light up for `/admin/questions`, and `/admin/questions` must not light up for `/admin/questions/import`.

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { ADMIN_NAV } from "@/lib/admin-nav";

export function AdminNav({ variant }: { variant: "sidebar" | "mobile" }) {
  const pathname = usePathname();

  // Exact match only. Prefix matching would light "Questions" while the user
  // is on "Questions › Import", and "Overview" on every admin page.
  const isActive = (href: string) => pathname === href;

  if (variant === "mobile") {
    return (
      <nav
        aria-label="Admin"
        className="fixed inset-x-0 bottom-0 z-50 border-t border-border bg-card lg:hidden"
      >
        <div className="flex items-center justify-around py-2">
          {ADMIN_NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={isActive(item.href) ? "page" : undefined}
              className={cn(
                "flex flex-col items-center gap-0.5 rounded-lg px-3 py-1 text-xs font-semibold transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
                isActive(item.href) ? "text-primary" : "text-muted",
              )}
            >
              <item.icon className="h-5 w-5" />
              {item.name}
            </Link>
          ))}
        </div>
      </nav>
    );
  }

  return (
    <nav aria-label="Admin" className="space-y-0.5 p-3">
      {ADMIN_NAV.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          aria-current={isActive(item.href) ? "page" : undefined}
          className={cn(
            "relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-semibold transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
            isActive(item.href)
              ? "bg-secondary text-foreground"
              : "text-muted hover:bg-secondary hover:text-foreground",
          )}
        >
          {isActive(item.href) && (
            // A left rule rather than the student app's soft pill — the admin
            // reads as an instrument panel.
            <span className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r bg-primary" />
          )}
          <item.icon className="h-4 w-4 flex-shrink-0" />
          {item.name}
        </Link>
      ))}
    </nav>
  );
}
```

- [ ] **Step 3: Rewrite the layout**

Modify `src/app/admin/layout.tsx`. Keep the existing server-side role guard exactly as it is (`:18-25`) — it is the authoritative check. Change:

1. Replace the inline `adminNav` array and both `.map()` blocks with `<AdminNav variant="sidebar" />` and `<AdminNav variant="mobile" />`.
2. Add a skip link as the first element inside the root `<div>`:

```tsx
      <a
        href="#admin-main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-card focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
      >
        Skip to content
      </a>
```

3. Give `<main>` the id and focus target, and fix the mobile overlap — the fixed bottom nav currently covers the last row because `<main>` has no bottom padding (compare `src/app/(dashboard)/layout.tsx:45`):

```tsx
        <main id="admin-main" tabIndex={-1} className="flex-1 pb-24 lg:pb-0">
```

4. Verify `sr-only` and `focus:not-sr-only` exist in this Tailwind setup by grepping `src/app/globals.css`; if `sr-only` is not available, add the standard utility to `globals.css` once.

- [ ] **Step 4: Verify**

Run: `npx tsc --noEmit` and `npm run lint` — expected: clean.

Manual, with `npm run dev`:
- Load `/admin/questions`. Press Tab once from the top of the page: "Skip to content" must appear. Press Enter: focus lands on the main region.
- Tab through the sidebar: every link shows a visible focus ring.
- Inspect the active link: it must carry `aria-current="page"`, and only one link at a time.
- Narrow the viewport below `lg`: the bottom nav must not cover the last row of content.

- [ ] **Step 5: Commit**

```bash
git add src/lib/admin-nav.ts src/components/admin/admin-nav.tsx src/app/admin/layout.tsx
git commit -m "feat(admin): accessible admin shell with skip link and active nav state"
```

---

### Task 8: Shared admin primitives

**Files:**
- Create: `src/components/admin/status-banner.tsx`
- Create: `src/components/admin/confirm-dialog.tsx`

**Interfaces:**
- Produces: `<StatusBanner tone="error" | "success" | "info" title={string} message?: string action?: React.ReactNode />`
- Produces: `<ConfirmDialog open title description confirmLabel tone="danger" busy onConfirm onCancel>{children}</ConfirmDialog>`

- [ ] **Step 1: Build `StatusBanner`**

Errors must be announced. `role="alert"` is assertive (right for failures); `role="status"` is polite (right for success and counts).

```tsx
import { LuCircleCheck, LuInfo, LuTriangleAlert } from "react-icons/lu";
import { cn } from "@/lib/utils";

const TONES = {
  error: { role: "alert" as const, icon: LuTriangleAlert, cls: "border-danger/30 bg-danger-soft text-danger" },
  success: { role: "status" as const, icon: LuCircleCheck, cls: "border-success/30 bg-success-soft text-success" },
  info: { role: "status" as const, icon: LuInfo, cls: "border-border bg-secondary text-foreground" },
};

export function StatusBanner({
  tone,
  title,
  message,
  action,
  className,
}: {
  tone: keyof typeof TONES;
  title: string;
  message?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  const { role, icon: Icon, cls } = TONES[tone];
  return (
    <div
      role={role}
      className={cn("flex items-start gap-3 rounded-lg border px-4 py-3", cls, className)}
    >
      <Icon className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold">{title}</p>
        {message && <p className="mt-0.5 text-sm opacity-90">{message}</p>}
      </div>
      {action}
    </div>
  );
}
```

- [ ] **Step 2: Build `ConfirmDialog`**

Replaces the browser `confirm()` at `src/app/admin/questions/page.tsx:58`. Follow the existing dialog markup (`src/components/path/pretest-dialog.tsx:167`) and add the three things it lacks: Escape to close, a focus trap, and focus restoration.

```tsx
"use client";

import { useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  busy,
  disabled,
  onConfirm,
  onCancel,
  children,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  busy?: boolean;
  /** True when the action cannot proceed — the confirm button is withheld. */
  disabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  children?: React.ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;

    // Remember what opened us so focus can go back there on close.
    restoreRef.current = document.activeElement as HTMLElement | null;

    const focusables = () =>
      Array.from(
        panelRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      );

    focusables()[0]?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) {
        onCancel();
        return;
      }
      if (event.key !== "Tab") return;

      const items = focusables();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      restoreRef.current?.focus();
    };
  }, [open, busy, onCancel]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-description"
        className="w-full max-w-md rounded-lg border border-border-strong bg-card p-5"
      >
        <h2 id="confirm-title" className="text-base font-bold text-foreground">
          {title}
        </h2>
        <p id="confirm-description" className="mt-1.5 text-sm text-muted">
          {description}
        </p>
        {children}
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          {!disabled && (
            <Button variant="danger" size="sm" onClick={onConfirm} disabled={busy}>
              {busy ? "Working…" : confirmLabel}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Verify**

Run: `npx tsc --noEmit` and `npm run lint` — expected: clean. These components are exercised for real in Tasks 10–13.

- [ ] **Step 4: Commit**

```bash
git add src/components/admin
git commit -m "feat(admin): add status banner and accessible confirm dialog"
```

---

### Task 9: Overview page

**Files:**
- Create: `src/app/admin/page.tsx` (server component)

**Interfaces:**
- Consumes: `summariseSubjects`, `toStatRows` (Task 4)

- [ ] **Step 1: Build the page**

A server component that queries directly via `db` — no API route needed, since the layout already guarantees the admin role. Required content, every figure queried:

```tsx
import Link from "next/link";
import { db } from "@/lib/db";
import { PageHeader } from "@/components/ui/page-header";
import { StatusBanner } from "@/components/admin/status-banner";
import { summariseSubjects, toStatRows } from "@/lib/admin-stats";

export const dynamic = "force-dynamic";

export default async function AdminOverviewPage() {
  const [subjects, topicCount, unlinkedCount, byExam, byDifficulty, years] =
    await Promise.all([
      db.subject.findMany({
        select: {
          id: true,
          name: true,
          code: true,
          _count: { select: { questions: true } },
        },
        orderBy: { name: "asc" },
      }),
      db.topic.count(),
      db.question.count({ where: { topicId: null } }),
      db.question.groupBy({ by: ["examType"], _count: { _all: true } }),
      db.question.groupBy({ by: ["difficulty"], _count: { _all: true } }),
      db.question.findMany({
        where: { examYear: { not: null } },
        distinct: ["examYear"],
        select: { examYear: true },
        orderBy: { examYear: "desc" },
      }),
    ]);

  const summary = summariseSubjects(
    subjects.map((s) => ({
      id: s.id,
      name: s.name,
      code: s.code,
      questionCount: s._count.questions,
    })),
  );

  const examRows = toStatRows(
    byExam.map((r) => ({ key: r.examType, label: r.examType, count: r._count._all })),
    summary.total,
  );
  const difficultyRows = toStatRows(
    byDifficulty.map((r) => ({ key: r.difficulty, label: r.difficulty, count: r._count._all })),
    summary.total,
  );

  // ...render
}
```

The rendered page must contain:

1. `<PageHeader title="Overview" description={...} />` where the description states the real totals (`{summary.total} questions across {subjects.length} subjects, {topicCount} topics`).
2. When `summary.total === 0`: a `StatusBanner` with `tone="info"`, title "No questions yet", and a link to `/admin/questions/import`. Render no breakdown tables at all — do not show a table of zeroes.
3. A "By subject" table: subject name, code, count, percent bar. Each row links to `/admin/questions?subjectId={id}`.
4. A "By exam" and a "By difficulty" table, each row linking to `/admin/questions?examType={key}` / `?difficulty={key}`.
5. An "Exam years covered" line listing `years.map(y => y.examYear)` as chips, or "None recorded" when empty.
6. A "Gaps" section rendered only when there is something in it: `summary.empty` (subjects with zero questions, each linking to import) and `unlinkedCount` (questions with no topic, linking to `/admin/questions`). When both are clear, render a `tone="success"` banner reading "No coverage gaps detected."

Apply the admin visual language throughout: `rounded-lg`, `border-strong` rules, `tabular-nums` on every count and percentage, `text-[11px] font-semibold uppercase tracking-wider text-muted` column headings.

- [ ] **Step 2: Verify**

Run: `npx tsc --noEmit` and `npm run lint` — expected: clean.

Manual:
- Visit `/admin` as an admin. Every number must match reality — cross-check one against `/admin/questions` (the total in the list header) and one against the database.
- Click a subject row: the questions list must open pre-filtered to that subject (this requires Task 10's URL-parameter handling; until then verify the href is correct).
- Confirm no `NaN%` appears anywhere.

- [ ] **Step 3: Commit**

```bash
git add src/app/admin/page.tsx
git commit -m "feat(admin): add overview page with real coverage figures"
```

---

### Task 10: Harden the questions list

**Files:**
- Modify: `src/app/admin/questions/page.tsx` (substantial rewrite)

- [ ] **Step 1: Fix the fetch state machine**

The current effect refetches on every keystroke because `search` is a dependency of `fetchQuestions` (`:48`), which makes the submit handler (`:70`) dead code. Split input state from fetch state, and abort superseded requests:

```tsx
  const [queryInput, setQueryInput] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  const fetchQuestions = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ page: String(page), pageSize: "20" });
      if (appliedQuery) params.set("search", appliedQuery);
      if (examFilter) params.set("examType", examFilter);
      if (subjectFilter) params.set("subjectId", subjectFilter);
      if (difficultyFilter) params.set("difficulty", difficultyFilter);

      const res = await fetch(`/api/admin/questions?${params}`, { signal });
      const data = await res.json();
      if (!res.ok) {
        // Previously this fell through to the empty state, so a 500 was
        // indistinguishable from an empty database.
        setError(data.error ?? `Request failed (${res.status}).`);
        return;
      }
      setQuestions(data.questions);
      setPagination(data.pagination);
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      setError("Could not reach the server. Check your connection and retry.");
    } finally {
      setLoading(false);
    }
  }, [page, appliedQuery, examFilter, subjectFilter, difficultyFilter]);

  useEffect(() => {
    const controller = new AbortController();
    fetchQuestions(controller.signal);
    return () => controller.abort();
  }, [fetchQuestions]);
```

`handleSearch` becomes `setAppliedQuery(queryInput); setPage(1);` — now meaningful.

Read the initial filter values from the URL search params so the overview page's links work (`useSearchParams`). Consult `node_modules/next/dist/docs/01-app/` on `useSearchParams` and the Suspense boundary it requires in this version before wiring it.

- [ ] **Step 2: Render failure honestly**

Order the render branches: `error` first, then `loading`, then empty, then the table.

```tsx
      {error ? (
        <StatusBanner
          tone="error"
          title="Could not load questions"
          message={error}
          action={
            <Button variant="outline" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
              Retry
            </Button>
          }
        />
      ) : loading ? (
        /* spinner */
      ) : questions.length === 0 ? (
        /* genuinely empty */
      ) : (
        /* table */
      )}
```

Add a `reloadKey` state included in the effect's dependencies so Retry re-runs the fetch.

- [ ] **Step 3: Make the table accessible**

Required changes to the existing markup:

- Wrap the table region: `<div role="region" aria-label="Questions" aria-busy={loading}>`.
- `<caption className="sr-only">Questions, page {pagination.page} of {pagination.totalPages}</caption>`.
- `scope="col"` on every `<th>`.
- A visually hidden live region above the table announcing counts:
  `<p role="status" className="sr-only">Showing {questions.length} of {pagination?.total ?? 0} questions</p>`.
- Label the search input and every filter select. Use a visible `<label className="sr-only">` where the design has no room for visible text — but the exam, subject and difficulty selects should get visible labels, since there is space.
- The delete button gets a real name: `aria-label={\`Delete question: ${q.questionText.slice(0, 60)}\`}`, and `title` is not a substitute.
- Pagination: wrap in `<nav aria-label="Pagination">` and give each button `aria-label="Previous page"` / `"Next page"`. Keep the chevron icons but mark them `aria-hidden`.
- Every interactive element gets `focus-visible:ring-2 focus-visible:ring-primary/60`; prefer using the shared `Button` component, which already carries this (`src/components/ui/button.tsx:5`).

- [ ] **Step 4: Add the subject and difficulty filters**

The API already accepts `subjectId` and `difficulty` (`src/app/api/admin/questions/route.ts:28`, `:31`) — they were simply never wired up. Fetch the subject list once from `/api/subjects` for the select options.

- [ ] **Step 5: Apply the visual language**

`rounded-lg` on the table container, `divide-border-strong` between rows, `tabular-nums` on the year column and pagination numbers, `text-[11px] font-semibold uppercase tracking-wider` on `<th>`, remove any shadow from the container.

- [ ] **Step 6: Verify**

Run: `npx tsc --noEmit`, `npm run lint` — expected: clean.

Manual, with `npm run dev`:
- Type in the search box: the network tab must show **no** request until Enter is pressed.
- Stop the dev server and click Retry: an error banner must appear, never "No questions found".
- Tab through the entire page: every control reachable, every control shows a focus ring, no keyboard trap.
- With a screen reader (or the browser's accessibility inspector): the delete button announces the question text, and the table has a caption.
- Below `lg` width, scroll to the bottom: the last row is not covered by the nav.

- [ ] **Step 7: Commit**

```bash
git add src/app/admin/questions/page.tsx
git commit -m "fix(admin): surface list failures, fix filter refetching, make the table accessible"
```

---

### Task 11: Bulk selection and dependency-aware deletes

**Files:**
- Modify: `src/app/admin/questions/page.tsx`

- [ ] **Step 1: Add selection state**

```tsx
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Selection is per page: changing page or filters clears it, so a bulk
  // delete can never act on rows the admin can no longer see.
  useEffect(() => {
    setSelected(new Set());
  }, [page, appliedQuery, examFilter, subjectFilter, difficultyFilter]);
```

Row checkbox with an accessible name: `aria-label={\`Select question: ${q.questionText.slice(0, 60)}\`}`.

Header checkbox selects every row on the current page, with `aria-label="Select all questions on this page"` and `ref` setting `indeterminate` when the selection is partial (the `indeterminate` property cannot be set through JSX — assign it in a `useEffect` or a ref callback).

- [ ] **Step 2: Add the bulk action bar**

Rendered only when `selected.size > 0`, above the table: `{selected.size} selected` plus a "Delete selected" button and a "Clear selection" button. The count must be in a `role="status"` region so it is announced.

- [ ] **Step 3: Wire single delete through `ConfirmDialog`**

Replace `confirm()` (`:58`). On clicking a row's delete button, fetch `/api/admin/questions/{id}/usage` first, then open the dialog:

- When `deletable` is true: description reads "This cannot be undone." and the confirm button appears.
- When false: description states the counts — "This question has 14 student responses and appears in 2 assessments. It cannot be deleted." — and `disabled` withholds the confirm button entirely, so no button is offered that would fail.

Include the question text in the dialog body via `children`.

- [ ] **Step 4: Wire bulk delete**

The dialog confirms the count without pre-fetching usage per row — the `DELETE` route performs the authoritative check. On confirm:

```tsx
  const res = await fetch("/api/admin/questions", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: [...selected] }),
  });
  const data = await res.json();
  if (!res.ok) {
    setError(data.error ?? "Delete failed.");
    return;
  }
  setQuestions((prev) => prev.filter((q) => !data.deleted.includes(q.id)));
  setSelected(new Set());
  setResult({ deleted: data.deleted.length, refused: data.refused });
```

Render the outcome in a `StatusBanner`: `tone="success"` when nothing was refused, `tone="info"` when some were — listing how many were kept and why ("3 deleted, 2 kept because students have already answered them"). Never report a bare success when rows were refused.

- [ ] **Step 5: Fix the single-delete failure path**

The existing handler has no `else` on `res.ok` (`:62`). Every delete path must set `error` on failure and leave the row in place.

- [ ] **Step 6: Verify**

Manual, with `npm run dev`:
- Select three questions, delete: the count is announced, the rows disappear, the banner reports the number.
- Attempt to delete a question that a student has answered (find one via a completed attempt in the database): the dialog must state the dependency counts and offer no confirm button. Confirm the row is still present afterwards.
- Bulk-delete a mixed selection: the banner must report both the deleted count and the refused ones.
- Open the dialog with the keyboard, press Tab repeatedly: focus must cycle inside the dialog. Press Escape: it closes and focus returns to the delete button that opened it.

- [ ] **Step 7: Commit**

```bash
git add src/app/admin/questions/page.tsx
git commit -m "feat(admin): bulk question selection with dependency-aware deletes"
```

---

### Task 12: Question create and edit form

**Files:**
- Create: `src/components/admin/question-form.tsx`
- Create: `src/app/admin/questions/new/page.tsx`
- Create: `src/app/admin/questions/[id]/edit/page.tsx`

**Interfaces:**
- Consumes: `adminQuestionCreateSchema` (Task 5), the routes from Task 6
- Produces: `<QuestionForm mode="create" | "edit" questionId?: string initial?: QuestionFormValues />`

- [ ] **Step 1: Build the form component**

A client component holding the whole form. Requirements:

- **Fields**, mirroring `Question` (`prisma/schema.prisma:405-423`): subject select (required), topic select (filtered to the chosen subject, cleared when the subject changes), exam type select, exam year number, question number, question text textarea, question type select, option rows, correct answer, explanation textarea, difficulty select, marks, time estimate, question image URL, explanation image URL.
- **Option rows**: a repeating key/value pair with an "Add option" button and a remove button per row, seeded with four rows (A–D) for a new objective question. Each input needs an accessible name (`aria-label={\`Option ${key} text\`}`).
- **Correct answer**: for `OBJECTIVE`, a `<select>` whose options are the current option keys — so an unmatched correct answer is structurally impossible in the UI, with the schema as the backstop. For `THEORY`/`FILL_IN_BLANK`, a plain text input, and the option rows are hidden.
- **Validation**: on submit, run `adminQuestionCreateSchema.safeParse` client-side. Map `error.issues` to per-field messages, render each under its field with `id={\`${field}-error\`}`, and set `aria-invalid` and `aria-describedby` on the input. Focus the first invalid field.
- **Form-level status**: a `StatusBanner` above the fields for the server's error, and a `role="status"` summary of the validation failure count.
- **Submit**: `POST /api/admin/questions` for create, `PATCH /api/admin/questions/{id}` for edit. On success, `router.push("/admin/questions")` and `router.refresh()`.
- Fields are laid out in a two-column grid at `sm` and above, with the text areas spanning both columns. Use `rounded-lg` inputs with `border-border` and `focus-visible:ring-2 focus-visible:ring-primary/60`.

Every `<input>`, `<select>` and `<textarea>` must have a real `<label htmlFor>` — the existing list page's unlabelled select (`src/app/admin/questions/page.tsx:100`) is the mistake being corrected here.

- [ ] **Step 2: Build the create page**

`src/app/admin/questions/new/page.tsx` — a server component that loads the subject and topic lists (id, name, code, subjectId) and passes them into `<QuestionForm mode="create" />`, under a `PageHeader title="New question"` with a back link to `/admin/questions`.

- [ ] **Step 3: Build the edit page**

`src/app/admin/questions/[id]/edit/page.tsx` — server component; `const { id } = await params;` (params is a Promise — see the Global Constraints). Load the question via `db.question.findUnique`; call `notFound()` from `next/navigation` when absent. Pass the row as `initial` into `<QuestionForm mode="edit" questionId={id} />`.

- [ ] **Step 4: Link it up**

Add a "New question" `Button` to the questions list header (`src/app/admin/questions/page.tsx:77`), linking to `/admin/questions/new`. Add an edit link per row, next to delete, with `aria-label={\`Edit question: ${q.questionText.slice(0, 60)}\`}`.

- [ ] **Step 5: Verify**

Run: `npx tsc --noEmit`, `npm run lint` — expected: clean.

Manual:
- Create an objective question with four options; confirm it appears in the list and in `/admin` counts (the counts are cached — this proves `revalidateTag` fired).
- Try to submit with a blank question text: the error appears under the field, focus moves there, and no request is sent.
- Change the subject: the topic select must reset rather than keep a topic from the old subject.
- Edit an existing question, change only the explanation, save: confirm the other fields are unchanged in the database.
- Switch question type to THEORY: the option rows disappear and the correct answer becomes free text.
- Complete the whole create flow using only the keyboard.

- [ ] **Step 6: Commit**

```bash
git add src/components/admin/question-form.tsx src/app/admin/questions/new src/app/admin/questions/[id] src/app/admin/questions/page.tsx
git commit -m "feat(admin): add question create and edit forms"
```

---

### Task 13: Import page

**Files:**
- Create: `src/app/admin/questions/import/page.tsx`

**Interfaces:**
- Consumes: `parseImportPayload`, `MAX_IMPORT_ROWS` (Task 3); `POST /api/admin/questions/import`

- [ ] **Step 1: Build the page**

A client component with three phases held in one `phase` state: `"input" | "preview" | "result"`.

**Input phase:** a textarea for pasted JSON and a `<input type="file" accept="application/json">`. Reading a file uses `await file.text()`. Both routes converge on the same string. A "Validate" button (not "Import" — nothing is sent yet) calls `parseImportPayload`.

- On `ok: false`, render a `StatusBanner tone="error"` with the fatal message and stay in the input phase.
- On `ok: true`, move to preview.

Document the expected shape inline, generated from the real fields rather than prose:

```tsx
const SAMPLE = `[
  {
    "subjectCode": "MTH",
    "topicSlug": "algebra",
    "examType": "WAEC",
    "examYear": 2019,
    "questionText": "Solve for x: 2x + 3 = 11",
    "options": { "A": "3", "B": "4", "C": "5", "D": "6" },
    "correctAnswer": "B",
    "explanation": "Subtract 3, then divide by 2.",
    "difficulty": "BASIC"
  }
]`;
```

State the cap plainly next to the input: "Up to {MAX_IMPORT_ROWS} questions per file."

**Preview phase:** a summary line — "{rows.length} of {total} rows are valid" — plus, when `errors.length > 0`, a table of `index`, `field`, `message`. A `skipDuplicates` checkbox with a visible label, defaulting to checked (matching the server default at `src/lib/validators.ts:127`). Buttons: "Back" and "Import {rows.length} questions", the latter disabled when `rows.length === 0`.

When there are errors, the import button must still work on the valid subset — but the page must say explicitly that the errored rows will be skipped, so an admin never believes a partial import was complete.

**Result phase:** POST `{ questions: rows, skipDuplicates }`, then render the server's tally in a `role="status"` region: imported, skipped as duplicates, and a table of the server's own per-index errors (`results.errors` from `src/app/api/admin/questions/import/route.ts:70`). A non-`ok` response renders a `StatusBanner tone="error"` with the server message. Offer "Import another file" (returns to input) and "View questions" (links to `/admin/questions`).

- [ ] **Step 2: Verify**

Manual:
- Paste the sample above and validate: preview reports 1 of 1 valid.
- Paste `{ not json`: a fatal error appears, and nothing is sent.
- Paste an array with one row missing `correctAnswer`: the preview names row index 1 and the field, and the import button offers to import the rest.
- Import a valid file twice with `skipDuplicates` on: the second run reports the rows as skipped, not imported.
- Import a row with a subject code that does not exist: the server's per-index error is displayed verbatim.
- Confirm `/admin` counts change after a successful import.
- Complete the whole flow with the keyboard only.

- [ ] **Step 3: Commit**

```bash
git add src/app/admin/questions/import/page.tsx
git commit -m "feat(admin): add bulk import page with pre-flight validation"
```

---

### Task 14: Final verification pass

**Files:** none created; fixes land in the files they belong to.

- [ ] **Step 1: Run the full gate**

```bash
npm run lint
npx tsc --noEmit
npm test
npm run build
```

Expected: all four clean. `npm run build` is included because a client/server boundary mistake (for example importing `db` into a `"use client"` file) often only surfaces there.

- [ ] **Step 2: Walk the whole console with the keyboard only**

Unplug the mouse, then: `/admin` → click a subject figure → filter the list → create a question → edit it → select two rows → bulk delete → import a file. Every step must be reachable, every focus state visible, no trap, and no action that reports success without having happened.

- [ ] **Step 3: Check both colour schemes**

Toggle the OS between light and dark. Every admin surface must remain legible — if anything is unreadable, the cause is a hard-coded colour that should be a token from `src/app/globals.css`.

- [ ] **Step 4: Confirm the audit trail**

After the walkthrough, query the audit table:

```bash
npx prisma studio
```

`AdminAudit` must hold one row per mutation performed: creates, the update, the deletes, and the import.

- [ ] **Step 5: Commit any fixes and push the branch**

**Never `git add -A` in this repository.** The working tree carries ~104 uncommitted files of unrelated in-progress work that predate this branch. Stage only the admin paths you touched:

```bash
git add src/app/admin src/app/api/admin src/components/admin src/lib/admin-*.ts scripts/test-admin-*.mts
git commit -m "chore(admin): final verification fixes"
git push -u origin admin-console-phase-1
```

---

## Notes for the implementer

- **The three existing bugs this plan fixes are real and confirmed**, not speculative: the per-keystroke refetch (`src/app/admin/questions/page.tsx:48`), the failed-fetch-shows-empty-state fall-through (`:123`), and the restricted-delete 500 caused by `AssessmentQuestion.question` / `QuestionResponse.question` having no `onDelete` (`prisma/schema.prisma:470`, `:505`). If any of them appear already fixed when you arrive, check `git log` before assuming the plan is stale.
- **Do not add `onDelete: Cascade` to those relations to make deletes "work".** Destroying a student's answered-question history to satisfy an admin's delete is the failure mode the dependency check exists to prevent.
- **Order matters between Tasks 9 and 10:** the overview's links depend on the list reading its filters from the URL, which Task 10 implements. Verifying the hrefs is enough until Task 10 lands.
