# Question Provider Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest sdashapi past questions into our own database on first fetch, so any subject/exam/year filter is requested from the provider once and served from Postgres forever after.

**Architecture:** A coverage ledger (`ProviderFetch`) decides whether we call out at all. Every payload is captured verbatim into a staging table (`ProviderQuestion`) before any mapping, then a pure mapper promotes what passes validation into `Question`. Rejected rows keep their raw payload and re-promote offline when the mapper improves. A swept catalogue (`ProviderCatalogue`) lets the practice picker offer papers we do not hold yet.

**Tech Stack:** Next.js 16.2.11 (App Router, `after()` from `next/server`), Prisma + PostgreSQL (Supabase), zod, `node --test` + `tsx` for unit tests.

**Spec:** `docs/superpowers/specs/2026-09-01-question-provider-cache-design.md`

## Global Constraints

- **Never alter an existing table.** The migration is purely additive. `Question.explanation` stays `String @db.Text` (required).
- **Only three exam types are ever requested:** `utme→JAMB`, `wassce→WAEC`, `neco→NECO`. `post-utme` and `university` return `403` and must never be requested.
- **HTTP `404` from the provider means the filter is empty, not that the call failed.** It must mark the ledger `SATURATED` with `rawCount: 0`, never `FAILED`.
- **Provider image URLs must never be stored in `Question.questionImageUrl`.** Images are mirrored to our own Cloudinary on promotion.
- **Migration files must be written with LF line endings.** `core.autocrlf=true` silently drifts Prisma migration checksums.
- **Migrations are applied through the Supabase SQL editor**, never `prisma migrate deploy` — `DIRECT_URL` is unreachable from this machine. Verify against `information_schema`; the editor reports success on partly-applied batches.
- **Stop the dev server before running `prisma generate`** — it fails EPERM on the query engine DLL and leaves a stale client throwing bogus `tsc` errors.
- Saturation constants: draw size **50**, stop under **10** new ids in a draw, hard cap **12** draws.
- New test suites must be added to the `test` script in `package.json` and must pass `npm run typecheck:tests`.

---

## File Structure

**Created:**

| File | Responsibility |
| --- | --- |
| `prisma/migrations/20260902000000_question_provider_cache/migration.sql` | Additive DDL: 3 enums, 3 tables |
| `src/lib/question-provider/alias.ts` | Exam-type and subject-slug alias tables (pure) |
| `src/lib/question-provider/types.ts` | `ProviderFilter`, `QuestionProviderAdapter`, `ProviderError` |
| `src/lib/question-provider/cache-key.ts` | Filter → canonical ledger key (pure) |
| `src/lib/question-provider/errors.ts` | HTTP status → empty / terminal / retryable (pure) |
| `src/lib/question-provider/saturation.ts` | The stop rule and its thresholds (pure) |
| `src/lib/question-provider/mapper.ts` | Raw payload → `MappedQuestion` or rejection reasons (pure) |
| `src/lib/question-provider/sdash.ts` | The sdashapi adapter (injected `fetch`) |
| `src/lib/question-provider/ingest.ts` | Orchestrator: claim, draw, stage, promote, count |
| `scripts/sync-provider-catalogue.ts` | One-off sweep populating `ProviderCatalogue` |
| `scripts/test-provider-alias.mts` | Tests for `alias.ts` |
| `scripts/test-provider-cache-key.mts` | Tests for `cache-key.ts` |
| `scripts/test-provider-errors.mts` | Tests for `errors.ts` |
| `scripts/test-provider-saturation.mts` | Tests for `saturation.ts` |
| `scripts/test-provider-mapper.mts` | Tests for `mapper.ts` |
| `scripts/test-provider-sdash.mts` | Tests for `sdash.ts` against canned responses |
| `src/app/admin/api/provider/backfill/route.ts` | Admin-only backfill trigger |
| `scripts/repromote-provider-questions.ts` | Re-run the mapper over rejected rows, no API calls |

**Modified:**

| File | Change |
| --- | --- |
| `prisma/schema.prisma` | 3 enums, 3 models, 2 back-relations |
| `src/lib/cloudinary.ts` | Add `uploadRemoteImage` |
| `src/lib/validators.ts` | Add `examYear` to `generateQuizSchema`; add `providerBackfillSchema` |
| `src/lib/questions.ts` | `listPastPapers` unions the catalogue; `PastPaper` gains `cached` / nullable count |
| `src/lib/question-pool.ts` | `QuestionPoolFilter` gains `examYear` |
| `src/lib/attempt-lifecycle.ts` | `findResumableAttempt` matches on `examYear` |
| `src/lib/assessment-generation.ts` | Accept `examYear`, call `ensureQuestionsCached`, schedule `after()` |
| `src/components/practice/past-question-picker.tsx` | Render uncached papers (its link already sends `year`) |
| `src/app/(dashboard)/practice/past-questions/[subjectSlug]/page.tsx` | Read `year` from search params |
| `src/components/assessment/quiz-engine.tsx` | Pass `examYear` through to the generate call |
| `package.json` | Register the six new test suites |
| `.env.example` | Three new placeholder keys |

---

### Task 1: Schema and migration

**Files:**
- Create: `prisma/migrations/20260902000000_question_provider_cache/migration.sql`
- Modify: `prisma/schema.prisma`

**Interfaces:**
- Consumes: nothing.
- Produces: Prisma models `ProviderFetch`, `ProviderQuestion`, `ProviderCatalogue`; enums `QuestionProvider` (`SDASH`), `ProviderFetchStatus` (`PENDING`/`SATURATED`/`FAILED`), `ProviderQuestionStatus` (`PENDING`/`PROMOTED`/`REJECTED`). Every later task uses `db.providerFetch`, `db.providerQuestion`, `db.providerCatalogue`.

- [ ] **Step 1: Add the enums to `prisma/schema.prisma`**

Place these next to the existing enums (after `enum Difficulty`, around line 66):

```prisma
enum QuestionProvider {
  SDASH
}

enum ProviderFetchStatus {
  PENDING
  SATURATED
  FAILED
}

enum ProviderQuestionStatus {
  PENDING
  PROMOTED
  REJECTED
}
```

- [ ] **Step 2: Add the three models to `prisma/schema.prisma`**

Append at the end of the file:

```prisma
// ─── Question provider cache ──────────────────────────────
// See docs/superpowers/specs/2026-09-01-question-provider-cache-design.md

/// The coverage ledger. One row per filter we have ever asked the provider for.
/// Its existence — not the presence of questions — is what makes a read a hit.
model ProviderFetch {
  id       String              @id @default(cuid())
  provider QuestionProvider
  cacheKey String
  status   ProviderFetchStatus @default(PENDING)

  subjectId String?
  examType  ExamType?
  examYear  Int?

  drawCount     Int @default(0)
  rawCount      Int @default(0)
  newInLastDraw Int @default(0)
  promotedCount Int @default(0)
  rejectedCount Int @default(0)

  error       String?  @db.Text
  questions   ProviderQuestion[]
  startedAt   DateTime @default(now())
  completedAt DateTime?

  // Doubles as the in-flight lock: the first request to insert wins.
  @@unique([provider, cacheKey])
  @@index([subjectId, examType, examYear])
}

/// Verbatim capture. Written before any mapping is attempted, and never edited.
model ProviderQuestion {
  id      String        @id @default(cuid())
  fetchId String
  fetch   ProviderFetch @relation(fields: [fetchId], references: [id], onDelete: Cascade)

  provider           QuestionProvider
  providerQuestionId String?
  fingerprint        String
  payload            Json

  status           ProviderQuestionStatus @default(PENDING)
  rejectionReasons Json?
  mapperVersion    Int                    @default(1)

  questionId String?   @unique
  question   Question? @relation(fields: [questionId], references: [id], onDelete: SetNull)

  createdAt  DateTime  @default(now())
  promotedAt DateTime?

  @@unique([provider, providerQuestionId])
  @@unique([provider, fingerprint])
  @@index([status, mapperVersion])
}

/// Papers the provider actually holds, so the picker can offer papers we have
/// not fetched yet. Built by scripts/sync-provider-catalogue.ts.
model ProviderCatalogue {
  id        String           @id @default(cuid())
  provider  QuestionProvider
  subjectId String
  subject   Subject          @relation(fields: [subjectId], references: [id], onDelete: Cascade)
  examType  ExamType
  examYear  Int
  syncedAt  DateTime         @updatedAt

  @@unique([provider, subjectId, examType, examYear])
  @@index([examType, examYear])
}
```

- [ ] **Step 3: Add the two back-relations**

These are virtual — they produce no column and no DDL.

In `model Question` (after the `assessmentQuestions` line, around line 468):

```prisma
  providerQuestion ProviderQuestion?
```

In `model Subject` (after the `flashcardDecks` line, around line 297):

```prisma
  providerCatalogue ProviderCatalogue[]
```

- [ ] **Step 4: Validate the schema**

Run: `npx prisma validate`
Expected: `The schema at prisma/schema.prisma is valid 🚀`

If it complains about a missing opposite relation field, step 3 was not applied to both models.

- [ ] **Step 5: Write the migration SQL with LF line endings**

Create the directory and file. **Write it with the Write tool, not a shell heredoc**, then confirm the line endings.

`prisma/migrations/20260902000000_question_provider_cache/migration.sql`:

```sql
-- Question provider cache. Purely additive: no existing table is altered.

CREATE TYPE "QuestionProvider" AS ENUM ('SDASH');
CREATE TYPE "ProviderFetchStatus" AS ENUM ('PENDING', 'SATURATED', 'FAILED');
CREATE TYPE "ProviderQuestionStatus" AS ENUM ('PENDING', 'PROMOTED', 'REJECTED');

CREATE TABLE "ProviderFetch" (
    "id" TEXT NOT NULL,
    "provider" "QuestionProvider" NOT NULL,
    "cacheKey" TEXT NOT NULL,
    "status" "ProviderFetchStatus" NOT NULL DEFAULT 'PENDING',
    "subjectId" TEXT,
    "examType" "ExamType",
    "examYear" INTEGER,
    "drawCount" INTEGER NOT NULL DEFAULT 0,
    "rawCount" INTEGER NOT NULL DEFAULT 0,
    "newInLastDraw" INTEGER NOT NULL DEFAULT 0,
    "promotedCount" INTEGER NOT NULL DEFAULT 0,
    "rejectedCount" INTEGER NOT NULL DEFAULT 0,
    "error" TEXT,
    "startedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completedAt" TIMESTAMP(3),
    CONSTRAINT "ProviderFetch_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "ProviderFetch_provider_cacheKey_key" ON "ProviderFetch"("provider", "cacheKey");
CREATE INDEX "ProviderFetch_subjectId_examType_examYear_idx" ON "ProviderFetch"("subjectId", "examType", "examYear");

CREATE TABLE "ProviderQuestion" (
    "id" TEXT NOT NULL,
    "fetchId" TEXT NOT NULL,
    "provider" "QuestionProvider" NOT NULL,
    "providerQuestionId" TEXT,
    "fingerprint" TEXT NOT NULL,
    "payload" JSONB NOT NULL,
    "status" "ProviderQuestionStatus" NOT NULL DEFAULT 'PENDING',
    "rejectionReasons" JSONB,
    "mapperVersion" INTEGER NOT NULL DEFAULT 1,
    "questionId" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "promotedAt" TIMESTAMP(3),
    CONSTRAINT "ProviderQuestion_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "ProviderQuestion_questionId_key" ON "ProviderQuestion"("questionId");
CREATE UNIQUE INDEX "ProviderQuestion_provider_providerQuestionId_key" ON "ProviderQuestion"("provider", "providerQuestionId");
CREATE UNIQUE INDEX "ProviderQuestion_provider_fingerprint_key" ON "ProviderQuestion"("provider", "fingerprint");
CREATE INDEX "ProviderQuestion_status_mapperVersion_idx" ON "ProviderQuestion"("status", "mapperVersion");

ALTER TABLE "ProviderQuestion" ADD CONSTRAINT "ProviderQuestion_fetchId_fkey" FOREIGN KEY ("fetchId") REFERENCES "ProviderFetch"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "ProviderQuestion" ADD CONSTRAINT "ProviderQuestion_questionId_fkey" FOREIGN KEY ("questionId") REFERENCES "Question"("id") ON DELETE SET NULL ON UPDATE CASCADE;

CREATE TABLE "ProviderCatalogue" (
    "id" TEXT NOT NULL,
    "provider" "QuestionProvider" NOT NULL,
    "subjectId" TEXT NOT NULL,
    "examType" "ExamType" NOT NULL,
    "examYear" INTEGER NOT NULL,
    "syncedAt" TIMESTAMP(3) NOT NULL,
    CONSTRAINT "ProviderCatalogue_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "ProviderCatalogue_provider_subjectId_examType_examYear_key" ON "ProviderCatalogue"("provider", "subjectId", "examType", "examYear");
CREATE INDEX "ProviderCatalogue_examType_examYear_idx" ON "ProviderCatalogue"("examType", "examYear");

ALTER TABLE "ProviderCatalogue" ADD CONSTRAINT "ProviderCatalogue_subjectId_fkey" FOREIGN KEY ("subjectId") REFERENCES "Subject"("id") ON DELETE CASCADE ON UPDATE CASCADE;
```

- [ ] **Step 6: Verify the file has no CRLF**

Run: `file prisma/migrations/20260902000000_question_provider_cache/migration.sql`
Expected: `ASCII text` — **not** `ASCII text, with CRLF line terminators`.

If it shows CRLF, fix it before going further:
`perl -pi -e 's/\r\n/\n/g' prisma/migrations/20260902000000_question_provider_cache/migration.sql`

- [ ] **Step 7: Apply the migration through the Supabase SQL editor**

Paste the whole file into the SQL editor and run it. **Do not trust the success message.**

- [ ] **Step 8: Verify against the catalog, not the success message**

Run this in the SQL editor:

```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('ProviderFetch','ProviderQuestion','ProviderCatalogue')
ORDER BY table_name;

SELECT typname FROM pg_type
WHERE typname IN ('QuestionProvider','ProviderFetchStatus','ProviderQuestionStatus')
ORDER BY typname;

SELECT is_nullable FROM information_schema.columns
WHERE table_name = 'Question' AND column_name = 'explanation';
```

Expected: 3 table rows, 3 type rows, and `is_nullable = NO` — confirming `Question` was left alone.

- [ ] **Step 9: Regenerate the Prisma client**

Stop the dev server first (EPERM on the query engine DLL otherwise).

Run: `npx prisma generate`
Expected: `Generated Prisma Client`

- [ ] **Step 10: Confirm the client typechecks**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors.

- [ ] **Step 11: Commit**

```bash
git add prisma/schema.prisma prisma/migrations/20260902000000_question_provider_cache/migration.sql
git commit -m "feat(provider): add question provider cache tables"
```

---

### Task 2: Alias tables

**Files:**
- Create: `src/lib/question-provider/alias.ts`, `src/lib/question-provider/types.ts`
- Test: `scripts/test-provider-alias.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `SupportedExamType = "WAEC" | "JAMB" | "NECO"`
  - `toExamType(providerSlug: string): SupportedExamType | null`
  - `toProviderExamSlug(examType: string): "utme" | "wassce" | "neco" | null`
  - `toProviderSubjectSlug(ourSlug: string): string | null`
  - `PROVIDER_EXAM_SLUGS: readonly ("utme"|"wassce"|"neco")[]`
  - `ProviderFilter = { subjectSlug: string; examType: SupportedExamType; examYear: number }` (from `types.ts`)
  - `QuestionProviderAdapter` interface and `ProviderError` class (from `types.ts`)

- [ ] **Step 1: Write the failing test**

`scripts/test-provider-alias.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  toExamType,
  toProviderExamSlug,
  toProviderSubjectSlug,
  PROVIDER_EXAM_SLUGS,
} from "../src/lib/question-provider/alias";

test("the three supported exam slugs map to our enum", () => {
  assert.equal(toExamType("utme"), "JAMB");
  assert.equal(toExamType("wassce"), "WAEC");
  assert.equal(toExamType("neco"), "NECO");
});

test("exam slugs are matched case- and whitespace-insensitively", () => {
  assert.equal(toExamType(" UTME "), "JAMB");
});

test("unentitled exam types are refused, not folded into CUSTOM", () => {
  // The provider answers these with 403; requesting them is a bug.
  assert.equal(toExamType("post-utme"), null);
  assert.equal(toExamType("university"), null);
  assert.equal(toExamType("waec"), null); // their slug is "wassce"
});

test("our exam enum maps back to their slug", () => {
  assert.equal(toProviderExamSlug("JAMB"), "utme");
  assert.equal(toProviderExamSlug("WAEC"), "wassce");
  assert.equal(toProviderExamSlug("NECO"), "neco");
  assert.equal(toProviderExamSlug("CUSTOM"), null);
});

test("PROVIDER_EXAM_SLUGS holds exactly the three we request", () => {
  assert.deepEqual([...PROVIDER_EXAM_SLUGS], ["utme", "wassce", "neco"]);
});

test("single-word subjects map to themselves", () => {
  assert.equal(toProviderSubjectSlug("chemistry"), "chemistry");
  assert.equal(toProviderSubjectSlug("mathematics"), "mathematics");
  assert.equal(toProviderSubjectSlug("biology"), "biology");
});

test("multi-word subjects map through the alias table", () => {
  assert.equal(toProviderSubjectSlug("english-language"), "english");
  assert.equal(toProviderSubjectSlug("literature-in-english"), "englishlit");
  assert.equal(toProviderSubjectSlug("christian-religious-studies"), "crk");
  assert.equal(toProviderSubjectSlug("islamic-studies"), "irk");
  assert.equal(toProviderSubjectSlug("civic-education"), "civiledu");
  assert.equal(toProviderSubjectSlug("computer-studies"), "computer");
  assert.equal(toProviderSubjectSlug("fine-art"), "fineart");
  assert.equal(toProviderSubjectSlug("agricultural-science"), "agriculture");
  assert.equal(toProviderSubjectSlug("financial-accounting"), "accounting");
});

test("subjects the provider does not carry return null", () => {
  // Measured 2026-09-02: absent from their /v1/subjects response.
  for (const slug of [
    "further-mathematics",
    "technical-drawing",
    "health-education",
    "marketing",
    "office-practice",
    "french",
  ]) {
    assert.equal(toProviderSubjectSlug(slug), null, `${slug} should be unmapped`);
  }
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-alias.mts`
Expected: FAIL — cannot find module `../src/lib/question-provider/alias`.

- [ ] **Step 3: Write `src/lib/question-provider/alias.ts`**

```ts
// Translations between our vocabulary and sdashapi's.
//
// Written from their live GET /v1/subjects on 2026-09-02, not guessed.
// scripts/sync-provider-catalogue.ts re-verifies it against the live list and
// fails loudly if a mapped slug disappears.

export type SupportedExamType = "WAEC" | "JAMB" | "NECO";
export type ProviderExamSlug = "utme" | "wassce" | "neco";

/**
 * The only exam types we ever request. `post-utme` and `university` exist in
 * their API but answer 403 — our token is not entitled to them — and our
 * ExamType enum could only represent them as CUSTOM, which would corrupt
 * past-paper grouping.
 */
export const PROVIDER_EXAM_SLUGS = ["utme", "wassce", "neco"] as const;

const EXAM_BY_SLUG: Record<ProviderExamSlug, SupportedExamType> = {
  utme: "JAMB",
  wassce: "WAEC",
  neco: "NECO",
};

export function toExamType(providerSlug: string): SupportedExamType | null {
  const key = providerSlug.trim().toLowerCase() as ProviderExamSlug;
  return EXAM_BY_SLUG[key] ?? null;
}

export function toProviderExamSlug(examType: string): ProviderExamSlug | null {
  const found = PROVIDER_EXAM_SLUGS.find(
    (slug) => EXAM_BY_SLUG[slug] === examType.trim().toUpperCase(),
  );
  return found ?? null;
}

/** Every slug their /v1/subjects returned. */
const PROVIDER_SUBJECT_SLUGS = new Set([
  "accounting", "agriculture", "arabic", "biology", "chemistry", "civiledu",
  "commerce", "computer", "crk", "currentaffairs", "economics", "english",
  "englishlit", "fineart", "geography", "government", "hausa", "history",
  "homeeconomics", "igbo", "independ", "insurance", "irk", "lastdays",
  "lekki", "lifechanger", "mathematics", "music", "physics", "sweetsixteen",
  "yoruba",
]);

/**
 * Our `Subject.slug` (from `slugify(name)` in prisma/seed.ts) to theirs, for
 * the cases where they differ. Anything not listed is tried as-is.
 */
const SUBJECT_ALIASES: Record<string, string> = {
  "english-language": "english",
  "literature-in-english": "englishlit",
  "christian-religious-studies": "crk",
  "islamic-studies": "irk",
  "civic-education": "civiledu",
  "computer-studies": "computer",
  "fine-art": "fineart",
  "agricultural-science": "agriculture",
  "financial-accounting": "accounting",
};

/**
 * Null means the provider does not carry the subject at all — measured cases
 * are Further Mathematics, Technical Drawing, Health Education, Marketing,
 * Office Practice and French. Those subjects are simply outside this feature.
 */
export function toProviderSubjectSlug(ourSlug: string): string | null {
  const normalised = ourSlug.trim().toLowerCase();
  const candidate = SUBJECT_ALIASES[normalised] ?? normalised;
  return PROVIDER_SUBJECT_SLUGS.has(candidate) ? candidate : null;
}
```

- [ ] **Step 4: Write `src/lib/question-provider/types.ts`**

```ts
import type { SupportedExamType } from "./alias";

/** The unit of coverage: one subject's paper for one exam in one year. */
export type ProviderFilter = {
  /** Our Subject.slug, not theirs — the adapter translates. */
  subjectSlug: string;
  examType: SupportedExamType;
  examYear: number;
};

/**
 * How a failed call should be treated.
 *
 * "empty" is deliberately absent: a filter the provider has nothing for
 * returns an empty array, not an error, so the ledger saturates it with
 * rawCount 0 and never asks again.
 */
export type ProviderFailureKind = "terminal" | "retryable";

export class ProviderError extends Error {
  constructor(
    message: string,
    readonly kind: ProviderFailureKind,
    readonly httpStatus: number | null = null,
  ) {
    super(message);
    this.name = "ProviderError";
  }
}

export interface QuestionProviderAdapter {
  readonly name: "SDASH";
  /**
   * One draw. Returns the raw, unvalidated payloads — validation belongs to
   * the mapper, which runs against what we stored rather than what came off
   * the wire. An empty array means the provider holds nothing for the filter.
   */
  draw(filter: ProviderFilter, limit: number): Promise<unknown[]>;
  listSubjects(): Promise<{ id: number; name: string; slug: string }[]>;
  listYears(): Promise<number[]>;
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-alias.mts`
Expected: PASS, 8 tests.

- [ ] **Step 6: Register the suite in `package.json`**

Append `scripts/test-provider-alias.mts` to the end of the `test` script's file list.

- [ ] **Step 7: Typecheck and run the full suite**

Run: `npm run typecheck:tests && npm test`
Expected: no type errors; all suites pass.

- [ ] **Step 8: Commit**

```bash
git add src/lib/question-provider/alias.ts src/lib/question-provider/types.ts scripts/test-provider-alias.mts package.json
git commit -m "feat(provider): add exam and subject alias tables"
```

---

### Task 3: Cache key

**Files:**
- Create: `src/lib/question-provider/cache-key.ts`
- Test: `scripts/test-provider-cache-key.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `ProviderFilter` from `types.ts`.
- Produces: `cacheKey(filter: ProviderFilter): string`.

- [ ] **Step 1: Write the failing test**

`scripts/test-provider-cache-key.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { cacheKey } from "../src/lib/question-provider/cache-key";

test("a filter serialises to a stable canonical key", () => {
  assert.equal(
    cacheKey({ subjectSlug: "chemistry", examType: "JAMB", examYear: 2022 }),
    "chemistry|JAMB|2022",
  );
});

test("casing and surrounding whitespace do not create a second key", () => {
  const a = cacheKey({ subjectSlug: "chemistry", examType: "JAMB", examYear: 2022 });
  const b = cacheKey({ subjectSlug: " Chemistry ", examType: "JAMB", examYear: 2022 });
  assert.equal(a, b);
});

test("different years never collide", () => {
  const a = cacheKey({ subjectSlug: "chemistry", examType: "JAMB", examYear: 2022 });
  const b = cacheKey({ subjectSlug: "chemistry", examType: "JAMB", examYear: 2021 });
  assert.notEqual(a, b);
});

test("different exam types never collide", () => {
  const a = cacheKey({ subjectSlug: "biology", examType: "WAEC", examYear: 2018 });
  const b = cacheKey({ subjectSlug: "biology", examType: "NECO", examYear: 2018 });
  assert.notEqual(a, b);
});

test("a subject whose name contains the separator cannot forge another key", () => {
  // Guards against "a|B" + "C" colliding with "a" + "B|C".
  const a = cacheKey({ subjectSlug: "chemistry|JAMB", examType: "NECO", examYear: 2022 });
  const b = cacheKey({ subjectSlug: "chemistry", examType: "JAMB", examYear: 2022 });
  assert.notEqual(a, b);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-cache-key.mts`
Expected: FAIL — cannot find module.

- [ ] **Step 3: Write `src/lib/question-provider/cache-key.ts`**

```ts
import type { ProviderFilter } from "./types";

/**
 * The ledger's identity for a filter.
 *
 * Stored in ProviderFetch.cacheKey under a unique constraint, so it is both
 * the coverage record and the in-flight lock — two requests for the same paper
 * cannot both call the provider. Normalisation matters: " Chemistry " and
 * "chemistry" must not buy two fetches of the same paper.
 */
export function cacheKey(filter: ProviderFilter): string {
  const subject = filter.subjectSlug.trim().toLowerCase().replaceAll("|", "%7C");
  return `${subject}|${filter.examType}|${filter.examYear}`;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-cache-key.mts`
Expected: PASS, 5 tests.

- [ ] **Step 5: Register the suite and commit**

Append `scripts/test-provider-cache-key.mts` to the `test` script, then:

```bash
npm run typecheck:tests && npm test
git add src/lib/question-provider/cache-key.ts scripts/test-provider-cache-key.mts package.json
git commit -m "feat(provider): add canonical filter cache key"
```

---

### Task 4: Response classifier

**Files:**
- Create: `src/lib/question-provider/errors.ts`
- Test: `scripts/test-provider-errors.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: nothing.
- Produces: `classifyStatus(httpStatus: number): "ok" | "empty" | "terminal" | "retryable"`.

This is the single highest-risk pure function in the feature: misclassifying `404` as a failure reintroduces unbounded retries against combinations the provider will never have.

- [ ] **Step 1: Write the failing test**

`scripts/test-provider-errors.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { classifyStatus } from "../src/lib/question-provider/errors";

test("200 is a successful draw", () => {
  assert.equal(classifyStatus(200), "ok");
});

test("404 means the filter is empty, NOT that the call failed", () => {
  // Measured: {"status":404,"message":"No questions found for those filters."}
  // Treating this as a failure would retry empty combinations forever.
  assert.equal(classifyStatus(404), "empty");
});

test("403 is terminal — our token is not entitled to that exam", () => {
  assert.equal(classifyStatus(403), "terminal");
});

test("401 is terminal — the token is bad and retrying cannot help", () => {
  assert.equal(classifyStatus(401), "terminal");
});

test("429 is retryable, not terminal", () => {
  assert.equal(classifyStatus(429), "retryable");
});

test("server errors are retryable", () => {
  assert.equal(classifyStatus(500), "retryable");
  assert.equal(classifyStatus(503), "retryable");
});

test("unexpected 4xx codes are retryable rather than silently empty", () => {
  // Being wrong toward "retryable" costs a call; being wrong toward "empty"
  // permanently marks a real paper as having nothing in it.
  assert.equal(classifyStatus(418), "retryable");
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-errors.mts`
Expected: FAIL — cannot find module.

- [ ] **Step 3: Write `src/lib/question-provider/errors.ts`**

```ts
/**
 * How to treat a provider response, by status code. Measured against the live
 * API on 2026-09-02; their body echoes the same code in a `status` field.
 *
 *   200 -> a draw
 *   404 -> {"status":404,"message":"No questions found for those filters."}
 *   403 -> {"status":403,"message":"...no permission to query the \"x\" exam."}
 *   401 -> {"status":401,"message":"Invalid AccessToken."}
 */
export type ResponseClass = "ok" | "empty" | "terminal" | "retryable";

export function classifyStatus(httpStatus: number): ResponseClass {
  if (httpStatus === 200) return "ok";

  // The filter is genuinely empty. The ledger saturates it with rawCount 0 so
  // we never ask again — the catalogue contains combinations with nothing in
  // them, and retrying those forever is the runaway this design prevents.
  if (httpStatus === 404) return "empty";

  // Bad credentials or an exam our plan does not include. Retrying cannot fix
  // either, and hammering a 403 is how a key gets revoked.
  if (httpStatus === 401 || httpStatus === 403) return "terminal";

  // Everything else — throttling, server faults, anything unrecognised. Erring
  // toward "retryable" costs one call; erring toward "empty" would brand a
  // real paper as permanently barren.
  return "retryable";
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-errors.mts`
Expected: PASS, 7 tests.

- [ ] **Step 5: Register the suite and commit**

```bash
npm run typecheck:tests && npm test
git add src/lib/question-provider/errors.ts scripts/test-provider-errors.mts package.json
git commit -m "feat(provider): classify provider responses as empty, terminal or retryable"
```

---

### Task 5: Saturation rule

**Files:**
- Create: `src/lib/question-provider/saturation.ts`
- Test: `scripts/test-provider-saturation.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: nothing.
- Produces: `DRAW_LIMIT = 50`, `MIN_NEW_PER_DRAW = 10`, `MAX_DRAWS = 12`, and
  `isSaturated(input: { drawCount: number; returnedCount: number; newInLastDraw: number }): boolean`.

- [ ] **Step 1: Write the failing test**

`scripts/test-provider-saturation.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  isSaturated,
  DRAW_LIMIT,
  MIN_NEW_PER_DRAW,
  MAX_DRAWS,
} from "../src/lib/question-provider/saturation";

test("a draw yielding too few new ids saturates", () => {
  assert.equal(
    isSaturated({ drawCount: 5, returnedCount: 50, newInLastDraw: 9 }),
    true,
  );
});

test("a draw just above the threshold does not saturate", () => {
  assert.equal(
    isSaturated({ drawCount: 5, returnedCount: 50, newInLastDraw: 10 }),
    false,
  );
});

test("a short draw means the pool is smaller than one batch", () => {
  assert.equal(
    isSaturated({ drawCount: 1, returnedCount: 37, newInLastDraw: 37 }),
    true,
  );
});

test("an empty draw saturates immediately", () => {
  // The 404 path: nothing there, never ask again.
  assert.equal(
    isSaturated({ drawCount: 1, returnedCount: 0, newInLastDraw: 0 }),
    true,
  );
});

test("the hard cap stops a pathologically deep pool", () => {
  assert.equal(
    isSaturated({ drawCount: MAX_DRAWS, returnedCount: 50, newInLastDraw: 40 }),
    true,
  );
});

test("the measured decay does NOT saturate early", () => {
  // Live measurement for chemistry/utme/2022 on 2026-09-02: successive draws
  // yielded 50, 39, 32, 26 new ids. If a threshold change makes this stop at
  // draw 4 we would cache roughly a third of that pool and call it done.
  const measured = [50, 39, 32, 26];
  measured.forEach((newInLastDraw, index) => {
    assert.equal(
      isSaturated({ drawCount: index + 1, returnedCount: 50, newInLastDraw }),
      false,
      `draw ${index + 1} (${newInLastDraw} new) should not saturate`,
    );
  });
});

test("the constants are the values the spec was calibrated on", () => {
  assert.equal(DRAW_LIMIT, 50);
  assert.equal(MIN_NEW_PER_DRAW, 10);
  assert.equal(MAX_DRAWS, 12);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-saturation.mts`
Expected: FAIL — cannot find module.

- [ ] **Step 3: Write `src/lib/question-provider/saturation.ts`**

```ts
// When to stop drawing a filter.
//
// The provider caps `limit` at 50 and exposes no offset, page or total, and a
// measurement on 2026-09-02 showed draws are randomly redrawn from a pool of
// roughly 300-400 per filter — not a 40-question paper. So "keep going until a
// draw yields nothing new" is a coupon-collector problem that would never
// terminate inside a sane budget. We stop on diminishing returns instead.

/** Their maximum, and what we always request. */
export const DRAW_LIMIT = 50;

/** Below this many new ids in a draw, the pool's useful yield has collapsed. */
export const MIN_NEW_PER_DRAW = 10;

/** At 12 draws we expect ~85% of a 350-question pool. */
export const MAX_DRAWS = 12;

export function isSaturated(input: {
  drawCount: number;
  returnedCount: number;
  newInLastDraw: number;
}): boolean {
  // Fewer than a full batch: their bank for this filter is smaller than one
  // draw, so we have just seen all of it. Also covers the 404/empty case.
  if (input.returnedCount < DRAW_LIMIT) return true;

  if (input.newInLastDraw < MIN_NEW_PER_DRAW) return true;

  return input.drawCount >= MAX_DRAWS;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-saturation.mts`
Expected: PASS, 7 tests.

- [ ] **Step 5: Register the suite and commit**

```bash
npm run typecheck:tests && npm test
git add src/lib/question-provider/saturation.ts scripts/test-provider-saturation.mts package.json
git commit -m "feat(provider): add diminishing-returns saturation rule"
```

---

### Task 6: The mapper

**Files:**
- Create: `src/lib/question-provider/mapper.ts`
- Test: `scripts/test-provider-mapper.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `toExamType` from `alias.ts`; `normalizeOptions`, `checkQuestionInvariants`, `InvariantIssue` from `src/lib/admin-question.ts`.
- Produces:
  - `MAPPER_VERSION = 1`
  - `type MappedQuestion = { questionText: string; options: Record<string,string>; correctAnswer: string; explanation: string; examType: SupportedExamType; examYear: number; providerImageUrl: string | null }`
  - `type MapResult = { ok: true; question: MappedQuestion; providerQuestionId: string | null; fingerprint: string } | { ok: false; reasons: InvariantIssue[]; providerQuestionId: string | null; fingerprint: string }`
  - `mapProviderQuestion(payload: unknown): MapResult`
  - `fingerprintPayload(payload: unknown): string`

Note `MapResult` carries `providerQuestionId` and `fingerprint` on **both** branches — a rejected row still has to be staged under its dedupe keys.

- [ ] **Step 1: Write the failing test**

`scripts/test-provider-mapper.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  mapProviderQuestion,
  fingerprintPayload,
  MAPPER_VERSION,
} from "../src/lib/question-provider/mapper";

/** The documented payload, captured live on 2026-09-02. */
function payload(overrides: Record<string, unknown> = {}) {
  return {
    id: 4821,
    question: "Which of the following is the chemical formula for table salt?",
    section: null,
    option: { a: "NaCl", b: "KCl", c: "CaCO3", d: "NaOH" },
    answer: "a",
    solution: "NaCl is sodium chloride...",
    image: null,
    examtype: "UTME",
    examyear: "2022",
    ...overrides,
  };
}

function reasonFields(result: ReturnType<typeof mapProviderQuestion>) {
  return result.ok ? [] : result.reasons.map((r) => r.field);
}

test("the documented payload maps cleanly", () => {
  const result = mapProviderQuestion(payload());
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal(result.providerQuestionId, "4821");
  assert.equal(result.question.examType, "JAMB");
  assert.equal(result.question.examYear, 2022);
  assert.equal(result.question.correctAnswer, "A");
  assert.deepEqual(result.question.options, {
    A: "NaCl", B: "KCl", C: "CaCO3", D: "NaOH",
  });
  assert.equal(result.question.providerImageUrl, null);
});

test("option keys are upper-cased and the answer follows them", () => {
  const result = mapProviderQuestion(payload({ answer: "c" }));
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal(result.question.correctAnswer, "C");
});

test("WASSCE and NECO map to our enum", () => {
  for (const [theirs, ours] of [["WASSCE", "WAEC"], ["NECO", "NECO"]]) {
    const result = mapProviderQuestion(payload({ examtype: theirs }));
    assert.equal(result.ok, true, `${theirs} should map`);
    if (!result.ok) return;
    assert.equal(result.question.examType, ours);
  }
});

test("an image URL is carried through unmirrored", () => {
  const url = "https://res.cloudinary.com/aloc-ng/image/upload/v1/x.jpg";
  const result = mapProviderQuestion(payload({ image: url }));
  assert.equal(result.ok, true);
  if (!result.ok) return;
  // The mapper is pure; mirroring happens at promotion time.
  assert.equal(result.question.providerImageUrl, url);
});

test("options given out of order still map by key", () => {
  const result = mapProviderQuestion(
    payload({ option: { d: "NaOH", b: "KCl", a: "NaCl", c: "CaCO3" } }),
  );
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal(result.question.options.A, "NaCl");
  assert.equal(result.question.options.D, "NaOH");
});

test("an answer that is not an option key is rejected", () => {
  // The dangerous case: this marks every student wrong, silently.
  const result = mapProviderQuestion(payload({ answer: "e" }));
  assert.equal(result.ok, false);
  assert.ok(reasonFields(result).includes("correctAnswer"));
});

test("fewer than four usable options is rejected", () => {
  const result = mapProviderQuestion(
    payload({ option: { a: "NaCl", b: "KCl", c: "CaCO3" } }),
  );
  assert.equal(result.ok, false);
  assert.ok(reasonFields(result).includes("options"));
});

test("blank option values do not count toward the four", () => {
  const result = mapProviderQuestion(
    payload({ option: { a: "NaCl", b: "KCl", c: "CaCO3", d: "   " } }),
  );
  assert.equal(result.ok, false);
  assert.ok(reasonFields(result).includes("options"));
});

test("empty question text is rejected", () => {
  const result = mapProviderQuestion(payload({ question: "   " }));
  assert.equal(result.ok, false);
  assert.ok(reasonFields(result).includes("questionText"));
});

test("a missing solution is rejected — explanation is required", () => {
  for (const solution of [null, undefined, "", "   "]) {
    const result = mapProviderQuestion(payload({ solution }));
    assert.equal(result.ok, false, `solution=${String(solution)}`);
    assert.ok(reasonFields(result).includes("explanation"));
  }
});

test("an unparseable year is rejected", () => {
  const result = mapProviderQuestion(payload({ examyear: "n/a" }));
  assert.equal(result.ok, false);
  assert.ok(reasonFields(result).includes("examYear"));
});

test("an unrequestable exam type is rejected", () => {
  const result = mapProviderQuestion(payload({ examtype: "POST-UTME" }));
  assert.equal(result.ok, false);
  assert.ok(reasonFields(result).includes("examType"));
});

test("a rejected payload still carries its dedupe keys", () => {
  // Staging needs them even when promotion fails, or the row cannot be
  // written and we would re-fetch it forever.
  const result = mapProviderQuestion(payload({ answer: "e" }));
  assert.equal(result.ok, false);
  if (result.ok) return;
  assert.equal(result.providerQuestionId, "4821");
  assert.equal(typeof result.fingerprint, "string");
  assert.equal(result.fingerprint.length, 64);
});

test("garbage input is rejected rather than thrown", () => {
  for (const junk of [null, undefined, 42, "text", []]) {
    const result = mapProviderQuestion(junk);
    assert.equal(result.ok, false, `${JSON.stringify(junk)} should reject`);
  }
});

test("the fingerprint is stable across key order and whitespace", () => {
  const a = fingerprintPayload(payload());
  const b = fingerprintPayload(
    payload({ option: { d: "NaOH", c: "CaCO3", b: "KCl", a: "NaCl" } }),
  );
  assert.equal(a, b);
});

test("the fingerprint differs for different questions", () => {
  assert.notEqual(
    fingerprintPayload(payload()),
    fingerprintPayload(payload({ question: "Something else entirely?" })),
  );
});

test("the fingerprint ignores fields that are not identity", () => {
  // Re-issued under a new id with a rewritten solution: still the same question.
  assert.equal(
    fingerprintPayload(payload()),
    fingerprintPayload(payload({ id: 99999, solution: "Rewritten." })),
  );
});

test("MAPPER_VERSION is exported for the re-promotion sweep", () => {
  assert.equal(typeof MAPPER_VERSION, "number");
  assert.ok(MAPPER_VERSION >= 1);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-mapper.mts`
Expected: FAIL — cannot find module.

- [ ] **Step 3: Write `src/lib/question-provider/mapper.ts`**

```ts
import crypto from "crypto";
import {
  checkQuestionInvariants,
  normalizeOptions,
  type InvariantIssue,
} from "@/lib/admin-question";
import { toExamType, type SupportedExamType } from "./alias";

/**
 * Bump when the mapping rules change. Rows rejected under an older version are
 * re-run offline against the stored payload — no new API calls — so improving
 * this function retroactively grows the bank.
 */
export const MAPPER_VERSION = 1;

export type MappedQuestion = {
  questionText: string;
  options: Record<string, string>;
  correctAnswer: string;
  explanation: string;
  examType: SupportedExamType;
  examYear: number;
  /** Their URL. Mirrored to our own Cloudinary at promotion time, never stored. */
  providerImageUrl: string | null;
};

export type MapResult =
  | {
      ok: true;
      question: MappedQuestion;
      providerQuestionId: string | null;
      fingerprint: string;
    }
  | {
      ok: false;
      reasons: InvariantIssue[];
      providerQuestionId: string | null;
      fingerprint: string;
    };

const str = (v: unknown): string => (typeof v === "string" ? v.trim() : "");
const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** Their option values, trimmed, with blanks dropped and keys upper-cased. */
function readOptions(raw: unknown): Record<string, string> {
  if (!isRecord(raw)) return {};
  const usable: Record<string, string> = {};
  for (const [key, value] of Object.entries(raw)) {
    const text = str(value);
    if (text) usable[key] = text;
  }
  return normalizeOptions(usable).options ?? {};
}

/**
 * Identity hash over question text and options only.
 *
 * Their `id` is stable and is the primary dedupe key; this is the fallback,
 * and it also catches the same question re-issued under a new id. Deliberately
 * excludes `solution`, `id` and `image` so an edited explanation does not read
 * as a different question.
 */
export function fingerprintPayload(payload: unknown): string {
  const source = isRecord(payload) ? payload : {};
  const options = readOptions(source.option);
  const canonical = JSON.stringify({
    question: str(source.question).toLowerCase().replace(/\s+/g, " "),
    options: Object.keys(options)
      .sort()
      .map((key) => [key, options[key].toLowerCase().replace(/\s+/g, " ")]),
  });
  return crypto.createHash("sha256").update(canonical).digest("hex");
}

export function mapProviderQuestion(payload: unknown): MapResult {
  const fingerprint = fingerprintPayload(payload);
  const source = isRecord(payload) ? payload : {};
  const providerQuestionId =
    typeof source.id === "number" || typeof source.id === "string"
      ? String(source.id)
      : null;

  const reject = (reasons: InvariantIssue[]): MapResult => ({
    ok: false,
    reasons,
    providerQuestionId,
    fingerprint,
  });

  if (!isRecord(payload)) {
    return reject([{ field: "payload", message: "Payload is not an object." }]);
  }

  const reasons: InvariantIssue[] = [];

  const questionText = str(source.question);
  if (!questionText) {
    reasons.push({ field: "questionText", message: "Question text is empty." });
  }

  // Question.explanation is required and stays that way, so a question with no
  // solution cannot be served. It is still captured, and promotes later once
  // an explanation exists.
  const explanation = str(source.solution);
  if (!explanation) {
    reasons.push({
      field: "explanation",
      message: "The provider supplied no solution for this question.",
    });
  }

  const examType = toExamType(str(source.examtype));
  if (!examType) {
    reasons.push({
      field: "examType",
      message: `Unsupported exam type: "${str(source.examtype) || "(missing)"}".`,
    });
  }

  const examYear = parseInt(str(source.examyear), 10);
  if (!Number.isInteger(examYear)) {
    reasons.push({
      field: "examYear",
      message: `Unparseable exam year: "${str(source.examyear) || "(missing)"}".`,
    });
  }

  const options = readOptions(source.option);
  const correctAnswer = str(source.answer).toUpperCase();

  // The same gate the admin bulk import passes through: it is what stops a
  // question whose answer is not one of its options reaching a student.
  reasons.push(
    ...checkQuestionInvariants({
      questionType: "OBJECTIVE",
      options,
      correctAnswer,
    }),
  );

  if (reasons.length > 0 || !examType || !Number.isInteger(examYear)) {
    return reject(reasons);
  }

  return {
    ok: true,
    providerQuestionId,
    fingerprint,
    question: {
      questionText,
      options,
      correctAnswer,
      explanation,
      examType,
      examYear,
      providerImageUrl: str(source.image) || null,
    },
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-mapper.mts`
Expected: PASS, 17 tests.

- [ ] **Step 5: Register the suite and commit**

```bash
npm run typecheck:tests && npm test
git add src/lib/question-provider/mapper.ts scripts/test-provider-mapper.mts package.json
git commit -m "feat(provider): map provider payloads to questions or rejections"
```

---

### Task 7: The sdashapi adapter

**Files:**
- Create: `src/lib/question-provider/sdash.ts`
- Test: `scripts/test-provider-sdash.mts`
- Modify: `package.json`, `.env.example`

**Interfaces:**
- Consumes: `QuestionProviderAdapter`, `ProviderError`, `ProviderFilter` from `types.ts`; `classifyStatus` from `errors.ts`; `toProviderExamSlug`, `toProviderSubjectSlug` from `alias.ts`; `DRAW_LIMIT` from `saturation.ts`.
- Produces:
  - `createSdashAdapter(config: { baseUrl: string; token: string; fetchImpl?: typeof fetch }): QuestionProviderAdapter`
  - `getSdashAdapter(): QuestionProviderAdapter` — reads env, throws if unconfigured.

- [ ] **Step 1: Write the failing test**

`scripts/test-provider-sdash.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { createSdashAdapter } from "../src/lib/question-provider/sdash";
import { ProviderError } from "../src/lib/question-provider/types";

const QUESTION = {
  id: 4821,
  question: "Which of the following is the chemical formula for table salt?",
  section: null,
  option: { a: "NaCl", b: "KCl", c: "CaCO3", d: "NaOH" },
  answer: "a",
  solution: "NaCl is sodium chloride...",
  image: null,
  examtype: "UTME",
  examyear: "2022",
};

/** Records the requests made, and replays canned responses. */
function stubFetch(responses: { status: number; body: unknown }[]) {
  const calls: { url: string; token: string | null }[] = [];
  let index = 0;
  const impl = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    const headers = new Headers(init?.headers);
    calls.push({ url, token: headers.get("AccessToken") });
    const canned = responses[Math.min(index++, responses.length - 1)];
    return new Response(JSON.stringify(canned.body), {
      status: canned.status,
      headers: { "content-type": "application/json" },
    });
  }) as unknown as typeof fetch;
  return { impl, calls };
}

function adapter(responses: { status: number; body: unknown }[]) {
  const { impl, calls } = stubFetch(responses);
  return {
    subject: createSdashAdapter({
      baseUrl: "https://sdashapi.com/api",
      token: "test-token",
      fetchImpl: impl,
    }),
    calls,
  };
}

test("a draw sends our slugs translated into theirs, with the token header", async () => {
  const { subject, calls } = adapter([{ status: 200, body: { status: 200, data: [QUESTION] } }]);
  await subject.draw({ subjectSlug: "english-language", examType: "JAMB", examYear: 2022 }, 50);

  assert.equal(calls.length, 1);
  const url = new URL(calls[0].url);
  assert.equal(url.pathname, "/api/v1/q");
  assert.equal(url.searchParams.get("subject"), "english");
  assert.equal(url.searchParams.get("type"), "utme");
  assert.equal(url.searchParams.get("year"), "2022");
  assert.equal(url.searchParams.get("limit"), "50");
  assert.equal(calls[0].token, "test-token");
});

test("an array payload comes back as an array", async () => {
  const { subject } = adapter([
    { status: 200, body: { status: 200, data: [QUESTION, { ...QUESTION, id: 4822 }] } },
  ]);
  const rows = await subject.draw({ subjectSlug: "chemistry", examType: "JAMB", examYear: 2022 }, 50);
  assert.equal(rows.length, 2);
});

test("a single-object payload is normalised into an array", async () => {
  // limit=1 returns an object, limit>1 an array. Nothing downstream should care.
  const { subject } = adapter([{ status: 200, body: { status: 200, data: QUESTION } }]);
  const rows = await subject.draw({ subjectSlug: "chemistry", examType: "JAMB", examYear: 2022 }, 1);
  assert.equal(rows.length, 1);
});

test("404 yields an empty array, not an error", async () => {
  // The filter is genuinely empty; the ledger saturates it with rawCount 0.
  const { subject } = adapter([
    { status: 404, body: { status: 404, message: "No questions found for those filters." } },
  ]);
  const rows = await subject.draw({ subjectSlug: "biology", examType: "NECO", examYear: 2018 }, 50);
  assert.deepEqual(rows, []);
});

test("403 throws a terminal ProviderError", async () => {
  const { subject } = adapter([
    { status: 403, body: { status: 403, message: "Your API access is limited." } },
  ]);
  await assert.rejects(
    () => subject.draw({ subjectSlug: "chemistry", examType: "JAMB", examYear: 2022 }, 50),
    (err: unknown) => err instanceof ProviderError && err.kind === "terminal",
  );
});

test("401 throws a terminal ProviderError", async () => {
  const { subject } = adapter([{ status: 401, body: { status: 401, message: "Invalid AccessToken." } }]);
  await assert.rejects(
    () => subject.draw({ subjectSlug: "chemistry", examType: "JAMB", examYear: 2022 }, 50),
    (err: unknown) => err instanceof ProviderError && err.kind === "terminal",
  );
});

test("500 throws a retryable ProviderError", async () => {
  const { subject } = adapter([{ status: 500, body: { status: 500 } }]);
  await assert.rejects(
    () => subject.draw({ subjectSlug: "chemistry", examType: "JAMB", examYear: 2022 }, 50),
    (err: unknown) => err instanceof ProviderError && err.kind === "retryable",
  );
});

test("a subject the provider does not carry is refused without a call", async () => {
  const { subject, calls } = adapter([{ status: 200, body: { status: 200, data: [] } }]);
  await assert.rejects(
    () => subject.draw({ subjectSlug: "further-mathematics", examType: "JAMB", examYear: 2022 }, 50),
    (err: unknown) => err instanceof ProviderError && err.kind === "terminal",
  );
  assert.equal(calls.length, 0, "must not spend a request on a known-absent subject");
});

test("limit is clamped to their maximum", async () => {
  const { subject, calls } = adapter([{ status: 200, body: { status: 200, data: [QUESTION] } }]);
  await subject.draw({ subjectSlug: "chemistry", examType: "JAMB", examYear: 2022 }, 500);
  assert.equal(new URL(calls[0].url).searchParams.get("limit"), "50");
});

test("listSubjects and listYears unwrap the envelope", async () => {
  const { subject } = adapter([
    { status: 200, body: { status: 200, data: [{ id: 7, name: "Chemistry", slug: "chemistry" }] } },
  ]);
  assert.deepEqual(await subject.listSubjects(), [{ id: 7, name: "Chemistry", slug: "chemistry" }]);

  const years = adapter([{ status: 200, body: { status: 200, data: [2026, 2025] } }]);
  assert.deepEqual(await years.subject.listYears(), [2026, 2025]);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-sdash.mts`
Expected: FAIL — cannot find module.

- [ ] **Step 3: Write `src/lib/question-provider/sdash.ts`**

```ts
import { classifyStatus } from "./errors";
import { DRAW_LIMIT } from "./saturation";
import { toProviderExamSlug, toProviderSubjectSlug } from "./alias";
import {
  ProviderError,
  type ProviderFilter,
  type QuestionProviderAdapter,
} from "./types";

export type SdashConfig = {
  baseUrl: string;
  token: string;
  /** Test seam. */
  fetchImpl?: typeof fetch;
};

type Envelope = { status?: number; data?: unknown; message?: string };

export function createSdashAdapter(config: SdashConfig): QuestionProviderAdapter {
  const doFetch = config.fetchImpl ?? fetch;
  const root = config.baseUrl.replace(/\/$/, "");

  async function call(path: string, params: Record<string, string> = {}) {
    const url = new URL(root + path);
    for (const [key, value] of Object.entries(params)) {
      url.searchParams.set(key, value);
    }

    let res: Response;
    try {
      res = await doFetch(url, { headers: { AccessToken: config.token } });
    } catch (error) {
      throw new ProviderError(`Provider unreachable: ${String(error)}`, "retryable");
    }

    const kind = classifyStatus(res.status);
    if (kind === "empty") return null;

    if (kind !== "ok") {
      const body = (await res.json().catch(() => null)) as Envelope | null;
      throw new ProviderError(
        body?.message ?? `Provider returned ${res.status}`,
        kind,
        res.status,
      );
    }

    const body = (await res.json().catch(() => null)) as Envelope | null;
    if (!body || body.data === undefined) {
      throw new ProviderError("Provider returned an unreadable body", "retryable", res.status);
    }
    return body.data;
  }

  return {
    name: "SDASH",

    async draw(filter: ProviderFilter, limit: number): Promise<unknown[]> {
      const subject = toProviderSubjectSlug(filter.subjectSlug);
      const type = toProviderExamSlug(filter.examType);

      // Refuse before spending a request. A subject they do not carry, or an
      // exam we are not entitled to, can never succeed.
      if (!subject) {
        throw new ProviderError(
          `The provider does not carry "${filter.subjectSlug}".`,
          "terminal",
        );
      }
      if (!type) {
        throw new ProviderError(`Exam type "${filter.examType}" is not requestable.`, "terminal");
      }

      const data = await call("/v1/q", {
        subject,
        type,
        year: String(filter.examYear),
        limit: String(Math.min(Math.max(1, limit), DRAW_LIMIT)),
      });

      if (data === null) return []; // 404 — nothing here
      // limit=1 gives an object, limit>1 an array. Normalise it away.
      return Array.isArray(data) ? data : [data];
    },

    async listSubjects() {
      const data = await call("/v1/subjects");
      return Array.isArray(data) ? (data as { id: number; name: string; slug: string }[]) : [];
    },

    async listYears() {
      const data = await call("/v1/years");
      return Array.isArray(data) ? (data as number[]) : [];
    },
  };
}

/** The configured adapter, from env. Throws when the token is missing. */
export function getSdashAdapter(): QuestionProviderAdapter {
  const token = process.env.SDASH_ACCESS_TOKEN;
  if (!token) throw new ProviderError("SDASH_ACCESS_TOKEN is not set", "terminal");
  return createSdashAdapter({
    baseUrl: process.env.SDASH_BASE_URL ?? "https://sdashapi.com/api",
    token,
  });
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --import tsx --test --test-force-exit scripts/test-provider-sdash.mts`
Expected: PASS, 10 tests.

- [ ] **Step 5: Add the env placeholders**

Append to `.env.example`:

```
# sdashapi past-question provider (see docs/superpowers/specs/2026-09-01-question-provider-cache-design.md)
SDASH_BASE_URL=https://sdashapi.com/api
SDASH_ACCESS_TOKEN=your-sdash-access-token
QUESTION_PROVIDER_ENABLED=false
```

Add the same three keys with real values to your local `.env` (gitignored).

- [ ] **Step 6: Register the suite and commit**

```bash
npm run typecheck:tests && npm test
git add src/lib/question-provider/sdash.ts scripts/test-provider-sdash.mts package.json .env.example
git commit -m "feat(provider): add the sdashapi adapter"
```

---

### Task 8: Image mirroring

**Files:**
- Modify: `src/lib/cloudinary.ts`

**Interfaces:**
- Consumes: nothing new.
- Produces: `uploadRemoteImage(sourceUrl: string, publicId: string): Promise<string>` — returns our Cloudinary secure URL.

Cloudinary accepts a remote URL as the `file` parameter and fetches it server-side, so we never proxy the bytes ourselves.

- [ ] **Step 1: Add `uploadRemoteImage` to `src/lib/cloudinary.ts`**

Append (it reuses the module-private `credentials()` and `UploadRejectedError`):

```ts
/**
 * Copy a remote image into our own Cloudinary account and return our URL.
 *
 * Used when importing provider questions: their `image` field points at a
 * third party's Cloudinary (`res.cloudinary.com/aloc-ng/...`) which we neither
 * control nor pay for. Storing their URL would leave every imported diagram
 * one deletion away from breaking. Cloudinary fetches `file` server-side when
 * it is a URL, so the bytes never pass through us.
 */
export async function uploadRemoteImage(
  sourceUrl: string,
  publicId: string,
): Promise<string> {
  const creds = credentials();
  if (!creds) throw new Error("Image uploads aren't configured");

  const timestamp = Math.floor(Date.now() / 1000);
  const fullPublicId = `scholarscrib/questions/${publicId}`;

  const toSign = `overwrite=true&public_id=${fullPublicId}&timestamp=${timestamp}`;
  const signature = crypto
    .createHash("sha1")
    .update(toSign + creds.apiSecret)
    .digest("hex");

  const body = new FormData();
  body.append("file", sourceUrl);
  body.append("api_key", creds.apiKey);
  body.append("timestamp", String(timestamp));
  body.append("public_id", fullPublicId);
  body.append("overwrite", "true");
  body.append("signature", signature);

  const res = await fetch(
    `https://api.cloudinary.com/v1_1/${creds.cloudName}/image/upload`,
    { method: "POST", body },
  );

  if (!res.ok) {
    const detail = (await res.json().catch(() => null)) as {
      error?: { message?: string };
    } | null;
    throw new UploadRejectedError(
      detail?.error?.message ?? "Cloudinary rejected the remote image",
      res.status < 500,
    );
  }

  const json = (await res.json()) as { secure_url?: string };
  if (!json.secure_url) throw new Error("Cloudinary returned no image URL");
  return json.secure_url;
}
```

- [ ] **Step 2: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/lib/cloudinary.ts
git commit -m "feat(provider): mirror remote question images into our Cloudinary"
```

---

### Task 9: Ingestion orchestrator

**Files:**
- Create: `src/lib/question-provider/ingest.ts`

**Interfaces:**
- Consumes: everything from Tasks 2–8, plus `db` from `src/lib/db`.
- Produces:
  - `ensureQuestionsCached(filter: ProviderFilter, limit: number): Promise<{ questions: Question[]; source: "db" | "provider"; ledger: { status: ProviderFetchStatus; rawCount: number; promotedCount: number } }>`
  - `saturate(filter: ProviderFilter): Promise<void>`

- [ ] **Step 1: Write `src/lib/question-provider/ingest.ts`**

```ts
import type { Question } from "@prisma/client";
import { db } from "@/lib/db";
import { uploadRemoteImage } from "@/lib/cloudinary";
import { cacheKey } from "./cache-key";
import { mapProviderQuestion, MAPPER_VERSION } from "./mapper";
import { DRAW_LIMIT, MAX_DRAWS, isSaturated } from "./saturation";
import { getSdashAdapter } from "./sdash";
import { ProviderError, type ProviderFilter } from "./types";

const PROVIDER = "SDASH" as const;

/** Reads from our own bank, calling the provider only the first time we see a filter. */
export async function ensureQuestionsCached(
  filter: ProviderFilter,
  limit: number,
) {
  const key = cacheKey(filter);
  const subject = await db.subject.findUnique({
    where: { slug: filter.subjectSlug },
    select: { id: true },
  });
  if (!subject) {
    return {
      questions: [] as Question[],
      source: "db" as const,
      ledger: { status: "FAILED" as const, rawCount: 0, promotedCount: 0 },
    };
  }

  const existing = await db.providerFetch.findUnique({
    where: { provider_cacheKey: { provider: PROVIDER, cacheKey: key } },
  });

  // Already saturated: our copy is authoritative, and this branch is dead for
  // this filter from now on.
  if (existing?.status === "SATURATED") {
    return {
      questions: await readFromDb(subject.id, filter, limit),
      source: "db" as const,
      ledger: {
        status: existing.status,
        rawCount: existing.rawCount,
        promotedCount: existing.promotedCount,
      },
    };
  }

  // Claim the fetch. The unique constraint is the in-flight lock: a concurrent
  // second request loses the race here and reads what the winner wrote.
  let ledger = existing;
  if (!ledger) {
    try {
      ledger = await db.providerFetch.create({
        data: {
          provider: PROVIDER,
          cacheKey: key,
          subjectId: subject.id,
          examType: filter.examType,
          examYear: filter.examYear,
        },
      });
    } catch {
      // Lost the race — fall back to serving from the database.
      return {
        questions: await readFromDb(subject.id, filter, limit),
        source: "db" as const,
        ledger: { status: "PENDING" as const, rawCount: 0, promotedCount: 0 },
      };
    }
  }

  await drawOnce(ledger.id, subject.id, filter);

  const after = await db.providerFetch.findUnique({ where: { id: ledger.id } });
  return {
    questions: await readFromDb(subject.id, filter, limit),
    source: "provider" as const,
    ledger: {
      status: after?.status ?? "PENDING",
      rawCount: after?.rawCount ?? 0,
      promotedCount: after?.promotedCount ?? 0,
    },
  };
}

/** The remaining draws, run off the response path via `after()`. */
export async function saturate(filter: ProviderFilter): Promise<void> {
  const key = cacheKey(filter);
  const subject = await db.subject.findUnique({
    where: { slug: filter.subjectSlug },
    select: { id: true },
  });
  if (!subject) return;

  for (let i = 0; i < MAX_DRAWS; i++) {
    const ledger = await db.providerFetch.findUnique({
      where: { provider_cacheKey: { provider: PROVIDER, cacheKey: key } },
    });
    if (!ledger || ledger.status !== "PENDING") return;
    await drawOnce(ledger.id, subject.id, filter);
  }
}

/** One draw: fetch, stage, promote, then update the ledger. */
async function drawOnce(fetchId: string, subjectId: string, filter: ProviderFilter) {
  const adapter = getSdashAdapter();

  let payloads: unknown[];
  try {
    payloads = await adapter.draw(filter, DRAW_LIMIT);
  } catch (error) {
    const kind = error instanceof ProviderError ? error.kind : "retryable";
    await db.providerFetch.update({
      where: { id: fetchId },
      data: {
        // Terminal failures are final; retryable ones stay PENDING so a later
        // request can try again.
        status: kind === "terminal" ? "FAILED" : "PENDING",
        error: error instanceof Error ? error.message : String(error),
        completedAt: kind === "terminal" ? new Date() : null,
      },
    });
    return;
  }

  let newCount = 0;
  let promoted = 0;
  let rejected = 0;

  for (const payload of payloads) {
    const result = mapProviderQuestion(payload);

    // Dedupe on their id first, then on the content fingerprint.
    const seen = await db.providerQuestion.findFirst({
      where: {
        provider: PROVIDER,
        OR: [
          ...(result.providerQuestionId
            ? [{ providerQuestionId: result.providerQuestionId }]
            : []),
          { fingerprint: result.fingerprint },
        ],
      },
      select: { id: true },
    });
    if (seen) continue;

    newCount += 1;

    if (!result.ok) {
      await db.providerQuestion.create({
        data: {
          fetchId,
          provider: PROVIDER,
          providerQuestionId: result.providerQuestionId,
          fingerprint: result.fingerprint,
          payload: payload as object,
          status: "REJECTED",
          rejectionReasons: result.reasons,
          mapperVersion: MAPPER_VERSION,
        },
      });
      rejected += 1;
      continue;
    }

    // Mirror the image before promoting. A question pointing at a third
    // party's asset is not one we own, so a mirror failure rejects the row
    // rather than promoting a broken dependency; the raw payload is kept and
    // retried on the next MAPPER_VERSION sweep.
    let imageUrl: string | null = null;
    if (result.question.providerImageUrl) {
      try {
        imageUrl = await uploadRemoteImage(
          result.question.providerImageUrl,
          `${PROVIDER.toLowerCase()}-${result.providerQuestionId ?? result.fingerprint.slice(0, 16)}`,
        );
      } catch (error) {
        await db.providerQuestion.create({
          data: {
            fetchId,
            provider: PROVIDER,
            providerQuestionId: result.providerQuestionId,
            fingerprint: result.fingerprint,
            payload: payload as object,
            status: "REJECTED",
            rejectionReasons: [
              {
                field: "questionImageUrl",
                message: `Could not mirror the image: ${error instanceof Error ? error.message : String(error)}`,
              },
            ],
            mapperVersion: MAPPER_VERSION,
          },
        });
        rejected += 1;
        continue;
      }
    }

    await db.$transaction(async (tx) => {
      const question = await tx.question.create({
        data: {
          subjectId,
          examType: result.question.examType,
          examYear: result.question.examYear,
          questionText: result.question.questionText,
          questionImageUrl: imageUrl,
          questionType: "OBJECTIVE",
          options: result.question.options,
          correctAnswer: result.question.correctAnswer,
          explanation: result.question.explanation,
        },
      });
      await tx.providerQuestion.create({
        data: {
          fetchId,
          provider: PROVIDER,
          providerQuestionId: result.providerQuestionId,
          fingerprint: result.fingerprint,
          payload: payload as object,
          status: "PROMOTED",
          mapperVersion: MAPPER_VERSION,
          questionId: question.id,
          promotedAt: new Date(),
        },
      });
    });
    promoted += 1;
  }

  const current = await db.providerFetch.findUnique({ where: { id: fetchId } });
  const drawCount = (current?.drawCount ?? 0) + 1;
  const saturated = isSaturated({
    drawCount,
    returnedCount: payloads.length,
    newInLastDraw: newCount,
  });

  await db.providerFetch.update({
    where: { id: fetchId },
    data: {
      drawCount,
      newInLastDraw: newCount,
      rawCount: { increment: newCount },
      promotedCount: { increment: promoted },
      rejectedCount: { increment: rejected },
      status: saturated ? "SATURATED" : "PENDING",
      completedAt: saturated ? new Date() : null,
    },
  });
}

async function readFromDb(
  subjectId: string,
  filter: ProviderFilter,
  limit: number,
): Promise<Question[]> {
  return db.question.findMany({
    where: {
      subjectId,
      examType: filter.examType,
      examYear: filter.examYear,
      questionType: "OBJECTIVE",
    },
    take: limit,
  });
}
```

- [ ] **Step 2: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/lib/question-provider/ingest.ts
git commit -m "feat(provider): add the ingestion orchestrator"
```

---

### Task 10: Catalogue sweep script

**Files:**
- Create: `scripts/sync-provider-catalogue.ts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `getSdashAdapter`, `toProviderSubjectSlug`, `PROVIDER_EXAM_SLUGS`, `toExamType`.
- Produces: populated `ProviderCatalogue` rows; an npm script `sync-provider-catalogue`.

`/v1/years` is a **global** list, not per subject or type, so a cross product would advertise thousands of empty papers. The sweep records only combinations that actually answer `200`.

- [ ] **Step 1: Write `scripts/sync-provider-catalogue.ts`**

```ts
/**
 * One-off sweep populating ProviderCatalogue.
 *
 * Run: npm run sync-provider-catalogue
 *
 * ~24 mapped subjects x 26 years x 3 exam types is roughly 1,800 requests at
 * one per 400ms — about 20 minutes. Safe to re-run; it is idempotent, and
 * worth repeating annually as new years appear.
 */
import { db } from "../src/lib/db";
import { getSdashAdapter } from "../src/lib/question-provider/sdash";
import {
  PROVIDER_EXAM_SLUGS,
  toExamType,
  toProviderSubjectSlug,
} from "../src/lib/question-provider/alias";
import { ProviderError } from "../src/lib/question-provider/types";

const SPACING_MS = 400;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const adapter = getSdashAdapter();

  // Fail loudly if a slug we map has vanished from their catalogue — silently
  // skipping a subject would look identical to that subject having no papers.
  const theirSubjects = new Set((await adapter.listSubjects()).map((s) => s.slug));
  const years = await adapter.listYears();
  const ourSubjects = await db.subject.findMany({ select: { id: true, slug: true, name: true } });

  const mapped = ourSubjects.flatMap((subject) => {
    const providerSlug = toProviderSubjectSlug(subject.slug);
    if (!providerSlug) return [];
    if (!theirSubjects.has(providerSlug)) {
      throw new Error(
        `Alias maps "${subject.slug}" to "${providerSlug}", which is no longer in /v1/subjects. Update alias.ts.`,
      );
    }
    return [{ ...subject, providerSlug }];
  });

  console.log(
    `Sweeping ${mapped.length} subjects x ${years.length} years x ${PROVIDER_EXAM_SLUGS.length} exams ` +
      `= ${mapped.length * years.length * PROVIDER_EXAM_SLUGS.length} requests`,
  );

  let found = 0;
  let empty = 0;

  for (const subject of mapped) {
    for (const examSlug of PROVIDER_EXAM_SLUGS) {
      const examType = toExamType(examSlug)!;
      for (const year of years) {
        try {
          const rows = await adapter.draw(
            { subjectSlug: subject.slug, examType, examYear: year },
            1,
          );
          if (rows.length > 0) {
            await db.providerCatalogue.upsert({
              where: {
                provider_subjectId_examType_examYear: {
                  provider: "SDASH",
                  subjectId: subject.id,
                  examType,
                  examYear: year,
                },
              },
              create: { provider: "SDASH", subjectId: subject.id, examType, examYear: year },
              update: {},
            });
            found += 1;
          } else {
            empty += 1; // a 404 — definitively nothing here, not a failure
          }
        } catch (error) {
          if (error instanceof ProviderError && error.kind === "terminal") {
            console.error(`Terminal error, stopping: ${error.message}`);
            process.exit(1);
          }
          console.warn(`Retryable error on ${subject.slug}/${examSlug}/${year}, skipping`);
        }
        await sleep(SPACING_MS);
      }
    }
    console.log(`  ${subject.name}: ${found} found so far`);
  }

  console.log(`\nDone. ${found} papers recorded, ${empty} combinations empty.`);
}

main()
  .catch((error) => {
    console.error(error);
    process.exit(1);
  })
  .finally(() => db.$disconnect());
```

- [ ] **Step 2: Add the npm script**

In `package.json`, next to `import-questions`:

```json
"sync-provider-catalogue": "npx tsx scripts/sync-provider-catalogue.ts"
```

- [ ] **Step 3: Verify the alias check fires before any sweeping**

Run: `npm run sync-provider-catalogue`
Expected: it prints the request-count line within a few seconds. If it throws an alias error instead, `alias.ts` is out of date with their live catalogue — fix that first.

Let it run to completion (~20 minutes), then confirm in the SQL editor:

```sql
SELECT "examType", COUNT(*) FROM "ProviderCatalogue" GROUP BY "examType";
```

Expected: rows for JAMB and WAEC, and a smaller NECO count (their NECO coverage is sparse and recent).

- [ ] **Step 4: Commit**

```bash
git add scripts/sync-provider-catalogue.ts package.json
git commit -m "feat(provider): sweep the provider catalogue"
```

---

### Task 11: Admin backfill endpoint

**Files:**
- Create: `src/app/admin/api/provider/backfill/route.ts`
- Modify: `src/lib/validators.ts`, `src/lib/admin-audit.ts`

**Interfaces:**
- Consumes: `ensureQuestionsCached`, `saturate` from `ingest.ts`; `requireAdminApi` from `src/lib/admin-session`; `recordAudit`.
- Produces: `POST /admin/api/provider/backfill`, and the audit action `"provider.backfill"`.

This is the only route that can reach the provider until Task 14. It exists so imported-question quality can be reviewed on real data before any student sees one.

- [ ] **Step 1: Add the validator**

In `src/lib/validators.ts`:

```ts
export const providerBackfillSchema = z.object({
  subjectSlug: z.string().min(1),
  examType: z.enum(["WAEC", "JAMB", "NECO"]),
  examYear: z.number().int().min(2001).max(2026),
});
export type ProviderBackfillInput = z.infer<typeof providerBackfillSchema>;
```

- [ ] **Step 2: Add the audit action**

In `src/lib/admin-audit.ts`, add to the `AuditAction` union:

```ts
  | "provider.backfill"
```

- [ ] **Step 3: Write the route**

`src/app/admin/api/provider/backfill/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { revalidateTag } from "next/cache";
import { requireAdminApi } from "@/lib/admin-session";
import { recordAudit } from "@/lib/admin-audit";
import { CATALOGUE_TAG } from "@/lib/catalogue";
import { providerBackfillSchema } from "@/lib/validators";
import { ensureQuestionsCached, saturate } from "@/lib/question-provider/ingest";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

// POST /admin/api/provider/backfill — saturate one paper from the provider.
export async function POST(req: NextRequest) {
  try {
    const guard = await requireAdminApi();
    if (!guard.ok) return guard.response;

    const parsed = providerBackfillSchema.safeParse(await req.json());
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Validation failed", details: parsed.error.flatten() },
        { status: 400 },
      );
    }

    const filter = parsed.data;
    await ensureQuestionsCached(filter, 50);
    // Run the remaining draws inline: this is an admin tool, not a student
    // request, so completeness matters more than latency here.
    await saturate(filter);

    const result = await ensureQuestionsCached(filter, 0);
    revalidateTag(CATALOGUE_TAG, "max");

    await recordAudit({
      actorId: guard.actor.id,
      action: "provider.backfill",
      entity: "ProviderFetch",
      summary:
        `Backfilled ${filter.subjectSlug} ${filter.examType} ${filter.examYear}: ` +
        `${result.ledger.rawCount} captured, ${result.ledger.promotedCount} promoted ` +
        `(${result.ledger.status}).`,
    });

    return NextResponse.json({ ledger: result.ledger });
  } catch (error) {
    console.error("Provider backfill failed:", error);
    return NextResponse.json({ error: "Backfill failed" }, { status: 500 });
  }
}
```

- [ ] **Step 4: Typecheck**

Run: `npx tsc --noEmit -p tsconfig.json`
Expected: no errors.

- [ ] **Step 5: Backfill two papers and review them**

Start the dev server, log into the admin console, then:

```bash
curl -X POST http://localhost:3000/admin/api/provider/backfill \
  -H 'Content-Type: application/json' \
  -b 'scholarscrib.admin-session=<your session cookie>' \
  -d '{"subjectSlug":"chemistry","examType":"JAMB","examYear":2022}'
```

Expected: a `ledger` with `status: "SATURATED"` and a `promotedCount` around 80% of `rawCount`.

Then check the promoted questions in the SQL editor:

```sql
SELECT q."questionText", q."correctAnswer", q."options", q."questionImageUrl"
FROM "Question" q
JOIN "ProviderQuestion" pq ON pq."questionId" = q.id
LIMIT 10;

SELECT pq."rejectionReasons", COUNT(*)
FROM "ProviderQuestion" pq WHERE pq.status = 'REJECTED'
GROUP BY pq."rejectionReasons";
```

Verify by eye that `correctAnswer` is always one of the `options` keys, and that every `questionImageUrl` points at **our** Cloudinary cloud name, never `aloc-ng`.

- [ ] **Step 6: Commit**

```bash
git add src/app/admin/api/provider/backfill/route.ts src/lib/validators.ts src/lib/admin-audit.ts
git commit -m "feat(provider): add admin backfill endpoint"
```

---

### Task 12: Thread examYear through quiz generation

**Files:**
- Modify: `src/lib/validators.ts`, `src/lib/question-pool.ts`, `src/lib/assessment-generation.ts`, `src/lib/attempt-lifecycle.ts`, `src/components/assessment/quiz-engine.tsx`, `src/app/(dashboard)/practice/past-questions/[subjectSlug]/page.tsx`

**Interfaces:**
- Consumes: nothing from the provider modules.
- Produces: `GenerateQuizInput.examYear?: number`, honoured by `generateQuiz`; `QuestionPoolFilter.examYear?: number`.

This is a pre-existing bug. The picker's link **already** carries the year (`past-question-picker.tsx:224` builds `?exam=...&year=...`), but `[subjectSlug]/page.tsx:12` reads only `exam` and drops it, and `generateQuizSchema` has no `examYear` field to receive it anyway. So "2022 JAMB Chemistry" currently generates from every JAMB Chemistry year in the bank. The cache key is (subject, examType, year), so this must be fixed before Task 14.

**No change is needed to `past-question-picker.tsx` in this task** — its link is already correct.

- [ ] **Step 1: Add `examYear` to the schema**

In `src/lib/validators.ts`, inside `generateQuizSchema`'s object (next to `examType`):

```ts
    examYear: z.number().int().min(2001).max(2100).optional(),
```

- [ ] **Step 2: Teach the question pool to filter by year**

In `src/lib/question-pool.ts`, add to `QuestionPoolFilter` (next to `examType`):

```ts
  /** Restrict to one past paper's year. */
  examYear?: number;
```

and add a condition in `buildConditions`, immediately after the `examType` block:

```ts
  if (filter.examYear !== undefined) {
    conditions.push(Prisma.sql`q."examYear" = ${filter.examYear}`);
  }
```

- [ ] **Step 3: Honour it in `generateQuiz`**

In `src/lib/assessment-generation.ts`, add `examYear` to the destructuring at line 57:

```ts
  const {
    subjectSlug,
    subjectId,
    topicIds: explicitTopicIds,
    topicSlug,
    examType,
    examYear,
    count,
    difficulty,
    title,
    untimed,
  } = input;
```

Then include `examYear` in every `QuestionPoolFilter` this function builds, alongside `examType`.

Also pass it to `findResumableAttempt` in `src/lib/attempt-lifecycle.ts` and add it to that function's matching criteria — without it, picking 2022 would resume an unfinished 2019 paper of the same subject and length, because every other field matches.

- [ ] **Step 4: Read it in the quiz page**

In `src/app/(dashboard)/practice/past-questions/[subjectSlug]/page.tsx`:

```tsx
  const examType = searchParams.get("exam") || undefined;
  const yearParam = searchParams.get("year");
  const examYear = yearParam ? Number(yearParam) : undefined;

  return (
    <QuizEngine
      subjectSlug={subjectSlug}
      examType={examType}
      examYear={examYear}
      count={40}
      backHref="/practice/past-questions"
    />
  );
```

- [ ] **Step 5: Pass it through `QuizEngine`**

Add an optional `examYear?: number` prop to `src/components/assessment/quiz-engine.tsx` and include it in the body posted to `/api/assessments/generate`.

- [ ] **Step 6: Verify by hand**

Start the dev server, pick a specific exam/subject/year in the picker, and confirm in the dev-server query log that the generated quiz's SQL includes `q."examYear" = <the year you picked>`.

Run: `npx tsc --noEmit -p tsconfig.json && npm run lint`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/lib/validators.ts src/lib/question-pool.ts src/lib/assessment-generation.ts src/lib/attempt-lifecycle.ts src/components/assessment/quiz-engine.tsx "src/app/(dashboard)/practice/past-questions/[subjectSlug]/page.tsx"
git commit -m "fix(practice): honour the chosen exam year when generating a past paper"
```

---

### Task 13: Offer uncached papers in the picker

**Files:**
- Modify: `src/lib/questions.ts`, `src/components/practice/past-question-picker.tsx`

**Interfaces:**
- Consumes: `ProviderCatalogue` rows.
- Produces: `PastPaper` gains `cached: boolean` and `questionCount: number | null`.

Without this the feature is unreachable: the picker is built from `listPastPapers`, which groups over `Question`, so a paper we have never fetched cannot be selected and therefore is never fetched.

- [ ] **Step 1: Union the catalogue into `listPastPapers`**

In `src/lib/questions.ts`, change the `PastPaper` type:

```ts
export type PastPaper = {
  examType: string;
  examYear: number | null;
  subjectId: string;
  subjectName: string;
  subjectSlug: string;
  trackCategory: string;
  /** Null when we have not fetched this paper yet. */
  questionCount: number | null;
  cached: boolean;
};
```

Then, after building the grouped rows, merge in catalogue entries we do not already hold:

```ts
  const catalogue = await db.providerCatalogue.findMany({
    where: {
      ...(filter.examType ? { examType: filter.examType as ExamType } : {}),
      ...(filter.subjectId ? { subjectId: filter.subjectId } : {}),
    },
    select: { subjectId: true, examType: true, examYear: true },
  });

  const held = new Set(papers.map((p) => `${p.subjectId}|${p.examType}|${p.examYear}`));
  const extra = catalogue.filter(
    (row) => !held.has(`${row.subjectId}|${row.examType}|${row.examYear}`),
  );
```

The existing `subjectById` map must be built from the union of both id sets, not just the grouped papers, or catalogue-only subjects render as "Unknown":

```ts
  const subjectIds = [
    ...new Set([
      ...papers.map((p) => p.subjectId),
      ...extra.map((row) => row.subjectId),
    ]),
  ];
  const subjects = await db.subject.findMany({
    where: { id: { in: subjectIds } },
    select: { id: true, name: true, slug: true, trackCategory: true },
  });
  const subjectById = new Map(subjects.map((s) => [s.id, s]));

  const held: PastPaper[] = papers.map((p) => {
    const subject = subjectById.get(p.subjectId);
    return {
      examType: p.examType,
      examYear: p.examYear,
      subjectId: p.subjectId,
      subjectName: subject?.name ?? "Unknown",
      subjectSlug: subject?.slug ?? "",
      trackCategory: subject?.trackCategory ?? "CORE",
      questionCount: p._count.id,
      cached: true,
    };
  });

  const uncached: PastPaper[] = extra.map((row) => {
    const subject = subjectById.get(row.subjectId);
    return {
      examType: row.examType,
      examYear: row.examYear,
      subjectId: row.subjectId,
      subjectName: subject?.name ?? "Unknown",
      subjectSlug: subject?.slug ?? "",
      trackCategory: subject?.trackCategory ?? "CORE",
      questionCount: null,
      cached: false,
    };
  });

  return [...held, ...uncached];
```

- [ ] **Step 2: Render uncached papers in the picker**

In `src/components/practice/past-question-picker.tsx`, update the local `PastPaper` type:

```ts
type PastPaper = {
  examType: string;
  examYear: number;
  subjectId: string;
  subjectName: string;
  subjectSlug: string;
  trackCategory: string;
  questionCount: number | null;
  cached: boolean;
};
```

The exam and subject aggregation steps sum `questionCount`, so treat `null` as `0` there — an uncached paper contributes nothing to the count but still counts toward the `years` tally. In the `exams` memo:

```ts
      counts.set(p.examType, (counts.get(p.examType) ?? 0) + (p.questionCount ?? 0));
```

and in the `subjects` memo:

```ts
      if (found) {
        found.questionCount = (found.questionCount ?? 0) + (p.questionCount ?? 0);
        found.years += 1;
      }
```

Then in the year step (line 229), show the count only when the paper is cached:

```tsx
                  <p className="mt-0.5 text-xs text-muted">
                    {paper.cached ? `${paper.questionCount} questions` : "Not loaded yet"}
                  </p>
```

- [ ] **Step 3: Verify**

Run: `npx tsc --noEmit -p tsconfig.json && npm run lint`
Expected: clean.

Start the dev server and open `/practice/past-questions`. Expected: many more years are offered than before, with counts on the ones already backfilled in Task 11 and "Not loaded yet" on the rest.

- [ ] **Step 4: Commit**

```bash
git add src/lib/questions.ts src/components/practice/past-question-picker.tsx
git commit -m "feat(practice): offer past papers we have not fetched yet"
```

---

### Task 14: Wire the cache into quiz generation

**Files:**
- Modify: `src/lib/assessment-generation.ts`, `src/app/api/assessments/generate/route.ts`

**Interfaces:**
- Consumes: `ensureQuestionsCached`, `saturate` from `ingest.ts`; `after` from `next/server`.
- Produces: student-facing misses that self-serve.

- [ ] **Step 1: Call the cache in `generateQuiz`**

In `src/lib/assessment-generation.ts`, after the subject resolves (line 76) and before `reapStaleAttempts`:

```ts
  // Fetch from the provider the first time we ever see this paper. Gated on
  // all three of subject/type/year, so topic quizzes, mock exams and JAMB CBT
  // never reach it — they read the bank this fills.
  if (
    process.env.QUESTION_PROVIDER_ENABLED === "true" &&
    examType &&
    examYear &&
    (examType === "WAEC" || examType === "JAMB" || examType === "NECO")
  ) {
    const filter = { subjectSlug: subject.slug, examType, examYear };
    const ledger = await db.providerFetch.findUnique({
      where: {
        provider_cacheKey: { provider: "SDASH", cacheKey: cacheKey(filter) },
      },
      select: { status: true },
    });

    if (ledger?.status !== "SATURATED" && ledger?.status !== "FAILED") {
      // The user waits for exactly one call; the rest of the paper warms up
      // after the response has gone out.
      await ensureQuestionsCached(filter, count);
      after(() => saturate(filter));
    }
  }
```

`generateQuiz` currently selects only `{ id: true, name: true }` for the subject — add `slug: true` to that `select` at line 74.

Import at the top of the file:

```ts
import { after } from "next/server";
import { cacheKey } from "@/lib/question-provider/cache-key";
import { ensureQuestionsCached, saturate } from "@/lib/question-provider/ingest";
```

- [ ] **Step 2: Cap outbound calls**

In `src/app/api/assessments/generate/route.ts`, the existing `rateLimit` on `generate:${session.user.id}` (20/60s) already bounds a single student. Add a global cap so a burst of misses cannot hammer the provider — before calling `generateQuiz`:

```ts
    const outbound = rateLimit({
      key: "provider:outbound",
      limit: 30,
      windowSeconds: 60,
    });
    if (!outbound.ok) {
      // Serve from whatever we already hold rather than failing the student.
      process.env.QUESTION_PROVIDER_ENABLED = "false";
    }
```

If mutating the env var reads badly, pass an explicit `allowProviderFetch` flag into `generateQuiz` instead — the behaviour that matters is that exhausting the cap degrades to a database-only quiz rather than an error.

- [ ] **Step 3: Verify end to end with the flag off**

Set `QUESTION_PROVIDER_ENABLED=false`, restart, and start a past-paper quiz on a subject/year already backfilled in Task 11.
Expected: the quiz starts, and no `ProviderFetch` row is created for a new filter.

- [ ] **Step 4: Verify end to end with the flag on**

Set `QUESTION_PROVIDER_ENABLED=true`, restart, and pick a year showing "Not loaded yet".
Expected: the quiz starts after roughly a second; a `ProviderFetch` row appears; and the ledger's `drawCount` continues climbing for a few seconds afterwards as `after()` saturates it.

Then repeat the same pick. Expected: instant, and `drawCount` does not increase — the filter is `SATURATED` and we never call out again.

- [ ] **Step 5: Full check**

Run: `npm run typecheck:tests && npm test && npx tsc --noEmit -p tsconfig.json && npm run lint`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/lib/assessment-generation.ts src/app/api/assessments/generate/route.ts
git commit -m "feat(provider): serve past-paper misses from the provider once"
```

---

### Task 15: Re-promotion sweep

**Files:**
- Create: `scripts/repromote-provider-questions.ts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `mapProviderQuestion`, `MAPPER_VERSION` from `mapper.ts`; `uploadRemoteImage`.
- Produces: an npm script `repromote-provider-questions`.

This is the mechanism the whole staging design exists for: improving the mapper must promote previously-rejected questions **with no new API calls**. Without it, decision 2's central claim is unimplemented and the staging table is a dead-letter box.

- [ ] **Step 1: Write `scripts/repromote-provider-questions.ts`**

```ts
/**
 * Re-run the current mapper over rejected rows and promote whatever now passes.
 *
 * Run: npm run repromote-provider-questions
 *
 * Makes no network calls to the provider — it reads the payloads we already
 * captured. Run it after bumping MAPPER_VERSION, or after writing explanations
 * for the cohort rejected for a missing solution.
 */
import { db } from "../src/lib/db";
import { uploadRemoteImage } from "../src/lib/cloudinary";
import { mapProviderQuestion, MAPPER_VERSION } from "../src/lib/question-provider/mapper";

async function main() {
  const stale = await db.providerQuestion.findMany({
    where: { status: "REJECTED", mapperVersion: { lt: MAPPER_VERSION } },
    include: { fetch: { select: { subjectId: true } } },
  });

  console.log(`${stale.length} rejected rows below mapper version ${MAPPER_VERSION}`);

  let promoted = 0;
  let stillRejected = 0;

  for (const row of stale) {
    const result = mapProviderQuestion(row.payload);
    const subjectId = row.fetch.subjectId;

    if (!result.ok || !subjectId) {
      await db.providerQuestion.update({
        where: { id: row.id },
        data: {
          mapperVersion: MAPPER_VERSION,
          rejectionReasons: result.ok
            ? [{ field: "subjectId", message: "The fetch has no subject." }]
            : result.reasons,
        },
      });
      stillRejected += 1;
      continue;
    }

    let imageUrl: string | null = null;
    if (result.question.providerImageUrl) {
      try {
        imageUrl = await uploadRemoteImage(
          result.question.providerImageUrl,
          `sdash-${result.providerQuestionId ?? result.fingerprint.slice(0, 16)}`,
        );
      } catch (error) {
        await db.providerQuestion.update({
          where: { id: row.id },
          data: {
            mapperVersion: MAPPER_VERSION,
            rejectionReasons: [
              {
                field: "questionImageUrl",
                message: `Could not mirror the image: ${error instanceof Error ? error.message : String(error)}`,
              },
            ],
          },
        });
        stillRejected += 1;
        continue;
      }
    }

    await db.$transaction(async (tx) => {
      const question = await tx.question.create({
        data: {
          subjectId,
          examType: result.question.examType,
          examYear: result.question.examYear,
          questionText: result.question.questionText,
          questionImageUrl: imageUrl,
          questionType: "OBJECTIVE",
          options: result.question.options,
          correctAnswer: result.question.correctAnswer,
          explanation: result.question.explanation,
        },
      });
      await tx.providerQuestion.update({
        where: { id: row.id },
        data: {
          status: "PROMOTED",
          rejectionReasons: undefined,
          mapperVersion: MAPPER_VERSION,
          questionId: question.id,
          promotedAt: new Date(),
        },
      });
      await tx.providerFetch.update({
        where: { id: row.fetchId },
        data: {
          promotedCount: { increment: 1 },
          rejectedCount: { decrement: 1 },
        },
      });
    });
    promoted += 1;
  }

  console.log(`Promoted ${promoted}, still rejected ${stillRejected}.`);
}

main()
  .catch((error) => {
    console.error(error);
    process.exit(1);
  })
  .finally(() => db.$disconnect());
```

- [ ] **Step 2: Add the npm script**

In `package.json`, next to `sync-provider-catalogue`:

```json
"repromote-provider-questions": "npx tsx scripts/repromote-provider-questions.ts"
```

- [ ] **Step 3: Verify it is a no-op at the current version**

Run: `npm run repromote-provider-questions`
Expected: `0 rejected rows below mapper version 1` — nothing to do, because nothing has been rejected under an older mapper yet. That is the correct outcome and proves the query is right.

- [ ] **Step 4: Prove it actually promotes**

Temporarily change `MAPPER_VERSION` to `2` in `mapper.ts` and re-run. Expected: it walks every rejected row, promotes none (the rules did not change), and stamps them all at version 2. Confirm with:

```sql
SELECT status, "mapperVersion", COUNT(*) FROM "ProviderQuestion" GROUP BY status, "mapperVersion";
```

Then **revert `MAPPER_VERSION` to 1** and reset the stamps so the next real bump re-examines them:

```sql
UPDATE "ProviderQuestion" SET "mapperVersion" = 1 WHERE status = 'REJECTED';
```

- [ ] **Step 5: Commit**

```bash
git add scripts/repromote-provider-questions.ts package.json
git commit -m "feat(provider): re-promote rejected questions without re-fetching"
```

---

## Rollout note

Ship Tasks 1–11 first and leave `QUESTION_PROVIDER_ENABLED=false`. That gives a complete, admin-only ingestion path with nothing student-facing, so imported-question quality can be judged on real data before Task 14 exposes it. Tasks 12 and 13 are independently valuable — 12 fixes an existing bug, 13 only changes what the picker offers.
