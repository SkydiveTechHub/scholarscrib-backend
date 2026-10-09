# Repair Stage: Hints, Misconception Feedback and Targeted Retry — Design

**Date:** 2026-10-09
**Status:** Draft, pending review

## 1. Problem

The loop is Teach → Test → Diagnose → Plan. When a student gets a question wrong, the product records it and, later, may route them back to a lesson. Between "wrong" and "lesson" nothing helps the student fix the specific mistake.

- Feedback is a static `Question.explanation`, shown after the answer (`explanation-modal.tsx`). It is the same text whichever wrong option the student picked.
- `LearningEvent` records `correct: bool`, `difficulty` and `seconds`. It cannot tell a misconception from a slip or a lucky guess.
- Gap classes (WEAK, DECAYED, BOTTLENECK, ABANDONED, UNTOUCHED) say *where* a student is weak. They do not say *what they misunderstand*.
- There is no hint before the answer, so a stuck student either guesses or skips.
- A mistake is not re-tested at a spaced interval. Spaced repetition only covers optional flashcards, which are gated to STANDARD.

Result: the product finds weakness but does not repair it. Research on feedback and retrieval practice says repair is where most learning gains come from.

## 2. Goals

1. A wrong answer in practice produces **option-specific feedback**: why this option is tempting and why it is wrong.
2. A stuck student can ask for up to **two hints** before answering, at a small mastery cost.
3. A student can record **confidence** before submitting, so the system can tell confident-wrong (misconception) from unsure-wrong (gap).
4. Each mistake becomes a **repair item**: a short micro-lesson plus a retry on a *different* question for the same misconception, resurfaced at spaced intervals until it sticks.
5. Repair evidence feeds the existing mastery model without breaking it.
6. It degrades gracefully while most of the bank is untagged: topic-level repair works with no misconception data.

**Non-goals:** AI tutoring or free-text chat, scoring theory or essay answers (a separate spec), teacher/parent views, push reminders for repair items, changing exam, CBT and mock-exam behaviour (see 3.1), changing the subscription tiers (see 10).

## 3. Decisions made

| Question | Decision |
|---|---|
| Where it applies | Practice, topic quizzes, lesson quick-quizzes and pretests. **Never** inside timed exam sessions (JAMB CBT, mock exams, past-paper timed mode). Review after submission only |
| Misconception data | A curated catalogue per topic, attached to wrong options. Drafted with model assistance, **always reviewed by an admin** before it reaches students |
| When data is missing | Fall back to topic-level repair: question explanation plus the topic's key points, and a retry from the same topic |
| Hints | Up to 2 per question, stored on the question. Hint use discounts the mastery outcome; it is never blocked |
| Confidence | Optional 3-level rating (Guessing / Fairly sure / Certain), asked before submit in practice modes, skippable |
| Retry scheduling | Within the session (after 2–3 other questions), then +1 day and +4 days. A separate small queue, not flashcards |
| Mastery integration | New event kinds; repair never double-penalises the original mistake. `SCORING_VERSION` bump to 4 |
| Architecture | New `repair` service beside `learning` and `srs`. It reads the ledger and writes repair events; mastery folds them like other events |

### 3.1 Why not in exams

Hints and mid-attempt feedback invalidate exam simulation and the predicted-grade bands. Exam sessions keep today's behaviour. Wrong answers from an exam attempt still create repair items **after submission**, from the review screen.

## 4. Data model

Migrations follow the existing Alembic flow, with the catalog checked after applying.

### 4.1 `Misconception` (new)

| Field | Type | Notes |
|---|---|---|
| `id` | cuid | |
| `topic_id` | FK `Topic` | required |
| `label` | String | short, e.g. "Confuses mass with weight" |
| `explanation` | Text | what the student believes and why it is wrong, 1–3 sentences |
| `micro_lesson_md` | Text | markdown with LaTeX, aim for under 2 minutes of reading; may include one worked example |
| `status` | enum `DRAFT \| APPROVED \| RETIRED` | only `APPROVED` is served |
| `created_by`, `reviewed_by`, `reviewed_at` | | audit trail; admin actions write the existing audit log |

Unique on `(topic_id, label)`.

### 4.2 `QuestionDistractor` (new)

Links a wrong option to a misconception and its feedback.

| Field | Type | Notes |
|---|---|---|
| `question_id` | FK `Question` | |
| `option_key` | String | matches the key used in `Question.options` and `selected_answer` |
| `misconception_id` | FK `Misconception`, nullable | null = "just wrong, no named misconception" |
| `feedback` | Text | shown when this option is picked, 1–2 sentences |

Primary key `(question_id, option_key)`. The option must not be the `correct_answer`; the importer and admin API enforce this.

### 4.3 `Question` (changed)

- Add `hints JSONB` (list of 0–2 strings, ordered from nudge to near-answer). Null means no hints; the Hint button is hidden.
- `options` and `explanation` are unchanged.

### 4.4 `RepairItem` (new)

One row per student per unresolved mistake.

| Field | Type | Notes |
|---|---|---|
| `id` | cuid | |
| `student_id` | FK | |
| `topic_id` | FK | always set |
| `misconception_id` | FK, nullable | null for topic-level repair |
| `origin_question_id` | FK | the question that was missed |
| `kind` | enum `CONFIDENT_WRONG \| UNSURE_WRONG \| WRONG` | from confidence at the time of the miss |
| `state` | enum `OPEN \| LEARNED \| RESOLVED \| DROPPED` | |
| `streak` | Int | consecutive correct retries |
| `due_at` | datetime | next retry |
| `attempts` | Int | |
| `created_at`, `resolved_at` | | |

- Unique open item per `(student_id, misconception_id)` when the misconception is set, else per `(student_id, topic_id)`. A repeat of the same mistake **updates** the existing item and resets `streak`; it does not create a duplicate.
- An item is `RESOLVED` after 3 correct retries on 3 different questions across at least 2 different days (matches the schedule in 6.3).
- An item is `DROPPED` if it is untouched for 60 days (it falls back to normal decay in the mastery model) or the student dismisses it.

### 4.5 `LearningEvent` (changed)

New `LearningEventKind` values:

| Kind | Meaning |
|---|---|
| `HINT_USED` | `source_id` = question id; `score` = hint number (1 or 2) |
| `REPAIR_LESSON_VIEWED` | micro-lesson opened to the end; `source_id` = misconception or topic id |
| `REPAIR_RETRY` | retry answered; `correct`, `difficulty`, `seconds`, `source_id` = repair item id |

Add a nullable `confidence` column (smallint 0–2) on `LearningEvent`, set on `QUESTION_ANSWERED` and `REPAIR_RETRY`. Add a nullable `selected_option` (String) on `QUESTION_ANSWERED`, so the ledger records *which* wrong option was chosen without joining attempt tables.

## 5. Learning-evidence integration

Constants live in `learning/evidence.py` next to the existing ones.

| Rule | Value | Rationale |
|---|---|---|
| Hint 1 used, then correct | outcome × 0.75 | Partial credit; scaffolded success is weaker evidence |
| Hint 2 used, then correct | outcome × 0.5 | |
| Hint used, then wrong | normal wrong outcome | Do not double-penalise |
| `REPAIR_RETRY` correct | same as a normal correct answer, weight 1.0 | It is real retrieval after delay |
| `REPAIR_RETRY` wrong | counts as a normal wrong answer **only if** the student has viewed the micro-lesson; otherwise weight 0.5 | Retrying cold is not strong evidence |
| `REPAIR_LESSON_VIEWED` | no mastery effect | Reading is not evidence of learning |
| Confident-wrong | no extra penalty in mastery | Used for classification and prioritisation, not scoring, to keep scoring explainable |

- `REPAIR_RETRY` flows through the existing `acc` channel. No new channel.
- Bump `SCORING_VERSION` to 4 to force a refold, since `fold_events` already resets aggregates on a version change.
- `is_mastered` still needs `MASTERY_MIN_QUESTIONS` (7) observations. Repair retries count towards that, so a student who repairs a topic can reach mastery.

### 5.1 Gap and recommendation changes

- New gap category **MISCONCEPTION**: an open `CONFIDENT_WRONG` repair item for a topic, or 2+ open items for the same misconception. It sorts ahead of WEAK, because a firmly held wrong belief is the most expensive kind.
- `recommendation_score` gains a term for open repair items (`0.10 * repair_pressure`, rebalanced from `freshness` and `readiness`; weights remain a sum of 1.0). Exact weights are tuned against beta data.
- The study planner treats open repair items due today as "revision" slots; it schedules them first and never exceeds the student's daily minutes (consistent with the term-mode spec, which already limits plan load).

## 6. Behaviour

### 6.1 Answering (practice modes)

1. Student sees a question. Optional **Hint** button (hidden if `hints` is null) reveals hint 1, then hint 2. Each reveal writes `HINT_USED`.
2. Student picks an answer. Optional confidence chips appear before **Submit** (skippable, default unset).
3. On submit the answer is graded as today, and `QUESTION_ANSWERED` is written with `selected_option` and `confidence`.

### 6.2 After a wrong answer

1. Show the **option-specific feedback** from `QuestionDistractor` if present, otherwise the question explanation.
2. Show **"Fix this"** (primary) and **"Skip"** (secondary). Skip still creates the repair item so the miss is not lost.
3. "Fix this" opens the micro-lesson (misconception's, or the topic's key points when there is no misconception). Reaching the end writes `REPAIR_LESSON_VIEWED`.
4. The `RepairItem` is created or updated (4.4). `due_at` is set for an in-session retry.

For a wrong answer in an exam session, step 1–3 happen from the review screen after submission instead.

### 6.3 Retry schedule

| Retry | When | Question choice |
|---|---|---|
| 1 | In the same session, after 2–3 other questions | Different question, same misconception, else same topic and difficulty |
| 2 | +1 day | Different from retry 1 and the origin question |
| 3 | +4 days | Different from earlier ones |

- A wrong retry shows feedback again, resets `streak` to 0 and moves `due_at` back to the start of the schedule. After 3 failed cycles, the item stays open but the UI suggests re-reading the topic lesson and the planner schedules it as a lesson slot.
- The question picker never serves the same question twice for one repair item unless the pool is exhausted, and then it prefers the one answered longest ago.
- If a misconception has fewer than 3 usable questions, the picker widens to the topic. This is the cold-start path, since most of the bank is untagged by misconception.

### 6.4 Where the student sees it

- **Practice result screen:** "You have N things to fix" with a button to start a repair session.
- **Dashboard:** a "Repair" card with the number due today. The card hides when there are none.
- **Performance page:** top misconceptions by frequency, and the confidence-vs-accuracy summary (confident-wrong rate per subject). It shows the "not enough data yet" verdict when below the confidence floor.
- **Repair session:** a short queue (default cap 10 items per session), each item as retry → feedback → next. No timer.

## 7. API surface

FastAPI routes under `app/api/student/`, using the existing auth dependencies.

| Route | Purpose |
|---|---|
| `POST /student/questions/{id}/hint` | Reveal next hint; writes `HINT_USED`; returns the hint text and remaining count |
| `GET /student/repair` | Summary: due count, open count, top misconceptions |
| `GET /student/repair/session` | Next batch of due items, each with the retry question (answer key **not** included) |
| `POST /student/repair/{id}/answer` | Submit retry; returns correctness, feedback, new schedule |
| `POST /student/repair/{id}/viewed` | Micro-lesson viewed |
| `POST /student/repair/{id}/dismiss` | Drop an item |

Existing answer-submission endpoints accept optional `confidence` and return `distractorFeedback`, `repairItemId` and `microLesson` fields on wrong answers.

Admin routes (under the isolated admin identity, audited):

- CRUD for `Misconception` including approve and retire.
- Attach or edit `QuestionDistractor` and `hints` on a question.
- A review queue of `DRAFT` misconceptions with the questions that reference them.

## 8. Content pipeline

The feature is only as good as the catalogue, so this is a first-class workstream.

1. **Seed from data.** For questions already in the bank, list the most frequently chosen wrong options per question (from attempts) to prioritise authoring where it matters.
2. **Draft.** Model-assisted drafts of misconception label, explanation, feedback and hints for a question or a topic's top wrong options. Drafts land as `DRAFT` and are never shown to students.
3. **Review.** An admin approves, edits or rejects in the console. Subject experts review Maths and sciences first. Target: 100% of served misconception text is human-reviewed.
4. **Prioritise.** Start with high-weight topics (WAEC/JAMB weighting) in 3 subjects for beta, e.g. Mathematics, Physics, Chemistry, rather than breadth.

Depends on the topic-tagging workstream (PRD decision 4): untagged questions cannot carry distractor data or create topic-level repair items. Questions with no topic can still show the generic explanation and hints.

## 9. Offline and low-data

- Micro-lessons are plain markdown and a few KB. Repair sessions must be cacheable: the session endpoint returns the batch with its lessons so a student can complete it without further requests.
- Answers and events are queued and synced when online (consistent with the PWA shell). Server time is authoritative for `due_at`; the client sends `occurred_at` and the server clamps it.
- No images required by default. Misconception lessons may reference one Cloudinary image, which should be optional and cached.

## 10. Entitlements (open)

Recommended default, to confirm in review:

- **Free:** option-specific feedback, hints, and a repair queue capped to a small number of items per day (e.g. 10).
- **STANDARD+:** unlimited repair sessions, the misconception breakdown in performance, and repair slots in the study plan.

Rationale: diagnosis without repair is the main gap in the free product, and the PRD already notes that gating Diagnose and Plan leaves free users with only a practice app. This must be decided alongside the usage-metering work, since the caps need metering that does not exist yet.

## 11. Metrics

| Metric | Target for beta | Why |
|---|---|---|
| Fix rate: wrong answers where the student opens the micro-lesson | ≥40% | Is "Fix this" attractive? |
| Retry accuracy at +1 day vs original accuracy on the same misconception | Tracked, expect improvement | Core learning signal |
| Resolution rate: items `RESOLVED` within 14 days | ≥50% | Does the schedule work? |
| Repeat-mistake rate: same misconception wrong again after `RESOLVED` | ≤15% | Is repair durable? |
| Hint use rate and correct-after-hint rate | Tracked | Are hints too easy or too hard? |
| Confidence calibration: accuracy at "Certain" | Tracked | Basis for the overconfidence feedback |
| Session abandonment in repair sessions | ≤25% | Is it too long or too heavy? |

Instrumented through the existing evidence ledger. No new analytics system.

## 12. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Wrong or misleading misconception text | High | Mandatory human review (section 8); `RETIRED` status; student "report this" link on every feedback panel |
| Catalogue too thin at beta | High | Topic-level fallback (6.3); start with 3 subjects; show the feature only where data exists |
| Repair feels like punishment and drives drop-off | Medium | Short sessions, a cap per session, an explicit Skip, and no streak penalty for unresolved items |
| Hints reduce the value of practice | Medium | Mastery discount (section 5); hints are optional and capped at 2 |
| Confidence prompt adds friction | Medium | Optional, one tap; measure skip rate and drop it if under ~30% use it |
| Scoring changes shift existing mastery | Medium | `SCORING_VERSION` bump and refold, tested on a copy of production-shaped data first |
| Untagged questions limit retry picking | Medium | Widens to topic; tagging workstream on the critical path already |

## 13. Phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **1: Feedback and hints** | `Question.hints`, `QuestionDistractor`, admin editing, option-specific feedback, hint button, `HINT_USED`, scoring discount | Works in practice mode on the pilot subjects; exam modes unchanged |
| **2: Repair queue** | `Misconception`, `RepairItem`, micro-lessons, in-session and +1/+4 day retries, repair session UI, dashboard card, `REPAIR_*` events, `SCORING_VERSION` 4 | A student can miss a question, fix it, and be re-tested over 4 days |
| **3: Confidence and diagnosis** | Confidence capture, MISCONCEPTION gap class, recommendation term, performance breakdown, planner slots | Gap list distinguishes misconceptions from weak topics |
| **4: Content scale** | Model-assisted drafting tools and review queue, bulk authoring | Catalogue covers high-weight topics in pilot subjects with reviewed text |

Phases 1 and 2 are the minimum to test the idea in beta. Phase 3 depends on enough data and on topic tagging.

## 14. Testing

- Pure functions (scheduler, hint discounts, resolution rules, gap classification) get unit tests with no database, in line with the project's existing approach.
- Fold tests: a ledger with hints and repair retries must produce the same state when replayed twice, and when folded incrementally.
- `SCORING_VERSION` 4 refold test: a v3 aggregate resets and refolds from the ledger.
- API tests: the answer key never appears in `GET /student/repair/session`; exam sessions never return hints or feedback before submission.
- Authorisation: a student can only access their own repair items and cannot reveal hints for questions outside an active practice attempt.

## 15. Open questions

1. Free vs paid split for repair (section 10).
2. Which 3 subjects for the pilot catalogue.
3. Who reviews misconception text: internal team, hired subject teachers, or both? Drives M2/M3 timing.
4. Confidence prompt: always on, or only for objective questions in topic practice?
5. Is a +1/+4 day schedule right for SS1–SS2 students on a term plan, or should it align to weekly lessons? To check against beta retry data.
