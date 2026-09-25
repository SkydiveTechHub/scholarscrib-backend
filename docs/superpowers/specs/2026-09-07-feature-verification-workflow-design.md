# Feature Verification Workflow — Design

**Date:** 2026-09-07
**Status:** Approved for planning

## Problem

Feature work in this repo already has a strong front half. The superpowers
chain — brainstorm, spec, plan, subagent-driven implementation, code review —
is in daily use and has produced 19 specs and 14 plans. What it does not have
is a credible back half.

Concretely:

- **Nothing proves the app works.** `npm test` runs roughly 50 files under
  `node --test`. Every one of them tests pure logic: no browser, no HTTP, no
  database. Nothing in the repo verifies that a student can sign in, start a
  practice session, and finish it.
- **Nothing measures performance.** No bundle analysis, no query profiling, no
  latency measurement. A regression would ship unnoticed.
- **Nothing gates a merge.** There is no CI. Vercel deploys on merge, so a
  broken commit reaches production with nothing in between.
- **"Done" is a judgment call.** Specs describe behavior in prose. There is no
  numbered, testable definition of done for a reviewer to check against, so
  verification is necessarily subjective.

A parallel, rejected framing is worth recording: the original request was for a
nine-role "development team" skill suite (PM, UI/UX, engineer, QA, DevOps, and
so on). That was deliberately trimmed. The roles were mostly ceremony over a
process that already works; the real gaps above are tooling gaps, and a skill
that *describes* end-to-end testing does not *provide* end-to-end testing. See
"Rejected alternatives".

## Goals

1. A feature can be verified end to end against an objective definition of done,
   by an agent that did not write it.
2. Every verified feature leaves behind a durable regression test, so the suite
   grows as a by-product of work that was happening anyway.
3. Performance regressions are caught on the surfaces a feature touched, with
   recorded numbers that can be compared across features.
4. Nothing broken reaches `main`, because `main` deploys.

## Non-goals

- Replacing or duplicating the superpowers chain. Brainstorming, planning,
  implementation, debugging and code review stay exactly as they are.
- A full-app performance audit. Measurement is scoped to what changed.
- Cross-browser testing. Chromium only until there is a reason otherwise.
- Visual regression testing.
- Replacing code review. Code review reads the diff; verification exercises the
  running app. They catch different classes of defect.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Scope | One skill plus three pieces of infrastructure | The gaps are tooling, not process. A nine-stage pipeline would be routed around by the third feature. |
| E2E runner | Playwright | The app is the product; the tests must drive the real thing. Playwright is the current default for Next.js. |
| Suite growth | Incremental, per feature | Avoids a large upfront E2E project standing between the user and the workflow. |
| Test database | Local Postgres | Isolated, repeatable, resettable, and the only option CI can run. |
| Postgres provisioning | Native Windows installer | Docker is not installed and is a heavy prerequisite. A service matches the URL already written in `.env.test`. |
| Performance scope | Only surfaces the feature touched | A whole-app audit tells you the app got slower without telling you which change did it. |
| Verifier isolation | Subagent, denied the plan and implementation reasoning | The context that wrote the code is the context that shares its blind spots. |

## Component 1 — End-to-end test harness

**Dependency:** `@playwright/test` as a devDependency, plus its Chromium binary
(downloaded outside the repo).

**Prerequisite (user action):** PostgreSQL 16 for Windows, installed as a
service, with a `scholarscrib-test` database and the credentials already recorded in
`.env.test`:

```
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/scholarscrib-test
DIRECT_URL=postgresql://postgres:postgres@localhost:5432/scholarscrib-test
```

That file already exists and points here; nothing is currently listening. Note
that against localhost, `prisma migrate deploy` works normally — this harness
sidesteps the known Supabase-unreachable problem rather than inheriting it.

**Layout:**

```
playwright.config.ts        baseURL localhost:3000, chromium, webServer boots the app
e2e/
  global-setup.ts           signs in each role once, saves storageState
  fixtures/                 typed fixtures: student page, admin page, seed handles
  *.spec.ts                 one spec per feature area
scripts/seed-e2e.ts         deterministic fixture data
```

**Test data.** `scripts/seed-e2e.ts` resets and rebuilds a fixed dataset each
run: one student with a known password, one admin, one subject with a topic, and
a small question bank. Deterministic ids so specs can assert against them. The
schema has 38 models; the seed covers only what tests reach, and grows with the
suite.

**Auth.** Every meaningful flow is behind a login, and the app runs two separate
NextAuth instances (student at `/api/auth`, admin at `/admin/api/auth`, with
deliberately different secrets). Global setup signs in once per role through the
real UI and saves `storageState`, which specs reuse. Tests do not re-login.

**Scripts** added to `package.json`: `e2e`, `e2e:ui`, `e2e:seed`, `e2e:migrate`.

## Component 2 — The `verify-feature` skill

Lives at `.claude/skills/verify-feature/SKILL.md`. Invoked after implementation
and code review, before merge.

**Isolation contract.** It runs as a subagent whose inputs are the acceptance
criteria, the diff, and the running application. It is *not* given the
implementation plan, the engineer's task reports, or the reasoning behind the
code. It is told explicitly that finding no defects is a suspicious result and
warrants a second pass with a different angle of attack.

**Sequence:**

1. **Baseline.** Confirm `lint`, `typecheck:tests` and `npm test` are green.
   A red baseline stops the pass — nothing downstream is trustworthy.
2. **Exploratory pass.** Drive the real app in a browser against each numbered
   acceptance criterion. Every screen is checked in each state it can occupy:
   empty, loading, error, partial, and success. Adversarial input is expected,
   not optional.
3. **Codify.** Write or extend a Playwright spec covering the happy path plus a
   regression case for every defect found. This is the mechanism by which the
   suite grows.
4. **Performance pass**, scoped to what the feature touched:
   - Prisma query count per request on changed paths, with N+1 patterns called
     out explicitly.
   - Latency of new or changed API routes.
   - Client bundle delta for affected routes.
   - Server/client component boundaries — flagging anything pulled client-side
     that did not need to be.
5. **Report.** Write `docs/team/YYYY-MM-DD-<slug>-verification.md`.

**Report contract.** Each defect carries: repro steps, expected versus actual,
severity, the acceptance criterion number it violates, and evidence. Performance
numbers are recorded as measurements, never as claims. The skill reports; it
never fixes — fixes go back through `superpowers:systematic-debugging` so the
same context does not both cause and absolve a defect.

**Project rules the skill must carry**, because subagents do not inherit them by
osmosis:

- Read the relevant guide under `node_modules/next/dist/docs/` before writing
  code — this Next.js differs from training data (per `AGENTS.md`).
- A new `scripts/test-*.mts` is not done until it is registered in the `test`
  script in `package.json`. That hand-maintained list is where coverage silently
  dies.
- `prisma generate` fails with EPERM while the dev server holds the query engine
  DLL, and the resulting stale client surfaces as bogus `tsc` errors. Do not
  chase those as real type failures.

## Component 3 — Acceptance criteria

An addition to `AGENTS.md`, not a skill:

- Every spec ends with an **Acceptance criteria** section: numbered, testable,
  each one observable from outside the code.
- Plan tasks cite the criterion numbers they satisfy.
- `verify-feature` checks the criteria and nothing else, so an untestable
  criterion is a spec bug to fix before implementation starts.

This is the smallest change in the design and the one the rest depends on.
Without it, verification has no objective target and collapses back into
opinion.

## Component 4 — Continuous integration

`.github/workflows/ci.yml`, on push and pull request:

1. Install dependencies, `prisma generate`.
2. `npm run lint`, `npm run typecheck:tests`, `npm test`.
3. Postgres service container → `prisma migrate deploy` → `npm run e2e:seed`.
4. `npm run e2e`.

Environment variables come from dummy CI values, not real secrets — the E2E
database is disposable and no external provider is called
(`QUESTION_PROVIDER_ENABLED=false`). Third-party integrations (Termii,
Cloudinary, Resend, sdashapi) are stubbed at the boundary rather than exercised.

`main` deploys to Vercel on merge, so this workflow is the only thing standing
between a broken commit and production. It should become a required check.

## Risks

- **E2E flakiness.** Browser tests fail intermittently and erode trust faster
  than they build it. Mitigation: no arbitrary waits, assert on user-visible
  state, and treat a flaky spec as a defect in the spec — fix or delete it,
  never retry it into passing.
- **Seed drift.** Thirty-eight models mean the seed will lag the schema.
  Mitigation: the seed covers only what tests reach, and a failing seed after a
  schema change is a signal, not a chore.
- **CI runtime.** A browser suite plus a database is slower than the current
  unit tests. Mitigation: unit tests and linting run first and fail fast; E2E
  parallelizes when it grows.
- **The skill going stale.** A skill describing a process nobody follows is
  worse than no skill. Mitigation: it is one skill, invoked at one moment, with
  a concrete output file. If three features pass without it being used, that is
  evidence to delete it.

## Rejected alternatives

- **The nine-role team suite** (`ship-feature` orchestrator, `product-brief`,
  `ux-design`, `qa-signoff`, `performance-pass`, `bug-triage`,
  `release-readiness`, `team-workflow`). Rejected as ceremony: the roles largely
  re-describe a process that already works, the five approval gates would be
  bypassed under time pressure — which teaches distrust of the process itself —
  and the role-play framing does not produce the quality. The artifacts and the
  verification do. Revisit only if a specific absence is actually felt.
- **Agent-driven browser checks with no Playwright.** No new dependencies, but
  nothing repeatable: no regression suite, nothing CI can run, every check
  starting from scratch.
- **Building the full Playwright suite upfront.** A substantial project standing
  between the user and the workflow requested. Incremental growth reaches the
  same place without the stall.
- **Testing against the existing Supabase dev database.** Zero setup, but runs
  pollute real data, tests cannot reset between runs so failures cascade, and it
  can never run in CI.

## Acceptance criteria

1. A developer with PostgreSQL installed can run `npm run e2e` and see a passing
   suite against a freshly seeded local database.
2. The suite includes at least one spec that signs a student in and completes a
   practice session end to end.
3. `npm run e2e:seed` is idempotent: running it twice in a row produces the same
   database state and a passing suite both times.
4. Invoking `verify-feature` against a feature with acceptance criteria produces
   `docs/team/<date>-<slug>-verification.md` containing a verdict per criterion,
   any defects with repro steps, and recorded performance measurements.
5. `verify-feature` correctly reports a deliberately introduced defect —
   verified by seeding one and confirming the pass catches it.
6. CI runs on pull request and fails when any of lint, typecheck, unit tests, or
   E2E fails.
7. `AGENTS.md` states the acceptance-criteria requirement, and the next spec
   written after this one satisfies it.
