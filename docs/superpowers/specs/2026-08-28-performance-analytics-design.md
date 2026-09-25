# Performance Analytics — subject, exam, and progress

The Performance page today answers one question badly: "how am I doing?" It
shows four tiles, a per-subject accuracy bar, a weak-topic list ranked by raw
wrong-count, and the last ten attempts. There is no time dimension anywhere on
it, no notion of a syllabus, and no way to ask about a specific exam.

Meanwhile the evidence layer underneath it already carries everything the page
would need. `LearningEvent` is an append-only, timestamped, difficulty-tagged
ledger of every answer, lesson checkpoint and card review. `TopicMastery` is a
recency-decayed fold of it with real confidence floors. `gaps.ts` already
classifies topics into WEAK / DECAYED / BOTTLENECK / ABANDONED / UNTOUCHED
against the knowledge graph. The page simply never asks.

This design turns Performance into three analytical lenses over that ledger —
by subject, by exam, and over time — plus a seam for recommendations later.

## Goal

A student should be able to answer, without help:

1. **Where am I weak, and in what way?** Not "which topic have I got wrong most
   often" — that is a measure of how much they practised — but which topics are
   genuinely weak, which have decayed, and which they have never proven at all.
2. **How ready am I for the exam I am actually sitting?** Per exam, per subject,
   with a predicted grade band the student and a parent can both interpret.
3. **Am I actually improving?** With an answer that is allowed to be "you don't
   have enough practice for me to tell", and that is not fooled by a student
   drifting toward easier questions.

## Non-goals

**No parent portal.** `Role` has no `PARENT`, and adding one is a separate piece
of work with its own consent and access questions. What this design does commit
to is that every verdict is written as a plain sentence a parent could read over
a shoulder and understand — no un-labelled dials, no jargon. A shared summary
later should be an additional surface over the same `Insight[]`, not a rewrite.

**No recommendation engine.** Section 9 fixes the shape recommendations will
consume so that adding them is additive. Nothing schedules work in this pass.

**No new derived tables.** See section 1.

**No cohort or peer comparison.** "You are in the top 30% of SS3 students" needs
a defensible population and raises real fairness problems on a product where the
population is self-selected and small. Not now.

**No fixing `PerformanceMetric.masteryLevel`.** It is stale — written only by
`topic-practice-result.ts` and `pretest.ts`, never by answering questions — and
that divergence is documented in the Phase 2 evidence spec, section 6. This
design does not read it anywhere. Mastery comes from `TopicMastery` via
`computeTopicState`, always.

## Constraints

**Derive, do not duplicate.** The evidence-layer spec is explicit that
`TopicMastery` is a cache of a fold, not a second source of truth, and the Phase
2 change fixed the Performance page by *deleting* four projected columns rather
than adding more. This design follows that precedent: every figure here is
computed on read.

**Mobile first, and mobile-only-if-forced.** Our students are on phones. Rules
that bind every screen in this design:

- Tabs are a horizontally scrollable pill row, never a wrapping desktop tab bar.
- No tables on small screens. The topic breakdown is a card stack that becomes a
  grid from `sm:` up. A horizontally scrolling table is not an acceptable phone
  experience.
- No hover-only information. Anything a chart tooltip would say must also be
  readable from the chart's caption, because there is no hover on a phone. Where
  a chart would need a tooltip to be legible at 360px, it is replaced by a
  sentence and a bar.
- Verdict sentence plus three or four headline figures above the fold; profiles
  and long topic lists in collapsible sections, with the "fix this" group open
  by default.
- One chart per viewport width, fixed aspect ratio. `recharts` is already a
  dependency and already used in `components/flashcards/stats-dashboard.tsx`;
  follow whatever that file establishes rather than introducing a second
  charting idiom.

**Never show a number the evidence cannot support.** `CONFIDENCE_FLOOR`,
`OBSERVATION_FLOOR` and `lib/evidence-display.ts` already encode this project's
answer to that problem, and its stated reasoning — a count tells the student
what resolves the uncertainty, a hedged percentage invites them to anchor on the
number anyway. Every surface here uses that same module. No parallel convention.

## Design

### 1. Where the numbers come from

A new `src/engines/analytics/` of pure functions, and a new
`src/lib/analytics/` of per-view data loaders. Everything is computed at request
time from three sources:

- `LearningEvent` — for time series, difficulty strata, pacing and rapid-guess
  rates. Indexed `[studentId, seq]` and `[studentId, topicId, seq]`.
- `TopicMastery` via `computeTopicState(prisma, studentId, graph, now)` — for
  mastery, retention, confidence and observation counts.
- `AssessmentAttempt` joined to `Assessment` — for exam-condition scores, which
  are a property of a *sitting*, not of the evidence ledger. Indexed
  `[studentId, status, completedAt]`.

Materialised rollups were considered and rejected. Per-student volume is small —
a heavy student answering fifty questions a day for a year is roughly eighteen
thousand rows, grouped in one indexed query — so rollups buy a speed-up we do
not need, in exchange for a second source of truth that drifts the moment
`SCORING_VERSION` is bumped, a question's difficulty is corrected in the admin
console, or events are backfilled. The same reasoning that made the Phase 2 fix
a deletion applies here.

If a real student's page ever measures slow, the seam is clean: the view loaders
are the only callers of the engines, so a cache or a rollup slots in behind them
without any view changing. Measured, not assumed.

**One load per request.** A page renders several bands off the same graph and
the same `TopicStateMap`. The view loader fetches `loadGraph` and
`computeTopicState` once and passes the result down; bands never load for
themselves. `computeTopicState` already writes back only topics whose fold
advanced, so a page render that folds nothing writes nothing.

### 2. Routes and navigation

`/performance` becomes a shell with four segments, each its own route so they
load, stream and cache independently:

| Route | Purpose |
|---|---|
| `/performance` | **Overview** — readiness, trend verdict, and the two or three things to fix next |
| `/performance/subjects` | **By subject** — subject evaluation, then topic breakdown |
| `/performance/exams` | **By exam** — per-subject readiness for a registered sitting |
| `/performance/progress` | **Progress** — is the improvement real |

Overview is deliberately not a fourth dashboard. `/dashboard` answers "what do I
do today"; Overview answers "where do I stand". It is the only screen that mixes
all three lenses, and the only one written to be legible to a parent in ten
seconds.

Selected subject and selected sitting live in the URL as search params
(`?subject=physics`, `?exam=WAEC-2027`), not in component state, so a student can
share or bookmark the view and a back-button press does what they expect.

### 3. Cumulative topic evidence

Per the request: a topic's numbers fold every source together — topic quizzes,
subject tests, past papers, mock exams, CBT practice, and lesson checkpoints.
This needs no new work, because they are all already `LearningEvent` rows tagged
with `topicId`. Once an answer becomes evidence, the attempt that produced it
stops mattering for topic-level reporting.

The attempt still matters for the exam lens, where "58% on a timed full paper"
is a different claim from "58% accuracy across scattered practice". The two
lenses read different sources on purpose, and section 6 keeps them separate on
screen rather than averaging them into one number.

### 4. The subject view

Subject chips across the top, ordered weakest-first — the ordering is itself
advice. Then three bands.

**Band 1 — the subject verdict.** One plain sentence, not a dial:

> You're at 61% accuracy in Physics across 340 questions — a C. That's up from
> 54% last month.

Beside it four figures: accuracy, questions answered, topics covered (`n` of `m`
in scope), time invested (summed `LearningEvent.seconds`, not
`StudentProgress.timeSpentMinutes`, so the figure counts answering time and
agrees with the pacing read in Band 3).

**Grades are the existing `getGrade` letters — A/B/C/D/F — everywhere.** WAEC's
1–9 numerals are not derivable from the boundaries currently encoded, and
inventing a finer scale would put this page in disagreement with every result
page in the app. Bands in the exam view are therefore letter ranges (`B–C`), not
numeral ranges. Adding true WAEC numerals is a separate change to `getGrade` and
its callers.

**Scope** means every topic in the subject's knowledge graph — the same
population `computePathState` already builds for the learning path, not a subset
filtered by the student's `classLevel`.

Filtering by class level was the first instinct, and it is wrong twice over. The
graph is a DAG with cross-level prerequisite edges, so cutting nodes out of it by
class level severs edges and changes what `classifyTopic` reports as a bottleneck
— the page would disagree with the learning path about which topics exist and
which are blocking. And an SS2 student who has genuinely covered an SS3 topic
would find their own evidence missing from the page.

Not-yet-taught topics are not a problem to be filtered away: they land in
**Unproven**, which already says "unknown, not weakness" and is the honest place
for them. The exam view uses the same population for a different reason
(section 6.2) — an exam does not care what has been taught yet.

**Band 2 — the topic breakdown.** Every topic in the subject's curriculum scope,
not only the ones with data: an untouched topic is a finding, not an absence.
Per topic: title, mastery (or the `evidence-display` fallback line), questions
answered, accuracy, last studied — grouped as described in section 5.

**Band 3 — difficulty and error profile.** Three reads, all from columns that
already exist:

- *Accuracy by difficulty* (`LearningEvent.difficulty`). Strong on `BASIC`,
  collapsing on `ADVANCED` is a depth problem. Weak on `BASIC` is a foundation
  problem, and advanced practice is wasted effort until it is fixed. Naming
  which one a student has is the point of the band.
- *Pacing* — mean `LearningEvent.seconds` against the authored
  `Question.timeEstimateSeconds` for the questions answered. Slow, on pace, or
  rushing. A student who is accurate but 40% over the estimate will fail a timed
  paper for reasons no accuracy figure reveals. The join runs through
  `LearningEvent.sourceId`, which is documented as being for audit rather than
  logic and carries no foreign key or index. So pacing is computed from the
  subject's *authored mean* estimate rather than per-question: one aggregate
  over `Question` filtered by subject, joined to nothing. That is a slightly
  coarser figure, and it is the honest cost of not adding an index to satisfy a
  display metric.
- *Rapid-guess rate* — the share of answers where `isRapidGuess(seconds)` holds.
  This quietly catches the student clicking through practice to farm a streak,
  and it is worth saying out loud to them, because their mastery numbers are
  being poisoned by it.

Rows link by group: **Needs work** → targeted practice, **Needs revision** → a
revision set, **Unproven** → the lesson, **Coming along** → more practice on the
same topic. **Solid** links to the lesson only when flagged stale.

### 5. Topic grouping is a presentation of `GapCategory`, not a new taxonomy

`classifyTopic` already returns WEAK / DECAYED / BOTTLENECK / ABANDONED /
UNTOUCHED, with carefully reasoned gating — WEAK on `confidence` because it asks
how well we know the topic now; DECAYED on raw observations because it asks
whether the student once knew it. That reasoning must not be re-litigated in a
second classifier.

But the gap queue cannot be used directly, because `classifyTopic` returns
`null` for two very different situations: a topic that is fine, and a topic with
some evidence but not enough to judge. The gap queue does not care — neither is
a gap. A performance view very much does: one is a success and the other is a
blind spot.

So `engines/analytics/topic-groups.ts` is a thin presentation layer that calls
`classifyTopic` and splits its `null`:

| Group | Source | What it means to the student |
|---|---|---|
| **Needs work** | WEAK, BOTTLENECK | Measured, and weak. Practise these. |
| **Needs revision** | DECAYED | You knew this and it has faded. |
| **Unproven** | UNTOUCHED, ABANDONED, and `null` below `OBSERVATION_FLOOR` | Not a weakness — an unknown. |
| **Coming along** | `null`, at or above the floor, mastery `< TARGET` | Real progress, not finished. |
| **Solid** | `null`, at or above the floor, mastery `>= TARGET` | Strong. Flagged *stale* when retention has fallen. |

Five groups, not four: `WEAK_MASTERY` is 50 and `TARGET` is 70, so a topic
between them is neither a gap nor solid. A four-group split would have to round
that band to one or the other, and both roundings lie — calling a 62 "solid"
tells a student to stop when they are eight points short of the threshold the
learning path itself uses, and calling it weak buries the genuine gaps beneath
topics that are going fine. It gets its own group and its own verb.

Coming along is bounded by `TARGET` alone, not by `WEAK_MASTERY`, because one
more case reaches `null`: a topic with enough observations but confidence below
`CONFIDENCE_FLOOR` — old evidence — whose mastery is under 50. `classifyTopic`
withholds WEAK there by design, and this view must not overturn that
withholding by routing the topic into Needs work through a side door. It is a
topic the student has worked and we can no longer confidently judge, which is
"keep going", not "you are weak at this". The five groups are therefore total
over every `GapCategory` and every `null`, with no unhandled case.

Four rules this encodes, each of which the current page gets wrong:

- Ranking by raw wrong-count conflates "you are bad at this" with "you practised
  this a lot", so the topics a student worked hardest on rise to the top of
  their weakness list. Mastery-based grouping does not have that failure.
- Unproven is separated from weak, because they need opposite responses — one
  needs practice, the other needs a lesson.
- ABANDONED sits in Unproven rather than Needs work, consistent with the gap
  queue's rule that abandonment explains a gap but does not rank it.
- Solid-but-stale is surfaced, because a topic aced in June and untouched since
  is not the same as one aced last week, and `retention` already knows.

Within Needs work, ordering follows `gapQueue`'s comparator — bottleneck score
descending, then mastery ascending — so the highest-leverage fix is first and
the page agrees with the learning path about what matters.

### 6. The exam view

#### 6.1 Exam registration

New models. Nothing currently records which subjects a student is sitting, and
inferring it from activity cannot see the single most valuable finding this
feature can produce: a registered subject with no data at all.

```prisma
model ExamRegistration {
  id        String   @id @default(cuid())
  studentId String
  student   User     @relation(fields: [studentId], references: [id], onDelete: Cascade)
  examType  ExamType
  year      Int
  /// Overrides the derived sitting date when the board publishes a timetable.
  sittingDate DateTime?

  subjects ExamRegistrationSubject[]

  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt

  @@unique([studentId, examType, year])
  @@index([studentId])
}

model ExamRegistrationSubject {
  registrationId String
  registration   ExamRegistration @relation(fields: [registrationId], references: [id], onDelete: Cascade)
  subjectId      String
  subject        Subject          @relation(fields: [subjectId], references: [id])

  @@id([registrationId, subjectId])
  @@index([subjectId])
}
```

A join table rather than a `Json` array of subject ids: it gives referential
integrity, it survives a subject being renamed or removed, and it makes "which
students are sitting Further Maths" a query rather than a scan. The cost is one
extra table, which is the right trade for the spine of a whole view.

`User` gains `examRegistrations ExamRegistration[]`; `Subject` gains
`examRegistrations ExamRegistrationSubject[]`.

Registration is seeded from the student's `Track` — the standard Science / Arts
/ Commercial combination — so the student confirms rather than composes, and
edits it in Settings. `sittingDate` is optional; when absent the date comes from
the existing `examTargetFor`, which stops guessing the *board* from class level
and starts reading the registration.

Validation warns, never blocks: JAMB is four subjects with Use of English
compulsory; WAEC and NECO expect English and Mathematics among eight or nine. A
student mid-way through setup should not be argued with, but they should be
told.

#### 6.2 Two signals, never merged

The headline shows days to the sitting and then, side by side:

> **Exam-condition score: 58%** — average of your last 5 timed WAEC papers
> **Syllabus coverage: 62%** — by exam weight, across your 9 registered subjects

Followed by one sentence reconciling them, which is where the value is:

- Papers high, coverage low → *"Your paper scores look like a C, but a third of
  the syllabus has never been attempted — expect the real thing to be harder
  than your practice."*
- Coverage high, papers low → *"You've covered the syllabus well; your paper
  scores lag your mastery, which usually means timing, not knowledge."* The
  pacing figure from section 4 is the evidence for that claim and is linked.

They are never averaged into a single readiness percentage. A blended number is
a judgement call nobody can defend to a parent, and the disagreement between the
two signals carries more information than either alone.

**Coverage and strength are exam-weighted.** `Topic.waecWeight` and
`Topic.jambWeight` already record how much each topic is worth in each exam, and
`recommend.ts` already reads them through `examWeight`. Both figures weight every
topic by the registration's exam weight rather than counting topics equally:

- **Coverage** = (weight of topics whose observations reach `OBSERVATION_FLOOR`)
  / (total weight of the subject's syllabus topics).
- **Syllabus strength** = weight-weighted mean mastery over those topics, with
  untouched topics contributing zero. In an exam context an absent topic is a
  real weakness, not a missing data point.

Unweighted counting would be actively misleading here: a student who has covered
fifteen minor topics and skipped the three that carry a quarter of the paper
would read as 83% covered. Weighting is the difference between a coverage figure
that predicts something and one that flatters. Where a subject's weights are all
zero — never authored — the engine falls back to equal weighting and says so in
the caption, rather than dividing by zero or silently reporting 0% coverage.

Scope here is the **full syllabus** for the subject, every `CurriculumLevel`, not
the student's class level: WAEC examines SS1 material whether or not an SS2
student has reached SS3.

#### 6.3 Predicted band

A grade *band* per subject (`B–C`), never a point estimate and never a
probability. Two estimators, chosen by what evidence exists:

**Estimator A — from papers** (preferred; requires at least `MIN_PAPERS = 2`
completed timed attempts of `PAST_PAPER`, `MOCK_EXAM` or `CBT_PRACTICE` type
matching the registration's `examType` and subject). Centre is the mean
percentage of the most recent five. Half-width is
`max(4, stdev) + (1 - coverage) * 6`.

The centre is deliberately *not* discounted for low coverage. A full past paper
already samples the whole syllabus, so a student's gaps are priced into that
score; discounting again would double-count them. Low coverage widens the band
instead — it is uncertainty, not a known deficit — and the untouched topics are
named separately underneath, which is more actionable than a shaved number.

**Estimator B — from mastery** (when papers are too few). Centre is mean
syllabus mastery mapped to a percentage, with untouched topics contributing the
guess rate for a four-option objective paper rather than zero, since a student
does not score zero on a topic they have never seen. Half-width is a flat 10,
and the figure is labelled *"estimated from your topic mastery, not from past
papers"* wherever it appears. It is the weaker estimator and says so.

**Neither** — below both thresholds the view refuses: *"Sit two full papers and
I'll be able to tell you."* Refusing to predict is a feature. A confident wrong
band in either direction — false comfort or false alarm — costs a student more
than a withheld one.

#### 6.4 Subject-by-subject readiness

Worst first: coverage, syllabus strength, papers sat, predicted band. A
registered subject with no data renders as **"No data — this is your biggest
risk"**, which is the row that justifies explicit registration and is invisible
without it.

#### 6.5 Course eligibility — seam only

`JambCombination` already maps a course of study to required and alternate
subjects. The exam view reserves the shape for it — *"Medicine requires Physics,
Chemistry and Biology; your Chemistry is the weakest of the three"* — as an
`Insight` of kind `COURSE_REQUIREMENT_RISK`. The course-choice UI is not built
in this pass; the registration model has the room for it (a chosen course hangs
off `ExamRegistration` when that lands).

### 7. The progress view

Organising principle: **state a verdict, then show the working.**

**Band 1 — the verdict.** Window selectable: 30 days, 90 days, all time. Three
shapes of answer:

> Your accuracy is up 9 points over 90 days, across 412 questions. That's a real
> improvement.

> You're up 6 points, but only on 23 questions this month — too few to call it.
> Practise more and I'll be able to tell you.

> Your accuracy is up 7 points, but you've shifted toward easier questions. On
> questions of the same difficulty, you're flat.

The statistics behind those, in `engines/analytics/trend.ts`:

- **Minimum sample.** `MIN_TREND_QUESTIONS = 30` answered in the window. Below
  it, no direction is reported at all — the copy names the shortfall, in keeping
  with the "a count, not a hedge" decision already taken for topic evidence.
- **Direction.** Split the window in half by time; compare accuracy `p1` (n1)
  against `p2` (n2) with a two-proportion test. A direction is claimed only when
  `|p2 - p1| >= 2 * sqrt(p(1-p) * (1/n1 + 1/n2))` for pooled `p` — roughly a 95%
  guard. Otherwise the verdict is "holding steady", which is a genuine finding
  and is written as one, not as a failure.
- **Difficulty adjustment by direct standardisation.** Compute accuracy within
  each difficulty stratum for each half, then recombine both halves using the
  *same* reference mix — the pooled difficulty mix of the whole window. The
  adjusted delta answers "how would you have done if you'd faced the same mix of
  easy, medium and hard questions in both periods". `LearningEvent.difficulty` is
  nullable; unlabelled events form their own stratum and are standardised like
  any other rather than being dropped, so no answer is silently discarded.
- **Divergence.** When the raw and adjusted deltas differ in sign, or by three
  points or more, the verdict reports the adjusted figure and names the drift.
  This is the case no other version of this page would catch, and it is the
  single most useful sentence in the view.

**Band 2 — the accuracy chart.** Weekly buckets, two lines: raw and
difficulty-adjusted. Volume bars underneath, because a point computed from four
questions must visibly not carry the weight of one from ninety. The caption
states in words whatever the lines are doing, so the chart is decoration on a
phone rather than the only carrier of the message.

**Band 3 — what moved.** Topics improved this window and topics slipped, by
mastery delta. This is where the recency decay inside `TopicMastery` becomes
visible to the student instead of silently adjusting a number they never see.

**Band 4 — consistency.** Practice days per week, current streak (reuse
`lib/streak.ts`), mean session length. For exam preparation consistency predicts
outcomes better than any single score, and it is the metric a parent grasps
immediately. Framed as an observation — *"You practise 3 days in most weeks"* —
with no invented benchmark. A comparative claim is only made if it can be
grounded in our own data, which it currently cannot, so it is not made.

**Per-subject drill-down** repeats the verdict and chart filtered to one
subject, because "improving overall" routinely hides one subject going
backwards.

### 8. Overview

The highest-severity handful of `Insight`s across all three lenses, plus the
target-exam headline and the trend verdict. Nothing computed here that the other
three views do not already compute; Overview is a selection, not a fourth
analysis.

### 9. The recommendation seam

Every view produces, alongside its display data, `Insight[]`:

```ts
export type InsightSeverity = "CRITICAL" | "WARNING" | "INFO" | "WIN";

export type Insight = {
  kind: InsightKind;
  severity: InsightSeverity;
  subjectId?: string;
  topicId?: string;
  /** One plain sentence. This is the text that renders. */
  headline: string;
  detail?: string;
  action?: { label: string; href: string };
};
```

`InsightKind` is a closed union: `UNTOUCHED_SUBJECT`, `LOW_COVERAGE`,
`WEAK_TOPIC`, `DECAYED_TOPIC`, `STALE_TOPIC`, `BOTTLENECK_TOPIC`,
`RAPID_GUESSING`, `PACING_SLOW`, `PACING_RUSHED`, `DIFFICULTY_DRIFT`,
`IMPROVING`, `PLATEAU`, `SLIPPING`, `INSUFFICIENT_EVIDENCE`,
`LOW_CONSISTENCY`, `SUBJECT_STRENGTH`, `EXAM_RULE_VIOLATION`,
`COURSE_REQUIREMENT_RISK`.

`STALE_TOPIC` and `SUBJECT_STRENGTH` carry the two findings the subject view
produces that no other kind covers: a Solid topic whose retention has slipped,
and a subject with no gaps at all — the `WIN` that stops the section reading as
an unbroken list of failings.

Today these render as the sentences described above. Later, a recommendation
engine consumes the same array and turns it into `StudyPlanItem`s — the seam
exists there already. Adding recommendations then touches only the consumer,
because the engines already emit the findings.

Insights are generated by pure functions over already-computed view data, so
they are tested without a database.

## Error handling and empty states

Each view has a distinct empty state, because the fixes differ: no practice at
all (start practising), practice but no registration (register your sitting), a
registration with no papers (sit a paper), enough of everything but too little
in the selected window (widen the window — offered as a button, not a lecture).

A `loadGraph` or `computeTopicState` failure fails the view, not the shell:
Overview's bands render independently so a broken readiness band does not blank
the trend verdict. The write-back inside `computeTopicState` is already
best-effort and its failure costs a recomputation and nothing else.

Division-by-zero paths — no questions in a stratum, no papers, a subject with no
topics in scope — return "insufficient evidence" states rather than `NaN`. The
engines return discriminated unions (`{ status: "ok", ... }` /
`{ status: "insufficient", reason, shortfall }`) so a view cannot accidentally
render a hole.

## Testing

Pure functions in `src/engines/analytics/` tested as `node --test` scripts
against fixture event streams, matching how the learning engines are tested:

- `scripts/test-analytics-topic-groups.mts` — the `null` split; a topic with two
  observations lands in Unproven not Solid; ABANDONED lands in Unproven;
  solid-but-stale is flagged; ordering matches `gapQueue`'s comparator.
- `scripts/test-analytics-trend.mts` — below `MIN_TREND_QUESTIONS` claims no
  direction; a change inside the noise guard reads "holding steady"; the
  flat-but-easier student is reported as flat with drift named; null-difficulty
  events are standardised, not dropped; empty strata do not produce `NaN`.
- `scripts/test-analytics-readiness.mts` — estimator A chosen at two papers and
  not at one; low coverage widens the band without moving the centre; estimator
  B labels itself; the registered-and-untouched subject produces a `CRITICAL`
  insight; JAMB rule validation warns on three subjects and on missing English;
  exam weighting — skipping three heavy topics scores far below skipping
  fifteen light ones, and an all-zero-weight subject falls back to equal
  weighting rather than reporting 0% or dividing by zero.
- `scripts/test-analytics-profile.mts` — pacing against `timeEstimateSeconds`,
  rapid-guess rate, accuracy by difficulty with missing labels.
- `scripts/test-analytics-insight.mts` — severity ordering and Overview
  selection; every `InsightKind` produces a sentence.
- `scripts/test-exam-registration.mts` — seeding from `Track`, uniqueness per
  (student, exam, year), sitting-date fallback to `examTargetFor`.

All added to the `test` script in `package.json`.

## Phasing

**Phase 1 — subject lens.** Tab shell and routes; `engines/analytics/`
scaffolding with `insight.ts`, `topic-groups.ts`, `profile.ts`; the subject
view. No migration, no new schema. Ships useful on its own and immediately
replaces the wrong-count ranking.

**Phase 2 — progress lens.** `trend.ts`; the progress view; Overview without its
readiness band. Still no migration.

**Phase 3 — exam lens.** The `ExamRegistration` migration; registration UI in
Settings; `readiness.ts`; the exam view; Overview's readiness band;
`examTargetFor` reading the registration.

The migration sits at a clean phase boundary on purpose — two useful phases ship
before any schema risk is taken.

## Migration note

`prisma migrate` cannot reach the database from the development machine, so the
Phase 3 migration is authored as SQL and applied through the Supabase SQL
editor. Two consequences the plan must carry:

- The migration file must be written with LF endings, or its checksum drifts
  against the applied migration.
- The SQL editor can report success on a partially applied batch. Success is
  confirmed by querying the catalog for both tables, their primary keys, the
  unique index on `(studentId, examType, year)` and both foreign keys — not by
  the editor's message.

## Decisions taken

- **Two signals, not one readiness score.** Coverage and exam-condition
  performance answer different questions, and their disagreement is the finding.
  A blended headline number cannot be explained to a parent.
- **Bands, not point predictions, and refusal below the evidence threshold.** A
  confident wrong prediction costs a student more than a withheld one.
- **Coverage widens the band; it does not lower the centre.** A full past paper
  already samples the syllabus, so discounting the score for gaps would count
  them twice.
- **Explicit exam registration over inferred subjects.** The registered subject
  with no data is the most valuable row the feature produces, and it is
  structurally invisible to any activity-inferred subject list.
- **Topic groups are a presentation of `GapCategory`.** The gating reasoning in
  `classifyTopic` is not re-litigated; the only addition is splitting its `null`
  into Unproven, Coming along and Solid — a distinction a gap queue does not
  need and a performance view cannot do without.
- **Coverage is weighted by `waecWeight` / `jambWeight`.** Counting topics
  equally would report a student who skipped the three heaviest topics as 83%
  covered. The weights are already authored and already read by `recommend.ts`;
  not using them would be the harder decision to defend.
- **Grades stay `getGrade` letters.** WAEC 1–9 numerals are not derivable from
  the encoded boundaries, and a finer scale invented here would disagree with
  every result page in the app.
- **Difficulty standardisation over a difficulty-weighted score.** Standardising
  both halves to a common mix answers the student's actual question — "same
  questions, am I better?" — and is explainable in one sentence. A weighted
  composite is not.
- **Derive on read; no rollup tables.** Consistent with the evidence layer's own
  rule that the fold is a cache, not a truth. The view loaders are the seam if
  measurement ever says otherwise.
- **No cohort comparison.** The population is self-selected and small; a
  percentile would be unfair before it was useful.
