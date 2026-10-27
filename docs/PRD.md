# ScholarsCrib — Product Requirements Document

| | |
|---|---|
| **Product** | ScholarsCrib |
| **Tagline** | Breeding room for excellence. *Learn. Practice. Pass.* |
| **Category** | Digital learning platform / EdTech (Senior Secondary Education) |
| **Market** | Nigeria (senior secondary students; WAEC, JAMB and NECO preparation is the market-entry offer) |
| **Target users** | SS1–SS3 students, post-SS3 resitters (WASSCE/GCE), private candidates |
| **Document version** | v0.4 |
| **Date** | 2026-10-26 |
| **Product version** | 0.1.0 (frontend and backend manifests) |
| **Stage** | Feature-complete for launch scope. Content-completion sprint. |
| **Next milestone** | Public launch, November 2026 |

> **How to read this document.** Sections 1–8 are the product case: problem,
> users, value, competitive position, and what has been built. Sections 9–15 are
> the delivery detail: a subsystem-by-subsystem build ledger, v1.0 scope,
> timeline, version scheme, metrics, open decisions, and risks. Executives and
> partners can stop at section 8. The build team should start at section 9.
>
> **What changed in v0.4 (since v0.3, 2026-09-28).** The product is now two
> deployables, a FastAPI backend and a Next.js frontend that is a pure client of
> it (section 7). The question bank is no longer the critical path: ~13,000
> topic-tagged questions from the ALOC provider are integrated (section 9.4).
> The critical path is now **lesson-note coverage** for every major subject. The
> Astra AI tutor, Today's Session, the configurable study timetable, push and
> in-app notifications, a PWA shell, device limits, a public SEO surface, and
> admin-side tooling (coverage reporting, admin Astra) have shipped. Paystack
> runs in **test mode** with pricing still being defined; the v0.3 statement
> that billing was "active" is corrected in section 10. The "platform vision" is
> now a dated roadmap (section 10).

---

## 1. Executive Summary

ScholarsCrib is a digital learning platform that brings school to the devices
Nigerian senior-secondary students already own. It serves SS1, SS2 and SS3
students and post-SS3 resitters. Its purpose is to make learning easy and to
change how students study and prepare for exams: not to produce crammers who
read only to pass, but students for whom every learning phase counts. It
combines a curriculum aligned to the government-approved syllabus and organised
by class level and term, topic notes by subject, a topic-tagged past-question
bank, full CBT and mock-exam simulations, spaced-repetition flashcards, a
personalised study-plan generator with a daily session, an evidence-based mastery
model, gamification, and an AI study assistant (Astra, in beta), with progress
tracking and retention evaluation throughout. Video lessons are planned. This
closes the gap between the "practice-only" apps and the "notes-only" portals
fragmented across the Nigerian EdTech market.

**Positioning.** Preparation for WAEC WASSCE, JAMB UTME, and NECO SSCE is an
integral service and the **market-entry offer**: it is the most urgent and most
easily understood reason a student or parent pays attention to a learning
product. It is not the whole product. The end goal is excellent students, not
students who merely pass and scale through, whether they are in SS1, SS2, SS3,
or resitting. At launch, marketing and onboarding lead with exam readiness;
the product, roadmap, and success metrics are built around learning quality.

**Where the build actually stands.** The learning machine is built and tested
and has since been rebuilt as a standalone FastAPI service behind a separate
Next.js client. Live today: authentication (email/password and Google), the
curriculum and lesson engine, four practice modes including a 180-question JAMB
CBT simulation, an append-only learning-event ledger with recency-decayed topic
mastery, spaced-repetition flashcards, progress tracking and performance
analytics, a study planner with class/term scope and a configurable timetable,
a daily "Today's Session", achievements and streaks, a library with signed file
delivery, push and in-app notifications, a PWA shell, a public SEO surface, a
complete admin console, and the Astra AI tutor as a beta feature. The backend
carries 17 Alembic migrations and about 50 pytest modules; the frontend carries
70+ test scripts.

**The gap is lesson content, not questions.** The ALOC integration put roughly
13,000 topic-tagged questions covering 20 years of WAEC, JAMB and NECO past
papers into the bank, which retires the question-bank gap that v0.3 called the
critical path. What remains is lesson notes: notes upload for all major subjects
is in progress, and the 30 days to launch are dedicated to completing every
subject, topic and term against the government-approved curriculum. The
positioning risk in section 15 is about exactly this.

**The plan.** Complete lesson coverage and end-to-end testing through
November's launch window, finalise pricing and move Paystack from test to live,
run the launch campaign with marketing, and open ScholarsCrib publicly in
November 2026. After launch: the AI tutor general release, video lessons, then
the teacher portal, marketplace and school portal toward the Year-3 goal of a
fully digitised senior secondary academic system (section 10).

### Beyond the student app: platform vision
Bringing school to devices extends past the individual student. Three further
layers are part of the platform vision. None is in launch scope (section 10):

- **Teacher portal.** Teachers generate lesson notes from the curriculum or a
  textbook, generate tests and exam questions seeded from the question bank,
  and upload videos for students.
- **Marketplace.** Students purchase teacher-recorded lessons, exam pins, and
  scholarship applications.
- **School portal.** Schools subscribe to a plan, create teacher accounts
  attached to subjects, onboard students, and run CBT exams as school term
  exams. The relationship model is School → Teachers → Class → Students.

The overall goal is to digitise the productivity hassles of the academic sector
and make it more effective.

## 2. Problem Statement (Nigerian Context)

### 2.1 The learning problem
Nigerian secondary education pulls students toward passing exams rather than
understanding subjects. The result is students who cram to pass and forget soon
after:

1. **Cramming over understanding.** Study effort concentrates in the months
   before an exam. Earlier learning phases (SS1, SS2, and each term of SS3) are
   treated as if they do not matter.
2. **No continuous view of progress.** Students, and those supporting them,
   rarely see how well a topic was actually understood until an exam result
   arrives.
3. **Retention is never measured.** Nothing checks whether last term's topics
   are still remembered, so forgetting goes unnoticed until it costs marks.
4. **Scattered resources.** Notes, videos, PDFs, practice questions, and
   revision aids live in separate places and are not connected to each other or
   to the student's progress.
5. **Uneven access to quality teaching.** Many students have no
   well-structured, self-paced way to learn a topic they missed or did not
   follow in class.

### 2.2 The exam-preparation problem
Every year ~1.5–2 million candidates sit JAMB UTME and millions more sit WAEC
and NECO. Yet the prep experience is broken:

1. **Fragmented tools.** Students jump between a past-question app (Pass.ng,
   TestDriller), a notes website (ClassNotes.ng), YouTube, and paper textbooks.
   Progress and weaknesses are never connected across these.
2. **Cost.** Quality past-question banks and CBT software are sold per-software,
   per-device or via costly centres; many students in public schools cannot
   afford them.
3. **No personalisation.** Most competitors serve the same 40-question practice
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
| **SS1** | First senior-secondary year, no external exam in sight. | Build strong foundations from the first term; understand each term's topics as they are taught; see whether they retain what they learn. |
| **SS2** | Mid-cycle student. | Stay on pace with the curriculum, term by term. |
| **SS3** | Exam-year student. | Sharpen for WAEC, JAMB and NECO with weak-topic coaching and full CBT simulation. |
| **Post-SS3 resitter** | Out-of-school WASSCE/GCE or JAMB candidate. | A structured second attempt with real practice; affordable full-curriculum coverage and past questions in one place. |
| **Chiamaka (SS3, Science)** | Lagos public-school student aiming for Medicine (JAMB cut-off ~280+). | Predicted-grade accuracy, weak-topic coaching, full CBT simulation. |
| **Ibrahim (SS2, Commercial)** | Kano student planning Accounting. | Track-aligned subjects (Maths, Econ, Commerce), structured study plan. |
| **Adaeze (SS1, Arts)** | Enugu student in her first senior-secondary year. | Habits early, and visibility into retention, not only scores. |
| **Parents and guardians** *(marketing audience)* | Want results and real learning. | Reassurance that the product builds understanding; not a user account in launch scope (parent portal is ruled out, section 10). |
| **Admin / content curator** | Internal operator maintaining the question and lesson bank. | Bulk import, coverage reporting, review, audit, and student administration tooling. |
| **Teacher / school** *(roadmap)* | School staff creating notes and tests, uploading videos, assigning work, running term CBT exams. | Lesson and test generation, classroom assignment, cohort analytics. Not in launch scope — section 10. |

## 4. Value Proposition

> "One platform that **teaches** you your senior-secondary curriculum, **tests**
> you, **tracks** what you actually retain, and **plans** your study, so every
> stage of school counts and exam success follows from real understanding.
> Built specifically for Nigerian students."

**Market-entry message (launch marketing).** Exam readiness is the way in:

> "Prepare for WAEC, JAMB and NECO the right way: learn the syllabus, practise
> with real past questions, get scored on the actual grading scale, and plan
> your revision backwards from exam day."

**Launch messaging.** *Breeding room for excellence.* · *Learn. Practice. Pass.* ·
*More than exam prep: a place to study and be groomed.* · *Built around the
Nigerian curriculum and WAEC, JAMB and NECO.* Brand colours: navy, blue and
gold. Audiences: students, parents and guardians, then schools and teachers as
portals arrive.

## 5. The Closed Loop

The product's organising idea is a loop that competitors only ever cover one or
two stages of. Each stage is backed by a shipped subsystem; planned additions are marked:

| Stage | What happens | Backing subsystem |
|---|---|---|
| **Teach** | Curriculum-aligned lessons by class level and term: topic notes with key points, worked examples, and LaTeX maths; Astra AI tutor in beta; videos planned. | Lesson engine, classroom, admin lesson upload, Astra |
| **Test** | Four practice modes over a topic-tagged past-question bank, timed and exam-accurate. | Assessment generation, JAMB CBT, mock exams |
| **Diagnose** | Every answer writes to an append-only ledger; mastery decays with time, so retention is measured rather than assumed; gaps are classified; topic standing is explained. | Learning evidence layer, topic mastery, gap analysis, topic standing |
| **Plan** | Study sequenced against the knowledge graph and detected weaknesses, by class and term, and backwards from the exam date when one is set; a daily session says what to do next. | Learning-path engine, study plan and timetable, Today's Session, spaced repetition |

Each stage feeds the next. A wrong answer in Test changes what Diagnose
believes, which changes what Plan schedules, which routes the student back to
Teach. That loop is the moat; sections 6 and 9 record how much of it is real
today.

## 6. Core Features

### 6.1 Accounts & Profiles
Email/password sign-up, forgot/reset-password flow, and Google OAuth completed
through a backend code exchange. Sessions are JWTs issued by the backend, in two
isolated realms (student and admin). Profile carries name, email, phone, state,
and school, with Cloudinary avatar upload and a complete-profile onboarding
step. The academic profile — class level (SS1/SS2/SS3) and track
(Science/Arts/Commercial) — drives curriculum scope and subject defaults;
changing class or track warns first and refreshes library and plan caches.
Account status supports suspension with live session revocation, and a device
limit caps STANDARD and above at two browsers (Freemium is unlimited).

### 6.2 Curriculum & Lessons
Forty-five subjects mapped to WAEC/JAMB/NECO availability and grouped by track
category: Core, Science, Arts, Commercial, Vocational. Curriculum is structured
per class level and term, with topics carrying WAEC and JAMB weightings,
prerequisites expressed as a directed knowledge graph, and estimated study
minutes. Lessons carry markdown content with LaTeX rendering, segmented key
points, worked examples, and attached resources.

**Classroom flow.** The curriculum follows the government-approved curriculum
and is divided into terms. A student chooses a subject, a class and term, and a
topic and lands on that topic's notes. Notes are shipped; **coverage of notes
across all major subjects is the launch priority** (section 10). Video lessons
are planned for the topic page, so that a topic shows its notes and videos
together.

### 6.3 Question Bank
Past-paper questions filed by exam type and year, each tagged to a syllabus
topic. Objective, theory, and fill-in-the-blank types with difficulty, marks,
time estimates, and full explanations. The bank is populated through the ALOC
provider integration (about 13,000 topic-tagged questions; WAEC, JAMB and NECO;
20 years of papers), an idempotent JSON importer, and an admin console import
with validation and duplicate detection. A provider cache with circuit breaker
and rate budget keeps the third-party dependency from blocking students. Admins
get a coverage view per subject and exam, with paper preview and export.

### 6.4 Practice Modes
- **Subject practice / past questions** — random questions per subject, timed,
  auto-submit, flag-for-review, question navigator, and a practice-exit flow.
- **JAMB CBT practice** — per-subject JAMB-format practice across the JAMB
  subject list.
- **Full CBT simulation** — 180 questions across 4 subjects in 120 minutes,
  mirroring the official JAMB UTME interface and subject tabs.
- **Mock exams** — WAEC/NECO/JAMB with configurable time limits and scoped
  subject selection.
- **Topic quizzes** — short topic-level checks that write to the evidence
  ledger and feed topic standing.

Exam configuration is accurate to the real papers: JAMB 180q / 120min / 400
marks, WAEC/NECO A1–F9 grading, no negative marking. Attempts track timing,
abandonment, and focus loss, and surface it on high-stakes results.

### 6.5 Results, Grading & Analytics
Instant scoring against official A1–F9 WASSCE boundaries and JAMB letter bands.
Per-question review with explanations. A performance section with subject, exam
and progress lenses over the evidence ledger, predicted grade bands, and an
honest "not enough data yet" verdict. Mastery is tracked per subject and topic
on a Weak → Developing → Competent → Strong scale with confidence floors and
recency decay; topic standing explains where a student stands on a topic and why.
Advanced analytics is opened to all tiers during the beta.

### 6.6 Learning Path, Study Plan & Today's Session
A knowledge graph of topic prerequisites drives recommendation, revision
sequencing, pre-tests that let a student skip what they already know, and gap
classification into WEAK / DECAYED / BOTTLENECK / ABANDONED / UNTOUCHED.

The study plan has three modes: **term** (follows the school term), **blended**,
and **exam** (backwards from the exam date). A plan carries its own class and
term scope, credits completed lessons, scopes subjects to the student's track,
revises only fading topics from its scope or earlier, replans from mastery and
pre-test results, and can be deleted and regenerated. Students can build a
**configurable study timetable** (editor and plan view), and Astra can propose
and report on timetables.

**Today's Session** replaces the dashboard's overlapping topic lists with one
stable, time-boxed daily queue of 3–4 actions, each with minutes, a direct link
and an evidence-based reason, ordered by learning science (retrieval of fading
material first, then focused practice, then new material), with completion
recorded per item. Streak and reminder calendars use `Africa/Lagos` time.

### 6.7 Flashcards
Decks generated from lesson content or authored directly, with cloze, basic, and
objective card types. Scheduling uses a spaced-repetition algorithm with review
logging, enrollment, per-deck analytics, and a stats surface. Cards built from a
lesson stay linked to their source and diff against it when the lesson changes.
Real per-card retention data feeds Today's Session.

### 6.8 Gamification
Shipped: achievements and badges across criteria types (questions answered,
perfect scores, day streaks, lessons completed, subject mastery, mock score
≥70%), streaks, and progress toward locked badges, with achievement icons.
**Planned:** a learning-first enhancement (XP and levels, improvement-weighted
rewards, no pay-to-win, no shaming streak loss), specified in
`scholarscrib-gamification-implementation-plan.md` and not yet built.

### 6.9 Library
Curated per-subject resources — textbooks, videos, PDFs, worksheets, past papers
— with free/premium flags and an in-app PDF reader. Authenticated files are
served through signed, short-lived Cloudinary links. Admins manage subject
materials from the console.

### 6.10 JAMB Subject-Combination Guidance
Reference data for required JAMB subject combinations per course and faculty,
plus approximate cut-off tiers (140–280+) by course competitiveness.

### 6.11 Administration
A full admin console on an isolated identity (own secret and cookie scope, so a
leaked student secret cannot forge admin tokens). It provides question
authoring and bulk import, a question-coverage tab with paper preview and
downloads, lesson upload and browsing, subject-material management, student
list and detail with filters and pagination, profile editing, subscription-tier
override and comps, suspension, owner-only force sign-out and deletion, an admin
team page, announcements, an audit log viewer, and **admin Astra** — an
assistant for operators with proposal-and-approve execution.

### 6.12 Astra, the AI Study Assistant *(beta)*
A curriculum-grounded study assistant available inside the app. It uses
retrieval over ingested curriculum content with reranking, streams answers to
the client, keeps chat sessions and history, applies guardrails and per-user
limits, and can propose actions (such as study timetables) that the student
approves before anything changes. It supports Teach and Diagnose by explaining
concepts, answering questions, and helping with revision. **Status:** shipped as
a beta feature, open to every tier during the beta. General release timing and
tier placement are open (section 14).

### 6.13 Notifications, PWA & Public Surface
Web push and in-app notifications with study reminders and admin announcements,
driven by scheduled jobs. A PWA shell (manifest, offline page) installs the app
on a phone. A public, indexable surface (`/learn` subject hubs and topic pages,
`/past-questions`, about, contact, terms) with sitemaps, structured data and
`noindex` on every gated route brings organic traffic in; roughly 137 pages were
sitemapped at the time of the SEO hand-off.

## 7. Technical Architecture

The product is now two deployables. The Next.js application used to own the
database; since the backend port it is a **pure client** of a FastAPI service
that owns auth, the database, scoring, study plans, billing, push delivery and
the AI tutor. The frontend holds no database access and no backend secret.

| Layer | Choice |
|---|---|
| Backend framework | FastAPI on Python 3.12 (`scholarscrib-api`), Scalar API reference |
| Database | PostgreSQL on the same schema, SQLAlchemy 2 (async, asyncpg), Alembic migrations |
| Cache / rate limits | Redis |
| Auth | Backend-issued JWTs, separate student and admin realms; Google OAuth code exchange; bcrypt |
| AI | LangChain / LangGraph agents with Postgres checkpointing, Milvus-backed RAG with reranking, Anthropic and OpenAI models, guardrails and human-in-the-loop proposals |
| Background work | Cron endpoints and background tasks (push reminders, RAG ingest, provider ingest) |
| Push | Web push (VAPID) via `pywebpush` |
| Frontend framework | Next.js 16 (App Router), React 19 |
| Client state | TanStack Query, Zustand, axios, Zod 4 |
| Styling and UI | Tailwind CSS v4, lucide / react-icons, Recharts, KaTeX |
| Media | Cloudinary (avatars, mirrored question images, signed library files), react-pdf |
| Payments | Paystack (hosted checkout, signed webhook) — test mode |
| Tests | Backend: pytest (~50 modules). Frontend: Node test runner over tsx (70+ scripts) |

**Architectural principles worth stating.** Business rules are kept
database-free and unit-testable — the subscription tier union, curriculum scope,
and grading boundaries are declared in plain modules, so rules can be tested
without a database. The learning evidence ledger is append-only and mastery is a
decayed fold of it; changing the scoring version replays history rather than
editing it. Every subsystem lands through a written design document and an
implementation plan committed to `docs/superpowers/` (in both repositories), and
the port is documented as a contract in `docs/backend-port/`.

## 8. Competitive Landscape & Advantage

### 8.1 The Market
| Competitor | Focus | Gaps ScholarsCrib addresses |
|---|---|---|
| **Pass.ng** | Past questions + mini-lessons | Practice-only; no personalised study plan, no curriculum weighting, limited analytics |
| **TestDriller** | Offline CBT software (per-device licence) | Paid, device-bound; no web analytics or plan generation |
| **Myschool.ng / Flashlearners** | Past questions + news/info | Content portal; weak learner analytics, no grading-simulation depth |
| **ClassNotes.ng** | Lesson notes | Notes only — no practice, timing, or performance tracking |
| **Local CBT centres (EduTams, etc.)** | Exam-hall mock CBT | Expensive, location-bound, one-off sittings; no continuous learning loop |
| **Video-led learning apps (e.g., uLesson)** | Curriculum video lessons | To be benchmarked. Assumed lighter on past-question depth, exam-format simulation, and evidence-based mastery. Verify before external use |
| **International apps (Khan Academy, Quizlet, Anki)** | Generic learning | Not syllabus/exam-aligned to WAEC/JAMB/NECO; wrong grading scale; not Nigeria-specific |

### 8.2 What Makes ScholarsCrib Different
1. **Fully Nigerian by design.** A1–F9 grading, JAMB 180q/120min/400-mark
   configuration, cut-off tiers, 36 states plus FCT, and the NERDC subject list
   across Science/Arts/Commercial/Vocational tracks — none of it an afterthought.
2. **The closed loop.** Teach → Test → Diagnose → Plan, as described in section
   5. Competitors cover one or two stages, not the loop.
3. **Exam-weight-aware studying.** WAEC/JAMB topic weightings let the plan and UI
   prioritise high-yield topics — a feature no mainstream Nigerian app exposes.
4. **An evidence layer, not a scoreboard.** An append-only, timestamped,
   difficulty-tagged ledger of every answer, lesson checkpoint, and card review,
   folded into mastery with recency decay and confidence floors. This is what
   makes "you are weak here" defensible rather than a wrong-answer tally.
5. **Full official-format simulation.** A complete 180-question, 4-subject,
   timed, tabbed JAMB CBT that mirrors the real interface, including focus-loss
   tracking — not just a Q&A drill.
6. **One subscription, whole journey.** Covers learning from SS1 through exam
   day and any resit, and replaces two to four separate paid products with a
   single platform.
7. **An owned content pipeline.** The provider cache captures every third-party
   call permanently into our own database. Once a paper is drawn once, we hold
   it forever, so the provider is not a permanent dependency, cost, or outage
   risk.
8. **Learning quality, not just pass rates.** Retention evaluation, mastery
   decay, and spaced repetition are built to produce students who understand and
   remember, not students who only pass.
9. **A tutor that acts on your plan.** Astra is grounded in the curriculum and
   can propose changes to a student's study timetable for them to approve, which
   a stand-alone chatbot cannot do.

---

# Delivery Detail

## 9. Build Status Ledger

State definitions: **Shipped** — on `main`, tested, in use. **Beta** — shipped
and reachable, behind a beta flag or open tier, scope still being proven. **In
progress** — being built or filled now. **Seam only** — the interface and data
model exist and are tested, but the behaviour behind them is deliberately
undefined. **Planned** — designed or scoped, no code.

### 9.1 Shipped

| Subsystem | Evidence |
|---|---|
| Accounts, auth, profiles, settings, forgot/reset password, Google sign-in | `2026-07-27-settings-and-profile-design.md`; auth routes in `app/api/student/auth.py` |
| Curriculum, lessons, classroom (class and term split) | `2026-08-01-lesson-engine-design.md`, `2026-08-05-classroom-design.md` |
| Lesson note upload + natural note format | `2026-08-05`, `2026-08-06` specs; admin lesson import |
| Question bank, importer, ALOC provider ingestion and cache | `2026-09-01-question-provider-cache-design.md`, `2026-09-07-multi-provider-ingestion-design.md`; `aloc_question_provider`, `provider_fetch_checkpoint` migrations |
| Practice, past questions, JAMB CBT, mock exams, topic quizzes, practice exit | `app/services/assessments/`, `practice_exit.py` |
| Attempt lifecycle, timing, abandonment, exam focus tracking | `2026-08-26-exam-focus-tracking-design.md` |
| Learning-path engine + knowledge graph | `2026-08-02-learning-path-engine-design.md` |
| Learning evidence layer, topic mastery with decay, topic standing | `2026-08-11`, `2026-08-12` specs; `app/services/learning/` |
| Performance analytics (subject, exam, progress lenses) | `2026-08-28-performance-analytics-design.md`; `app/services/analytics.py` |
| Flashcards, spaced repetition, build-from-lesson with source diffing | `2026-08-01-flashcards-design.md`, `2026-08-25-flashcard-build-from-lesson-design.md` |
| Study plan: term, blended and exam modes; class/term scope; replanning; delete | `2026-09-14-study-plan-term-mode-design.md`, `2026-10-15-study-plan-scope-design.md` |
| Configurable study timetable | `2026-10-25-configurable-study-timetable.md`; `study_plan_timetable` migration |
| Today's Session | `2026-10-12-todays-session-design.md`; `daily_session` migration |
| Achievements, streaks, library, subject materials | `2026-09-10-subject-materials-design.md`; signed Cloudinary delivery |
| Push and in-app notifications, announcements | `2026-09-14-push-notifications-design.md`; `in_app_notification` migration |
| PWA shell, device limit | `2026-09-11-pwa-shell-design.md`, `2026-09-13-device-limit-design.md` |
| Public SEO surface | `2026-09-09-seo-public-surface-design.md` |
| Admin console, isolated identity, audit log, student administration, structure and navigation | `2026-08-26-admin-identity-design.md`, `2026-08-27-admin-console-structure-design.md` |
| Admin question-coverage tab (preview, export) | `2026-10-20-admin-question-coverage-design.md`; `coverage_question` migration |
| Backend port to FastAPI; frontend as a pure API client | `docs/backend-port/` (13-part contract) |
| Billing and tier gating (Paystack **test** integration) | `2026-09-04-paystack-billing-design.md`; see 9.2 |

### 9.2 In progress

| Item | State |
|---|---|
| **Lesson notes for all major subjects** — every subject, topic and term against the government-approved curriculum | **Launch priority one.** Upload is in progress; per-subject coverage is not yet reported in the product, which is itself a gap (9.4) |
| **Paystack to live** — test integration is running; pricing is being defined | Blocked on the pricing decision (section 14, decision 2). Gating of the four boolean entitlements is built |
| **End-to-end testing** of CBT, flashcards and progress tracking | Scheduled in the 30-day push |
| **Launch campaign** with marketing | Scheduled in the 30-day push |

### 9.3 Beta, seam only, and planned

| Item | State | Notes |
|---|---|---|
| **Astra AI tutor (student)** | Beta | Open to every tier during the beta. General release is scoped for after launch. Still to settle: tier placement, quota, safeguards review |
| **Admin Astra** | Beta | Operator assistant with proposal-and-approve execution |
| **Repair stage** (hints, misconception feedback, targeted retry) | Planned | Draft design `2026-10-09-repair-stage-design.md`; no shipped surface verified |
| **Gamification enhancement** (XP, levels, improvement-weighted rewards) | Planned | `scholarscrib-gamification-implementation-plan.md`; existing achievements page is the starting point |
| **Usage-based tier limits** | Seam only | `ENTITLEMENTS` and `can()` exist and the boolean gates ship. No usage metering, so the Free tier's advertised caps (3 subjects, 25 questions a day, 1 mock exam) are unenforced |
| **Teacher role** | Seam only | `Role.TEACHER` exists; deliberately rejected at registration ("coming soon") |
| **Video lessons** | Planned | Topic page is designed for notes plus video; sourcing open |
| **Teacher portal, marketplace, school portal** | Planned | Roadmap in section 10 |

### 9.4 Known gaps

| Gap | Impact |
|---|---|
| **Lesson-note coverage is incomplete** | The product promises "every subject, every topic, every term". Until notes exist for all major subjects, the Teach stage is thinner than Test, Diagnose and Plan, and learning claims outrun the product. **This is the critical path to launch.** |
| **No coverage reporting for lessons** | The admin console reports question coverage per subject and exam, but there is no equivalent view for lesson notes, so completeness cannot be measured or reported. |
| **Question-bank figures not re-verified in this revision** | The ~13,000 / 20-year / three-exam-body figures come from the team onboarding deck (2026-10-26). Confirm per-subject and per-year coverage from the admin coverage tab before quoting externally. |
| **Free-tier numeric caps unenforced** | Landing page advertises limits the code does not enforce; safe direction (overstates the limits on Free) but inaccurate. |
| **Landing page advertises features that do not exist as described** | Premium column lists offline mode (not built, deferred). The AI tutor now exists as a beta; its tier and wording must match reality. |
| **`PerformanceMetric.masteryLevel` is stale** | Written by topic-practice and pre-test paths only, never by ordinary question answering. The evidence layer is the source of truth. |
| **Backend README is empty** | The frontend README is current; the backend's is a zero-line file. Onboarding cost for anyone new, and the weak link in the single-maintainer story (section 15). |
| **Phone OTP and transactional email not wired** | `TERMII_*` and `RESEND_API_KEY` settings exist but are unused per the port contract; `emailVerified` is never set by application code. Confirm what the forgot-password flow sends through before launch. |
| **No git tags or version bumps** | Both manifests still read `0.1.0`. |
| **No video lessons** | Topic pages carry notes only. |

## 10. Scope and Roadmap

### Launch scope (November 2026)
1. **Complete lesson coverage.** Notes for all major subjects, every topic, every
   term, following the government-approved curriculum. Priority one.
2. **Finalise pricing and take Paystack live.** Move from test to live keys,
   publish pricing, and reconcile the landing page with what each tier unlocks.
   Boolean entitlements are decided and built: flashcards and the study planner
   need STANDARD; premium library resources and the subject-level analytics
   breakdown need PREMIUM. During the beta, advanced analytics and Astra are
   open to every tier. **Corrects v0.3**, which described billing as active;
   it is built and tested, not live.
3. **Test end to end.** CBT, flashcards, progress tracking, onboarding, and the
   payment flow.
4. **Launch campaign** with marketing: exam readiness leads, learning quality
   follows (sections 4 and 15).
5. **Scope the AI tutor general release** for after launch, including tier,
   quota and safeguards.

### Roadmap
| Horizon | Goal | What it means |
|---|---|---|
| **Nov 2026** | Public launch | Open ScholarsCrib to students with notes, practice, flashcards, progress tracking, the study planner and Astra beta |
| **Year 1** | Lead exam prep | Become the leading exam practice and preparation platform for Nigerian students, and the place where students study and are groomed for excellence. Candidates: Astra general release, video lessons, usage-metered Free tier, gamification enhancement, repair stage, PWA/offline depth |
| **Year 3** | Fully digitised | Not an alternative academic platform: the platform that has fully digitised the senior secondary academic system, through the three portals below |

**The three portals (post-launch sequence).**
1. **Teacher portal.** Generate lesson notes from curriculum or textbook;
   generate tests and exam questions from the question bank; upload videos.
2. **Marketplace.** Teacher-recorded lessons, exam pins, scholarship
   applications.
3. **School portal.** Schools subscribe to a plan, create teacher accounts per
   subject, onboard students, run CBT exams as school term exams. Structure:
   School → Teachers → Class → Students.

### Deferred beyond launch
- **Video lessons on topic pages.** Timing and sourcing open (section 14).
- **Teacher portal, marketplace, and school portal.** Have their own consent,
  access, and pricing questions.
- **Offline and low-bandwidth mode** beyond the current PWA shell.
- Live proctoring, official exam registration, JAMB portal integration.
- Post-secondary content (post-UTME, GMAT-type).
- Parent portal and cohort/peer comparison — both ruled out in the analytics
  design for defensible reasons.

## 11. Timeline

Anchored on a **November 2026 public launch**, working from the date of this
revision (2026-10-26), roughly five weeks out. The April 2027 JAMB is the next
major exam date the exam-prep entry offer is aimed at.

| Milestone | Window | Goal | Status and exit criteria |
|---|---|---|---|
| **M1 — Land in flight** | Sep 2026 | Clear the branch backlog; finish ingestion | **Done in substance.** Analytics, admin structure, billing and provider work are on `main` in both repos. A few short-lived feature branches remain locally; prune them |
| **M2 — Fill the bank** | Sep–Oct 2026 | Turn the pipeline into content | **Done for questions** (ALOC, ~13,000 topic-tagged). Topic-tagging was solved by the provider's tagging rather than a separate workstream. Per-subject coverage to be confirmed from the coverage tab |
| **M3 — Hardening** | Oct–Nov 2026 | Safe to put in front of real students | **In progress.** Remaining: end-to-end test of CBT, flashcards, progress; content QA pass; rate-limit and abuse review; error monitoring; onboarding tested end to end |
| **M4 — Lesson coverage** | 26 Oct – late Nov 2026 | Notes for every major subject, topic and term | **Active, priority one.** Exit: per-subject lesson coverage reported and signed off for the launch subject set |
| **M5 — Launch** | Nov 2026 | Public launch with exam preparation as the entry offer | Pricing final; Paystack live; launch campaign running; metrics instrumented (section 13); `0.9.0` tagged |
| **M6 — Post-launch** | Dec 2026 – Q1 2027 | Learn from real usage and be ready for April 2027 JAMB | Free-tier caps metered; Astra general-release decision; beta findings addressed; `1.0.0` |

**Critical path: M4.** Lesson notes are now the long pole. Question content,
the learning engines and the platform are ready. If M4 slips, launch with the
subject set whose notes are complete and say so, rather than moving the date or
shipping thin topic pages. Cut subject breadth before cutting the M3 hardening
pass.

## 12. Version Scheme

Semantic versioning, with the pre-1.0 range carrying explicit meaning so that
release state is never ambiguous. Both `package.json` and `pyproject.toml`
currently read `0.1.0`; they should be bumped together and tagged in git at each
milestone boundary. There are currently no git tags in either repository.

| Version | Meaning |
|---|---|
| `0.1.0` | **Today.** Feature-complete for launch scope, content in progress. |
| `0.9.0` | **Launch candidate.** Lesson coverage signed off for the launch subject set, end-to-end tests green, Paystack live, pricing final. Patch releases `0.9.x` during the launch window. |
| `1.0.0` | **Stable public release.** Launch findings addressed; Free-tier usage caps metered. |
| `1.x` | Post-launch: Astra general release, video lessons, gamification enhancement, then portals. |

## 13. Success Metrics

**Launch targets (M5)** — the first weeks after launch are a learning exercise,
so these are diagnostic thresholds rather than growth goals:

| Metric | Target | Why |
|---|---|---|
| Activation — registered users completing a first practice session | ≥60% | Below this, onboarding or content breadth is broken |
| Week-2 retention | ≥40% | The loop either pulls students back or it does not |
| Practice attempts per active user per week | ≥3 | Enough signal for the evidence layer to produce non-trivial mastery |
| Students with enough data for a confident verdict | ≥50% by week 4 | Tests whether the evidence layer's confidence floors are set right against real usage |
| Content faults reported per 1,000 questions served | Tracked, no target | Establishes the content-quality baseline |
| Retention — share of reviewed cards and topics still recalled after 7 and 30 days | Tracked, no target | The core learning-quality signal |
| Classroom engagement — topics completed per active user per week, by class level | Tracked, no target | Shows whether SS1 and SS2 students use the platform outside exam season |
| Class-level mix of active users (SS1 / SS2 / SS3 / resit) | Tracked, no target | Tests whether exam-prep entry marketing draws in learners beyond SS3 |
| Today's Session follow-through — share of sessions with every item completed | Tracked, no target | Tests whether the daily queue is followed and helps (recorded per item) |
| Astra usage and helpfulness — chats per active user, quota hit rate | Tracked, no target | Informs the general-release decision |
| Lesson-note coverage — topics with notes ÷ topics in curriculum, per subject | 100% for the launch subject set | The launch gate (M4) |

**Post-launch (v1.0+):** accuracy improvement between first and last attempt;
free-to-premium conversion; 7-day streak retention; reach per school and state.

## 14. Open Decisions

| # | Decision | Options | Needed by |
|---|---|---|---|
| 1 | ~~Payment provider~~ | **Decided: Paystack.** Test integration running. Remaining work is the move to live keys | Done; go-live before launch |
| 2 | **Pricing and what each tier unlocks** | Boolean gates decided (section 10). **Open:** price points per tier, and whether the Free tier's numeric caps (3 subjects, 25 practice questions a day, 1 mock exam) are enforced at launch or the landing page is reworded to match what is gated | Before Paystack goes live |
| 3 | **Launch cohort and support model** | Public launch is the stated plan. Decide support load, onboarding for schools and parents reaching in, and whether any students are comped | Before launch campaign |
| 4 | ~~Topic-tagging approach~~ | **Resolved** by provider-tagged ALOC questions. Residual: spot-check tagging accuracy in the content QA pass | Done; QA in M3 |
| 5 | **Launch subject breadth** | Full set of major subjects vs. the subject set whose notes are complete by launch. The latter is the fallback in section 11 | Mid-November |
| 6 | ~~Shape of the November launch~~ | **Decided: public launch**, per the team deck | Done |
| 7 | **Astra scope, timing and tier** | Remains beta for launch, or generally released. Tier placement, quota, safeguards review, and whether it stays open to every tier | Before the pricing page is finalised |
| 8 | **Video lessons: timing and sourcing** | Later release. Who records and hosts videos, whether teachers upload them, and how they relate to the marketplace | Before video planning |
| 9 | **Gamification enhancement and repair stage: sequencing** | Both are specified and unbuilt. Which, if either, precedes the Year-1 roadmap items | Post-launch planning |
| 10 | **Transactional email and OTP** | Wire Resend and/or Termii vs. launch without email verification. Affects account recovery and trust | Before launch |

## 15. Risks & Assumptions

| Risk | Severity | Mitigation |
|---|---|---|
| **Lesson coverage not complete by launch** | High | It is the critical path (section 11). Track coverage per subject; launch with the complete subject set and be explicit about the rest rather than shipping empty topic pages |
| **Positioning outruns the product** | High | Launch messaging leads with exam preparation, which the product can deliver today. Broader "fully digital senior secondary journey" claims are made only as fast as they become real: notes coverage, videos, AI tutor. Track lesson-note coverage alongside question coverage |
| **Fixed November launch against content and payments** | High | See section 11. Do not move the date or ship untested content or an untested payment path; narrow the subject set instead |
| **Paystack go-live untested with real money** | High | Run the full checkout, webhook and settlement path end to end on live keys with a small internal transaction before the campaign starts; the webhook uses a signed raw-body check and must not be re-serialised |
| **AI tutor safety and cost** | Medium | Guardrails, per-user limits and human-approved proposals exist. Review prompts and refusal behaviour against a student audience, watch model spend while it is open to every tier, and decide tier placement before pricing is final |
| **Content quality of ingested questions** | Medium | Provider yield measured at 87% with promotion rules that reject incomplete rows; a human QA pass should sample ingested and tagged questions before launch. Do not market as exam-grade until validated |
| **Third-party provider dependency** | Medium | The cache design makes the dependency temporary by construction, with a circuit breaker and rate budget. Residual risk is any request that misses the cache at runtime |
| **Two-service operational surface** | Medium | Backend, frontend, Postgres, Redis, a vector store and background jobs must all be healthy. Add error monitoring and uptime checks; confirm cron secrets are set (an unset secret makes cron endpoints no-op) |
| **Data and device access among low-income students** | Medium | PWA shell shipped; deeper low-bandwidth and offline work deferred and should be informed by real telemetry |
| **Exam-body branding and trust** | Medium | Must be transparent throughout that ScholarsCrib is a practice tool and not affiliated with WAEC, JAMB, or NECO. Review copy before launch |
| **Small-team bus factor** | Medium | Mitigated by the design-and-plan discipline in `docs/superpowers/` and the backend-port contract. The empty backend README is the weak link |
| **Learning-quality claims are hard to prove** | Medium | Instrument retention, Today's Session follow-through and mastery movement from day one so claims are backed by data |
| **Local branch clutter** | Low | Prune merged feature branches and tag `0.9.0` at the launch candidate |
