# ScholarsCrib — Product Requirements Document

| | |
|---|---|
| **Product** | ScholarsCrib |
| **Category** | Exam preparation / EdTech (Secondary Education) |
| **Market** | Nigeria (WAEC, JAMB, NECO candidates) |
| **Target users** | SS1–SS3 students, resitting WASSCE/GCE candidates, private candidates |
| **Document version** | v0.2 |
| **Date** | 2026-09-03 |
| **Product version** | 0.1.0 (pre-beta) |
| **Stage** | Feature-rich, content-poor. Pre-beta. |
| **Next milestone** | Closed beta, Nov–Dec 2026 (free) |

> **How to read this document.** Sections 1–8 are the product case: problem,
> users, value, competitive position, and what has been built. Sections 9–14 are
> the delivery detail: a subsystem-by-subsystem build ledger, v1.0 scope,
> timeline, version scheme, open decisions, and risks. Executives and partners
> can stop at section 8. The build team should start at section 9.

---

## 1. Executive Summary

ScholarsCrib is an all-in-one digital exam-preparation platform for Nigerian
secondary-school students preparing for WAEC WASSCE, JAMB UTME, and NECO SSCE.
It combines a syllabus-aligned curriculum (SS1–SS3), a past-question bank, full
CBT and mock-exam simulations, spaced-repetition flashcards, a personalized
study-plan generator, an evidence-based mastery model, and gamification into a
single product — closing the gap between the "practice-only" apps and the
"notes-only" portals currently fragmented across the Nigerian EdTech market.

**Where the build actually stands.** The learning machine is built and tested.
Thirteen design-to-implementation cycles between 2026-07-27 and 2026-09-02 have
produced a working curriculum and lesson engine, four distinct practice modes
including a full 180-question JAMB CBT simulation, an append-only learning-event
ledger with recency-decayed topic mastery, a spaced-repetition flashcard system,
a study-plan generator, and a complete admin console with its own isolated
identity, audit log, and student administration. Twenty database migrations and
48 test suites back it.

**The gap is content, not capability.** The question bank holds roughly 2,500
questions across four subject streams, against a curriculum that claims 45
subjects. Every analytics, mastery, and study-plan feature is throttled by that.
The current workstream — a read-through ingestion cache over a third-party
past-question provider — exists specifically to close it, and is the critical
path to beta. A live probe measured 87% usable yield across 249 questions
spanning 2005–2022, so the approach is validated; what remains is finishing the
pipeline and sweeping the catalogue into our own database.

**The plan.** Land the in-flight work through September, fill the bank through
October, harden and run a free closed beta in November–December 2026, then add
payments and launch v1.0 publicly ahead of the 2027 exam season.

## 2. Problem Statement (Nigerian Context)

Every year ~1.5–2 million candidates sit JAMB UTME and millions more sit WAEC
and NECO. Yet the prep experience is broken:

1. **Fragmented tools.** Students jump between a past-question app (Pass.ng,
   TestDriller), a notes website (ClassNotes.ng), YouTube, and paper textbooks.
   Progress and weaknesses are never connected across these.
2. **Cost.** Quality past-question banks and CBT software are sold per-software,
   per-device or via costly centres; many students in public schools cannot
   afford them.
3. **No personalization.** Most competitors serve the same 40-question practice
   to every student. Nobody tells a student *which topics* are their weakest or
   sequences revision based on exam-date countdown and topic weight in the real
   paper.
4. **Syllabus blindness.** Students study topics that barely appear in WAEC/JAMB
   while neglecting high-weight topics. Weighting is not surfaced anywhere.
5. **Format shock.** JAMB is now fully CBT; many candidates practise only on
   paper and underperform on timing, navigation, and auto-submit behaviour.
6. **Data-poor.** Candidates don't know their predicted grade (A1–F9 scale),
   accuracy, or mastery per topic until the real result arrives.

## 3. Target Users & Personas

| Persona | Description | Core need |
|---|---|---|
| **Chiamaka (SS3, Science)** | Lagos public-school student aiming for Medicine (JAMB cut-off ~280+). | Predict-grade accuracy, weak-topic coaching, full CBT simulation. |
| **Ibrahim (SS2, Commercial)** | Kano student planning Accounting. | Track-aligned subjects (Maths, Econ, Commerce), structured study plan. |
| **Resitter / private candidate** | Out-of-school WASSCE/GCE candidate. | Affordable full-curriculum coverage plus past questions in one place. |
| **Admin / content curator** | Internal operator maintaining the question and lesson bank. | Bulk import, review, audit, and student administration tooling. |
| **Teacher / school** *(deferred)* | School staff assigning and monitoring work. | Classroom assignment and cohort analytics. Not in v1.0 — see section 10. |

## 4. Value Proposition

> "One platform that **teaches** you the WAEC/JAMB/NECO syllabus, **tests** you
> with real past questions, **scores** you on the actual grading scale, and
> **plans** your revision backwards from exam day — built specifically for
> Nigerian students."

## 5. The Closed Loop

The product's organizing idea is a loop that competitors only ever cover one or
two stages of. Each stage is a shipped subsystem, not an aspiration:

| Stage | What happens | Backing subsystem |
|---|---|---|
| **Teach** | Syllabus-structured lessons with key points, worked examples, LaTeX maths, and media. | Lesson engine, classroom, admin lesson upload |
| **Test** | Four practice modes over a tagged past-question bank, timed and exam-accurate. | Assessment generation, JAMB CBT, mock exams |
| **Diagnose** | Every answer writes to an append-only ledger; mastery decays with time; gaps are classified. | Learning evidence layer, topic mastery, gap analysis |
| **Plan** | Revision sequenced backwards from exam date against the knowledge graph and detected weaknesses. | Learning-path engine, study plan, spaced repetition |

Each stage feeds the next. A wrong answer in Test changes what Diagnose
believes, which changes what Plan schedules, which routes the student back to
Teach. That loop is the moat; sections 6 and 9 record how much of it is real
today.

## 6. Core Features

### 6.1 Accounts & Profiles
Email/password sign-up and Google OAuth (NextAuth v5 + Prisma adapter). Profile
carries name, email, phone, state, and school, with Cloudinary avatar upload.
The academic profile — class level (SS1/SS2/SS3) and track
(Science/Arts/Commercial) — drives curriculum scope and subject defaults.
Account status supports suspension with live session revocation.

### 6.2 Curriculum & Lessons
Forty-five subjects mapped to WAEC/JAMB/NECO availability and grouped by track
category: Core, Science, Arts, Commercial, Vocational. Curriculum is structured
per class level and term, with topics carrying WAEC and JAMB weightings,
prerequisites expressed as a directed knowledge graph, and estimated study
minutes. Lessons carry markdown content with LaTeX rendering, segmented key
points, worked examples, and attached resources.

### 6.3 Question Bank
Past-paper questions filed by exam type and year, each tagged to a syllabus
topic. Objective, theory, and fill-in-the-blank types with difficulty, marks,
time estimates, and full explanations. Two ingestion paths: an idempotent JSON
CLI importer and an admin console import with validation and duplicate
detection.

### 6.4 Practice Modes
- **Subject practice / past questions** — 40 random questions per subject,
  timed at 60 minutes, auto-submit, flag-for-review, question navigator.
- **JAMB CBT practice** — per-subject JAMB-format practice across the JAMB
  subject list.
- **Full CBT simulation** — 180 questions across 4 subjects in 120 minutes,
  mirroring the official JAMB UTME interface and subject tabs.
- **Mock exams** — WAEC/NECO/JAMB with configurable time limits and scoped
  subject selection.

Exam configuration is accurate to the real papers: JAMB 180q / 120min / 400
marks, WAEC/NECO A1–F9 grading, no negative marking. Attempts track timing,
abandonment, and focus loss — the platform counts how often a student leaves an
exam and surfaces that on high-stakes results.

### 6.5 Results, Grading & Analytics
Instant scoring against official A1–F9 WASSCE boundaries and JAMB letter bands.
Per-question review with explanations. A performance dashboard reporting
accuracy, attempts, latest grade, and subject-level metrics, with weak topics
surfaced from the evidence layer rather than raw wrong-counts. Mastery is
tracked per subject and topic on a Weak → Developing → Competent → Strong scale
with confidence floors and recency decay.

### 6.6 Learning Path & Study Plan
A knowledge graph of topic prerequisites drives recommendation, revision
sequencing, pre-tests that let a student skip what they already know, and gap
classification into WEAK / DECAYED / BOTTLENECK / ABANDONED / UNTOUCHED. The
study plan generates a week-by-week schedule backwards from the exam date across
chosen subjects with a daily-hours budget, mixing lessons, practice, revision,
past questions, and mock exams. Regenerable at any time.

### 6.7 Flashcards
Decks generated from lesson content or authored directly, with cloze, basic, and
objective card types. Scheduling uses a spaced-repetition algorithm with review
logging, enrollment, per-deck analytics, and a stats surface. Cards built from a
lesson stay linked to their source and diff against it when the lesson changes.

### 6.8 Gamification
Achievements and badges across criteria types: questions answered, perfect
scores, day streaks, lessons completed, subject mastery, and mock score ≥70%.

### 6.9 Library
Curated per-subject resources — textbooks, videos, PDFs, worksheets, past papers
— with free/premium flags and an in-app PDF reader.

### 6.10 JAMB Subject-Combination Guidance
Reference data for required JAMB subject combinations per course and faculty,
plus approximate cut-off tiers (140–280+) by course competitiveness.

### 6.11 Administration
A full admin console on an isolated identity: a second NextAuth instance mounted
at `/admin/api/auth` with its own secret and cookie scope, so a leaked student
secret cannot forge admin tokens. Provides question authoring and bulk import,
lesson upload and browsing, student list and detail with filters and pagination,
profile editing, subscription-tier override, suspension, owner-only force
sign-out and deletion, an admin team page, and an audit log viewer with entity
filtering.

## 7. Technical Architecture

| Layer | Choice |
|---|---|
| Framework | Next.js 16 (App Router), React 19 |
| Database | PostgreSQL via Prisma ORM (Supabase-hosted) |
| Auth | NextAuth v5 — separate student and admin instances |
| Styling | Tailwind CSS v4, lucide / react-icons |
| Media | Cloudinary (avatars, mirrored question images), react-pdf |
| Validation | Zod 4 |
| Charts | Recharts |
| Content | Versioned JSON question bank plus provider read-through cache |
| Tests | Node test runner over tsx, 48 suites |

**Architectural principles worth stating.** Business rules are kept
database-free and unit-testable — the subscription tier union, curriculum scope,
and grading boundaries are declared in plain modules rather than imported from
Prisma, so rules can be tested without a database. Every subsystem lands through
a written design document and an implementation plan committed to
`docs/superpowers/`, which is why the build ledger in section 9 can cite
evidence rather than recollection.

## 8. Competitive Landscape & Advantage

### 8.1 The Market
| Competitor | Focus | Gaps ScholarsCrib addresses |
|---|---|---|
| **Pass.ng** | Past questions + mini-lessons | Practice-only; no personalized study plan, no curriculum weighting, limited analytics |
| **TestDriller** | Offline CBT software (per-device licence) | Paid, device-bound; no web analytics or plan generation |
| **Myschool.ng / Flashlearners** | Past questions + news/info | Content portal; weak learner analytics, no grading-simulation depth |
| **ClassNotes.ng** | Lesson notes | Notes only — no practice, timing, or performance tracking |
| **Local CBT centres (EduTams, etc.)** | Exam-hall mock CBT | Expensive, location-bound, one-off sittings; no continuous learning loop |
| **International apps (Khan Academy, Quizlet, Anki)** | Generic learning | Not syllabus/exam-aligned to WAEC/JAMB/NECO; wrong grading scale; not Nigeria-specific |

### 8.2 What Makes ScholarsCrib Different
1. **Fully Nigerian by design.** A1–F9 grading, JAMB 180q/120min/400-mark
   configuration, cut-off tiers, 36 states plus FCT, and the NERDC subject list
   across Science/Arts/Commercial/Vocational tracks — none of it an afterthought.
2. **The closed loop.** Teach → Test → Diagnose → Plan, as described in section
   5. Competitors cover one or two stages, not the loop.
3. **Exam-weight-aware studying.** WAEC/JAMB topic weightings let the plan and UI
   prioritize high-yield topics — a feature no mainstream Nigerian app exposes.
4. **An evidence layer, not a scoreboard.** An append-only, timestamped,
   difficulty-tagged ledger of every answer, lesson checkpoint, and card review,
   folded into mastery with recency decay and confidence floors. This is what
   makes "you are weak here" defensible rather than a wrong-answer tally.
5. **Full official-format simulation.** A complete 180-question, 4-subject,
   timed, tabbed JAMB CBT that mirrors the real interface, including focus-loss
   tracking — not just a Q&A drill.
6. **One subscription, whole prep.** Replaces two to four separate paid products
   with a single platform.
7. **An owned content pipeline.** The provider cache is designed so that every
   third-party call is captured permanently into our own database on the way
   past. Once a paper is drawn once, we hold it forever. The provider becomes
   unnecessary rather than a permanent dependency, cost, and outage risk.

---

# Delivery Detail

## 9. Build Status Ledger

State definitions: **Shipped** — on `main`, tested, in use. **In flight** —
written and committed on a branch, not yet merged. **Seam only** — the interface
and data model exist and are tested, but the behaviour behind them is
deliberately undefined. **Not started** — designed or scoped, no code.

### 9.1 Shipped

| Subsystem | Evidence |
|---|---|
| Accounts, auth, profiles, settings | `2026-07-27-settings-and-profile-design.md`; initial migration |
| Curriculum, lessons, classroom | `2026-08-01-lesson-engine-design.md`, `2026-08-05-classroom-design.md`; `lesson_engine` migration |
| Lesson note upload + natural note format | `2026-08-05`, `2026-08-06` specs; admin lesson import |
| Question bank + idempotent importer | `data/*.json`, `scripts/import-questions.ts` |
| Practice, past questions, JAMB CBT, mock exams | `assessment-generation.ts`, `jamb-cbt-generation.ts`, `mock-exam-availability.ts` |
| Attempt lifecycle, timing, abandonment | `attempt-lifecycle.ts`, `attempt-timing.ts` |
| Exam focus tracking | `2026-08-26-exam-focus-tracking-design.md`; `attempt_away_events` migration |
| Learning-path engine + knowledge graph | `2026-08-02-learning-path-engine-design.md`; `learning_path` migration |
| Learning evidence layer, phases 1 and 2 | `2026-08-11`, `2026-08-12`, `2026-08-17` specs; `learning_evidence_layer` migration |
| Topic mastery with decay and confidence floors | `topic-mastery-store.ts`, `observation_counts` migration |
| Flashcards + spaced repetition | `2026-08-01-flashcards-design.md`; `flashcards` migration |
| Flashcards built from lessons, with source diffing | `2026-08-25-flashcard-build-from-lesson-design.md` |
| Study plan generator | `study-plan.ts` |
| Achievements, streaks, library | `achievements.ts`, `streak.ts`, `library.ts` |
| Admin console phase 1 + lesson browse | `2026-08-04`, `2026-08-06` specs |
| Admin isolated identity, audit log | `2026-08-26-admin-identity-design.md`; `admin_identity` migration |
| Student administration, suspension, session revocation | `student_account_status` migration |

### 9.2 In flight

| Item | State | Branch |
|---|---|---|
| **Performance analytics phase 1** — subject, exam, and progress lenses over the evidence ledger, with predicted grade bands and an honest "not enough data yet" verdict | Complete on branch, 13 commits, **unmerged** | `feat/performance-analytics-phase-1` |
| **Admin console structure** — grouped navigation, shared table and detail primitives, subscription-tier seam | 2 commits ahead, **unmerged**, 28 behind `main` | `feat/admin-console-structure` |
| **Paystack billing + tier gating** — `Subscription` rows as the source of truth with `User.tier` as a derived cache, hosted-checkout payment, signed webhook, admin comps, and the `ENTITLEMENTS` matrix enforced server-side across flashcards, the study planner, the subject breakdown, and premium library resources | `2026-09-04-paystack-billing-design.md`; `billing` migration; **unmerged** | `feat/paystack-billing` |
| **Question provider cache** — read-through ingestion from sdashapi. Tasks 1–10 committed: schema, alias tables, cache key, response classifier, saturation rule, mapper, adapter, image mirroring, ingestion orchestrator, catalogue sweep script. Task 11 (admin backfill endpoint) is uncommitted in the working tree. Tasks 12–15 open: thread `examYear` through quiz generation, offer uncached papers in the picker, wire the cache into generation, re-promotion sweep | 15 commits, **unmerged** | `ft/try-sdash` |

### 9.3 Seam only

| Item | What exists | What does not |
|---|---|---|
| **Usage-based tier limits** | The `ENTITLEMENTS` table in `subscription.ts` and the `can()` predicate every gate calls. Billing and the boolean gates themselves have shipped — see 9.2 | No usage metering, so the Free tier's advertised caps (3 subjects, 25 questions a day, 1 mock exam) are unenforced. Adding them means adding rows to `ENTITLEMENTS`, not changing call sites |
| **Teacher role** | `Role.TEACHER` exists in the schema | Offered at registration as "coming soon" and deliberately rejected by the validator. No classroom assignment, no school analytics |

### 9.4 Known gaps

| Gap | Impact |
|---|---|
| **Content coverage** | ~2,493 questions across four subject streams (JAMB Biology 1983–2004, Commerce, Economics, Financial Accounting, plus 2023 WAEC/JAMB samples) against a 45-subject curriculum. Every downstream feature — mastery, gaps, study plan, predicted grades — is throttled by this. **This is the critical path to beta.** |
| **Imported questions are untagged** | The provider cache explicitly does not topic-tag what it ingests. Untagged questions can be practised but cannot feed the learning path, mastery model, or weak-topic analytics — so ingestion alone widens the bank without deepening the loop. Section 10 pulls tagging into v1.0 scope to close this. |
| **`PerformanceMetric.masteryLevel` is stale** | Written by topic-practice and pre-test paths only, never by ordinary question answering. Documented divergence; the evidence layer is the source of truth. Not fixed in analytics phase 1. |
| **README is unedited boilerplate** | Still the `create-next-app` template plus one env-var note. Onboarding cost for anyone new. |
| **Branch backlog** | Three unmerged branches, one 28 commits behind `main`. Merge debt compounds. |

## 10. v1.0 Scope

### Committed
1. **Provider question ingestion, finished.** Complete Tasks 11–15 of the
   provider cache, then sweep the catalogue until the bank is a usable offline
   copy across the subjects the curriculum claims. Success is measured in
   subject-year coverage, not request count.
2. **Topic-tagging of imported questions.** Ingested questions must be mapped to
   syllabus topics so they feed the learning path, mastery model, and analytics.
   **This is a new workstream needing its own design pass** — it is currently an
   explicit non-goal of the provider cache design, not a follow-on task.
3. **Payment integration and tier gating.** Built and **active** as of
   2026-09-05, ahead of the M5 window this originally sat in. Paystack checkout
   is live behind `PAYSTACK_SECRET_KEY`, and the boolean half of the
   entitlement matrix is enforced server-side.

   This supersedes the earlier plan to keep the closed beta free. **The
   consequence is unresolved:** flashcards, the study planner, and the subject
   breakdown are now unavailable to FREEMIUM accounts, which is every existing
   user. Either the beta cohort gets comped to STANDARD or higher through the
   admin console — the comp path exists for exactly this — or M4 is no longer a
   free beta and the metrics it was meant to produce change shape. Decide before
   the cohort is recruited, not after.

   Still open: the Free tier's numeric caps (3 subjects, 25 practice questions a
   day, 1 mock exam) are advertised on the landing page but not enforced. They
   need usage metering. Until that ships, the landing page overstates the limits
   on Free rather than understating them, which is the safe direction but is
   still inaccurate.

   Separately, the Premium column on the landing page advertises an **AI tutor**
   and **offline mode**. Neither exists — offline is explicitly deferred beyond
   v1.0 below — and neither can be gated, because there is nothing to gate. They
   must come off the pricing page before it takes real money, or move onto the
   roadmap as v1.0 scope.

### Deferred beyond v1.0
- **Teacher / school accounts.** Classroom assignment, cohort analytics, school
  administration. Has its own consent, access, and pricing questions.
- **PWA, offline, and low-bandwidth mode.** Real for the target market, but it
  does not gate beta learning.
- Live proctoring, official exam registration, JAMB portal integration.
- Post-secondary content (post-UTME, GMAT-type).
- Parent portal and cohort/peer comparison — both ruled out in the analytics
  design for defensible reasons.

## 11. Timeline

Anchored on a **free closed beta in November–December 2026**, working backwards
from today (2026-09-03). Roughly seven months of runway to the 2027 exam season.

| Milestone | Window | Goal | Exit criteria |
|---|---|---|---|
| **M1 — Land in flight** | Sep 2026 | Clear the branch backlog and finish the ingestion pipeline | `feat/performance-analytics-phase-1` and `feat/admin-console-structure` merged to `main`; provider cache Tasks 11–15 complete and merged; full test suite green |
| **M2 — Fill the bank** | Sep–Oct 2026 | Turn the pipeline into content | Catalogue sweep run to saturation across target subject-years; topic-tagging designed, built, and applied to imports; coverage reported per subject and exam |
| **M3 — Beta hardening** | Oct 2026 | Make it safe to put in front of real students | Content QA pass on ingested questions; rate limiting and abuse review; error monitoring and observability; onboarding flow tested end to end; beta cohort recruited |
| **M4 — Closed beta** | Nov–Dec 2026 | Learn from real usage | Cohort live on `0.9.0`; activation, engagement, and accuracy-improvement metrics instrumented and reporting; weekly feedback triage. **No longer free by default** — gating shipped early, so the cohort must be comped to a paid tier or the beta runs paywalled; see §10 item 3 |
| **M5 — Launch** | Dec 2026 – Q1 2027 | Public v1.0 before the exam season | ~~Payment provider integrated; tier entitlements defined and gated~~ — both done 2026-09-05. Remaining: usage metering for the Free numeric caps; beta findings addressed; `1.0.0` public ahead of April 2027 JAMB |

**Critical path: M1 → M2.** Everything else has slack; these do not. M2 depends
on a third-party API whose behaviour we have measured but do not control, and it
carries a new, undesigned workstream (topic-tagging). If either slips, the beta
window compresses into December rather than moving — cut beta cohort size and
subject breadth before cutting the hardening in M3.

## 12. Version Scheme

Semantic versioning, with the pre-1.0 range carrying explicit meaning so that
release state is never ambiguous:

| Version | Meaning |
|---|---|
| `0.1.0` | **Today.** Feature-rich, content-poor, pre-beta. No external users. |
| `0.5.0` | M1 and M2 complete — ingestion finished, bank filled, imports tagged. Internal use only. |
| `0.9.0` | **Closed beta.** Feature-complete for beta scope. Monetisation is no longer absent — payments and gating shipped 2026-09-05 — so "free to the cohort" now depends on comping the cohort rather than on the code. Patch releases `0.9.x` during beta. |
| `1.0.0` | **Public launch.** Payments live and tier entitlements enforced (both already true); Free-tier usage caps metered; beta findings addressed. |
| `1.x` | Post-launch features: teacher/school accounts, PWA/offline, expanded exam coverage. |

`package.json` is the single source of the product version and should be bumped
at each milestone boundary, tagged in git. There are currently no git tags; M1
should establish the practice.

## 13. Success Metrics

**Beta targets (M4)** — the beta is a learning exercise, so these are diagnostic
thresholds rather than growth goals:

| Metric | Target | Why |
|---|---|---|
| Activation — registered users completing a first practice session | ≥60% | Below this, onboarding or content breadth is broken |
| Week-2 retention | ≥40% | The loop either pulls students back or it does not |
| Practice attempts per active user per week | ≥3 | Enough signal for the evidence layer to produce non-trivial mastery |
| Students with enough data for a confident verdict | ≥50% by week 4 | Directly tests whether the evidence layer's confidence floors are set right against real usage |
| Content faults reported per 1,000 questions served | Tracked, no target | Establishes the ingested-content quality baseline |

**Post-launch (v1.0+):** accuracy improvement between first and last attempt;
free-to-premium conversion; 7-day streak retention; reach per school and state.

## 14. Open Decisions

| # | Decision | Options | Needed by |
|---|---|---|---|
| 1 | **Payment provider** | Paystack vs. Flutterwave. Weigh settlement terms, card and transfer coverage, subscription/recurring support, and sandbox quality for Nigerian cards | M5 start (Dec 2026) |
| 2 | **What each tier unlocks** | **Decided 2026-09-05 for the boolean half**, in `ENTITLEMENTS` in `subscription.ts`, derived from what the landing page already sells: flashcards and the study planner need STANDARD; premium library resources and the subject-level analytics breakdown need PREMIUM. **Still open:** the Free tier's numeric caps — 3 subjects, 25 practice questions a day, 1 mock exam — which need usage metering before they can be enforced | Caps before M5; the beta-comping question above is the more urgent one |
| 3 | **Beta cohort sourcing** | School partnership vs. direct student recruitment vs. mixed. Determines size, support load, and how representative the metrics are | M3 (Oct 2026) |
| 4 | **Topic-tagging approach** | Manual curation, heuristic matching against syllabus topics, or model-assisted tagging with review. Cost and accuracy differ sharply; drives M2 duration | M2 start (Sep 2026) |
| 5 | **Subject breadth for beta** | Full 45-subject curriculum vs. a deliberately narrow set done well. Narrow is likely right for a beta but it constrains cohort composition | M3 (Oct 2026) |

## 15. Risks & Assumptions

| Risk | Severity | Mitigation |
|---|---|---|
| **Content quality of ingested questions** | High | Measured 87% yield with promotion rules that reject incomplete rows; requires a human QA pass in M3 before student exposure. The product cannot be marketed as exam-grade until this is validated. |
| **Third-party provider dependency during the sweep** | Medium | The cache design makes the dependency temporary by construction — every call is captured permanently. Risk is concentrated in M2 only. Their bank behaves as a pool rather than a fixed paper, so sweep-to-saturation cost is estimated, not known. |
| **Topic-tagging is undesigned and on the critical path** | High | Decision 4 must be settled at M2 start. If cost proves prohibitive, the fallback is tagging a narrow high-value subject set and shipping the rest untagged for practice-only use — degrading the loop rather than blocking beta. |
| **Beta window is tight** | Medium | M1 and M2 have no slack. Compress beta scope (cohort size, subject breadth) rather than the M3 hardening pass. |
| **Data and device access among low-income students** | Medium | Accepted for beta; PWA and low-bandwidth work is deferred to v1.x and should be informed by real beta telemetry rather than assumption. |
| **Exam-body branding and trust** | Medium | Must be transparent throughout that ScholarsCrib is a practice tool and not affiliated with WAEC, JAMB, or NECO. Review copy before any public launch. |
| **Merge debt** | Low | Three unmerged branches, one significantly behind `main`. M1 exists partly to retire this. |
| **Single-maintainer bus factor** | Medium | Mitigated in practice by the design-and-plan discipline in `docs/superpowers/`, which records intent rather than just outcome. The unedited README is the weak link in that story. |
