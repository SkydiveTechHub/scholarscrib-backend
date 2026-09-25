# Learning evidence, mastery, and the path engine

Pure functions: `src/engines/learning/*`. IO facades: `src/lib/learning-path.ts`, `topic-mastery-store.ts`, `learning-events.ts`, `classroom-data.ts`, `classroom-topic.ts`, `lesson-engine.ts`, `lesson-progress.ts`, `pretest.ts`.

Port the engines verbatim and keep `scripts/test-learning-path-*.mts` and `scripts/test-evidence-display.mts` as the oracle. `SCORING_VERSION` is **2**. Changing a formula requires bumping it so `TopicMastery` rows refold.

## Ledger

Every graded answer, abandon, lesson completion, card review, and passed pretest appends a `LearningEvent`. Nothing in the scoring path updates or deletes events.

`emitLearningEvents` is the writer used by submit. Card review and pretest and the lesson-practice pass path write their own kinds. Kinds that do **not** move a mastery channel: `QUIZ_ABANDONED` (counted as an abandon signal) and `PRETEST_PASSED` (readiness flag lives on `PerformanceMetric`).

| Kind | Channel | Contribution |
|---|---|---|
| `QUESTION_ANSWERED` | accuracy (`acc`) | difficulty-shaped outcome, see below |
| `LESSON_BLOCK_COMPLETED`, `LESSON_COMPLETED` | `lesson` | the event’s `score` (0..1) when present |
| `CARD_REVIEWED` | `srs` | AGAIN 0, HARD 0.5, GOOD 0.85, EASY 1 |
| `QUIZ_ABANDONED` | none | increments abandon evidence |
| `PRETEST_PASSED` | none | separate pretest flag |

## Evidence math (`evidence.ts`)

| Constant | Value |
|---|---|
| `RECENCY_HALF_LIFE_DAYS` | 45 |
| `PRIOR_STRENGTH` | 4 |
| `PRIOR_OUTCOME` | 0.45 |
| `RAPID_SECONDS` | 3 |
| `RAPID_WEIGHT` | 0.3 |
| `CONFIDENCE_FLOOR` | 0.35 |
| `OBSERVATION_FLOOR` | 3 |
| `ABANDONED_FLOOR` | 2 |

Recency weight of an event aged `ageDays`:

```
w = 2 ^ (-ageDays / 45)
```

Response weight is `w` times `0.3` when `seconds < 3` (rapid guess), else `w`.

Difficulty outcomes for `QUESTION_ANSWERED`:

| Difficulty | Correct | Wrong |
|---|---|---|
| BASIC | 0.85 | 0 |
| INTERMEDIATE | 1.0 | 0.15 |
| ADVANCED | 1.0 | 0.35 |

A wrong advanced answer still leaves residual credit (the student attempted a hard item). A wrong basic answer does not.

Channel score, with `mass` = sum of response weights and `outcome` = sum of weight × item outcome:

```
score = (outcome + 4 * 0.45) / (mass + 4)
confidence = mass / (mass + 4)
```

The prior (strength 4, outcome 0.45) is a sceptical baseline so one lucky answer cannot produce mastery 100.

## Fold (`fold.ts` and `topic-mastery-store.ts`)

`decayTo` moves a stored aggregate from `decayAnchor` to “now” by multiplying **outcome and mass** by the recency factor between those instants. Observation counts stay put. That is why a topic can have many observations and a small mass after a long gap.

`lastEffortAt` advances on effort kinds. Opening a lesson without a scored block does not count.

`loadFoldedAggregates` reads `TopicMastery`, decays it, then folds events with `seq > cursorSeq`. If `scoringVersion` mismatches, it replays from seq 0. `persistAggregates` writes the new sums, the new anchor, and the new cursor. Persistence is best-effort: a failed write must not fail the page that triggered the read. The next read will fold the same events again.

## Mastery (`mastery.ts`)

Base weights, multiplied by each channel’s confidence and then renormalised so they sum to 1:

| Channel | Base weight |
|---|---|
| Accuracy | 0.45 |
| Lesson | 0.35 |
| SRS | 0.20 |

```
mastery = round(clamp(composite * 100, 0, 100))
confidence = totalMass / (totalMass + PRIOR_STRENGTH)
```

`composite` is the confidence-weighted sum of channel scores.

### Levels

`masteryLevelFromScore` in `src/lib/lesson-engine.ts` (also used as the displayed level):

| Score | Level | Stability S (days) |
|---|---|---|
| ≥ 85 | STRONG | 60 |
| ≥ 70 | COMPETENT | 30 |
| ≥ 50 | DEVELOPING | 14 |
| else | WEAK | 5 |

### Retention

Shared with flashcard scheduling (`src/lib/spaced-repetition.ts`):

```
R(t) = (1 + (19/81) * (t / S)) ^ -0.5
```

`t` is days since `lastEffortAt`. `S` is the stability of the current level. `topicRetention` in the mastery module is this function. A topic with no effort yet has no retention to decay.

## Availability and gates (`availability.ts`)

| Name | Value | Meaning |
|---|---|---|
| `TARGET` | 70 | “mastered” for recommendations and exam-candidate filters |
| `GATE` | 60 | prerequisite bar before strength is applied |
| `PRETEST_PASS` | 80 | pretest percentage that sets `pretestPassedAt` |

A topic is unlocked when every `PREREQUISITE` edge is satisfied. An edge with `strength` s is satisfied when the prerequisite’s mastery ≥ `GATE * s`, **or** the student has passed the pretest on that prerequisite. `strength` 1 means mastery ≥ 60. `strength` 0.5 means mastery ≥ 30.

Lesson unlock (classroom, not the graph colour): the topic is available, authored prerequisites listed on the lesson are done, and prior sibling lessons in the topic are `COMPLETED`.

## Recommendations (`recommend.ts`)

| Threshold | Value |
|---|---|
| `STRONG_MASTERY` | 85 |
| `DECAY_RETENTION` | 0.85 |
| `WEAK_MASTERY` | 50 |
| `GAP_RETENTION` | 0.8 |

Score of a candidate topic:

```
0.30 * urgency + 0.30 * leverage + 0.20 * decay + 0.10 * readiness + 0.10 * freshness
```

- `urgency = (TARGET - mastery) / TARGET` with TARGET 70
- `leverage` grows with how many later topics this one blocks
- `decay` grows as retention falls
- `readiness` is 1 when the topic is available
- `freshness` is 0.5 if `lastEffortAt` is under 1 day ago, else 1 (don’t immediately re-recommend what they just did)

Default list length `k = 3`. If nothing qualifies, fall back to mastered topics whose retention is below 0.85.

`keepLearning` (dashboard “continue”) prefers an in-progress lesson, then this recommender.

## Gaps (`gaps.ts`)

Classification priority used by the gaps list and by analytics:

| Category | Rule |
|---|---|
| WEAK | confidence ≥ 0.35 and mastery < 50 |
| DECAYED | observations ≥ 3 and retention < 0.8 |
| BOTTLENECK | topic is locked and at least 2 dependents are not mastered |
| ABANDONED | ≥ 2 abandon events and no other evidence category |
| UNTOUCHED | available, but none of the above |

Sort: non-abandoned first, then bottleneck score descending, then mastery ascending.

## Revision queue (`revision.ts`)

Due when retention < 0.85, or `cadenceDueAt ≤ now`, or the topic has due SRS cards. Priority:

```
decay * examWeight * (1 + blockedDependents / maxBlocked)
```

`examWeight` is the topic’s WAEC or JAMB weight depending on the student’s target. Cadence offsets are `[1, 3, 7, 14]` days from completion, next due at local midnight (Lagos) of that offset. The same offsets are the default `Lesson.revisionDays`.

## Graph colours

Subject page node colour (`classroom-data.ts` plus availability):

| Colour | Condition |
|---|---|
| DECAYED | retention < 0.85 |
| MASTERED | mastery ≥ 70 |
| STARTED | some evidence, not mastered |
| READY | available, no evidence |
| LOCKED | prerequisites unmet |

Check the function that maps state to colour before porting the labels; the tests in `scripts/test-learning-path-state.mts` pin the transitions.

## Pretest

`src/lib/pretest.ts`. Five objective questions, topic pool first, subject pool to fill. Creates a timed `TOPIC_QUIZ` (`ceil(5 * 1.5)` minutes). Pass at 80%. Grading completes the attempt like any other quiz and, on pass, upserts `PerformanceMetric` for `(student, subject, topic)` with `pretestPassedAt = now` and appends `PRETEST_PASSED`. A second pass returns `alreadyPassed: true` and does not need to move the timestamp. Grade label stored on the attempt is `"Pass"` or `"Retry"`.

## Lesson progress and lesson mastery

`PATCH /api/lessons/{id}/progress` writes `StudentProgress` only. It does **not** append a learning event. `masteryScore` on that row is not evidence; the comment in `lesson-progress.ts` says a client that sends `masteryScore` must also emit `LESSON_BLOCK_COMPLETED` or the work will not affect `TopicMastery`. Nothing in the current client does that. Events that do feed the lesson channel are written by the topic-practice pass path (`LESSON_COMPLETED`) and by any future block-completion writer.

`saveLessonProgress` resolves subject and topic through subtopic → topic. `forwardOnlyProgress`:

- status rank `NOT_STARTED < IN_PROGRESS < COMPLETED`
- never replace `COMPLETED` with an earlier status
- never lower `completionPercent`
- checkpoint JSON is a shallow merge of the patch onto the stored object

Lesson mastery score (separate from the evidence-layer mastery, and stored on `StudentProgress.masteryScore`):

- Knowledge checks: first-try correct = 1, correct after a retry = 0.5, else 0. Average those.
- Practice: best of the last 3 topic-practice percentages, as a 0..1 fraction.
- `masteryScore = round(clamp((0.3 * kc + 0.7 * practice) * 100, 0, 100))`

Levels for that score use the same 85 / 70 / 50 cuts as above.

## Analytics engines

Used by the performance subject view (`src/lib/analytics/subject-view.ts`), not by a dedicated route.

**Profile** (`src/engines/analytics/profile.ts`): needs at least 20 answers before a profile is shown. `rapidGuessRate` is the share of answers under 3 seconds. Pacing compares the student’s median time to the question’s `timeEstimateSeconds`: ratio < 0.6 is `RUSHED`, ratio > 1.3 is `SLOW`.

**Topic groups** (`topic-groups.ts`): gap categories map onto NEEDS_WORK, NEEDS_REVISION, UNPROVEN, COMING_ALONG, SOLID. A SOLID topic whose retention < `STALE_RETENTION` (0.9) is called out as stale.

**Insights** (`insight.ts`, `subject-insights.ts`): `RAPID_GUESS_ALARM = 20` (percent). Emits WEAK, BOTTLENECK, DECAYED, STALE, and positive WIN lines. `selectInsights` returns at most 3, and at most one WIN.

## What the pages compute

These are not HTTP routes. Shapes to expose are listed in [13-page-loaders.md](./13-page-loaders.md). The engines above are what those loaders call. A FastAPI port should keep the engines pure and let the loaders (or new endpoints) own the queries.
