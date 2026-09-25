# Assessments, question selection, and scoring

Modules: `src/lib/question-pool.ts`, `assessment-generation.ts`, `assessment-submit.ts`, `attempt-lifecycle.ts`, `attempt-timing.ts`, `attempt-results.ts`, `jamb-cbt.ts`, `jamb-cbt-generation.ts`, `jamb-cbt-preparation.ts`, `board-availability.ts`, `curriculum-scope.ts`, `topic-practice-result.ts`, `src/lib/utils.ts` (WAEC grades).

## Constants

| Name | Value | File |
|---|---|---|
| Minutes per question | 1.5, rounded up with `ceil` | `assessment-generation.ts` |
| Submit grace | 120 seconds past the deadline | `attempt-timing.ts` |
| Untimed stale | 24 hours after `startedAt` | `attempt-timing.ts` |
| Seen-question window | 30 days | `question-pool.ts` |
| Question type drawn | `OBJECTIVE` only | `question-pool.ts` |
| Exam year floor for pickers | 2000 | `exam-years.ts` |
| Provider outbound budget | 30 calls / 60s, key `provider:outbound` | `assessment-generation.ts` |

JAMB paper (`src/lib/jamb-cbt.ts`):

| Field | Value |
|---|---|
| English subject code | `ENG` |
| English questions | 60 |
| Each other subject | 40 |
| Other subjects the student picks | 3 (plus English = 4) |
| Total questions | 180 |
| Duration | 120 minutes |
| Marks per subject | 100 |
| Total marks | 400 |
| `passMarkPercent` stored on the assessment | 50 |
| No negative marking | correct count only |

## Selecting questions

`pickRandomQuestionIds` is SQL `ORDER BY random() LIMIT n` over `Question` joined to topic/curriculum when a scope is set.

Filters that may apply: `subjectId`, `topicIds`, `examType`, `examYear`, `difficulty`, curriculum scopes `(classLevel, term)`, and an exclude-list of question ids the student has already answered.

“Seen” means a `QuestionResponse` on a `COMPLETED` attempt whose `completedAt >= now - 30 days`. The query starts from the student’s attempts so it does not scan every response in the bank.

`pickQuestionsPreferringUnseen`:

1. Draw `count` unseen.
2. If short, draw from the full pool (over-fetch by the shortfall) and fill.
3. Return fewer than `count` if the bank itself is smaller. Callers decide whether a short paper is an error.

JAMB CBT does **not** prefer unseen. It draws each subject’s quota independently with `pickRandomQuestionIds`.

## Ordinary quiz (`generateQuiz`)

1. Resolve subject by slug or id → `subject-not-found`.
2. Resolve `topicSlug` into `topicIds` → `topic-not-found` if missing.
3. Provider warm-up, only when all of these hold: `QUESTION_PROVIDER_ENABLED=true`, `examType` is WAEC, JAMB, or NECO, and `examYear` is set. If the ledger is not `SATURATED` or `FAILED` and the outbound budget allows, schedule `ensureQuestionsCached` on `after()` (do not block the response).
4. `assessmentType` is `PAST_PAPER` when `examType` is set, otherwise `TOPIC_QUIZ`.
5. Reap stale attempts, then look for a resumable one: same student, subject, type, exam type, exam year, `totalMarks` equal to the requested count, and (when topics were requested) every question’s topic inside that set. Newest of up to 5 candidates. Hit → return it with `source: "resumed"` and `resumed: true`. Do not create another paper.
6. Else pick, preferring unseen. If a topic filter returns nothing, fall back to the whole subject.
7. Still empty: `questions-preparing` if a fetch was scheduled, else `no-questions`.
8. Create `Assessment`, `AssessmentQuestion` rows in order, and an `AssessmentAttempt` in `IN_PROGRESS`.

Timing: `timeLimitMinutes = null` only when `untimed` is true **and** `examType` is absent. Otherwise `ceil(n * 1.5)`. `deadlineAt` is `startedAt + timeLimitMinutes` when timed.

The client payload drops `correctAnswer` and `explanation`.

## Scoped mock (`generateScopedMockExam`)

Inputs: exam type, subject, `from`/`to` scope points, count. `expandScopeRange` normalises a reversed range (SS3→SS1 becomes SS1→SS3). Draws with subject + exam type + those curriculum scopes. A short paper is still success: `short: true` and `totalQuestions < requestedCount`. Zero questions is `no-questions-in-scope` → HTTP 422. Assessment type `MOCK_EXAM`, always timed.

## JAMB CBT generation

1. Resolve English by code plus the three chosen subject ids. Reject duplicates, a count other than 3, or English inside the chosen three (`bad-selection`). A chosen id that is not a JAMB subject is `subjects-unavailable`. Missing English row in the database is `english-missing` (500, data error).
2. `ensureJambYearCached` schedules provider work for English and the three subjects for that year.
3. Coverage must be complete (60 + 40 + 40 + 40 available) or the generate route returns 422 `INSUFFICIENT_QUESTIONS` with `coverage` and `shortfalls`. Prepare does not require this; it returns `ready: false`.
4. Resume if an in-progress `CBT_PRACTICE` exists for this student, JAMB, this year, `totalMarks = 400`.
5. Otherwise draw and persist. Title and per-subject section metadata go on the response as `subjects`.

`prepareJambYear` returns `{ ready, message, coverage, shortfalls }` and is HTTP 200 whenever the selection is valid.

## Board readiness

`src/lib/board-availability.ts`, used by the boards route and by navigation that hides exams the bank cannot support.

| Mode | Ready when |
|---|---|
| Past questions | ≥ 4 subjects that each have ≥ 1 paper |
| Mock exam | ≥ 3 subjects that each have ≥ 20 questions tagged to a curriculum level |

The response counts `qualifying` versus `required` and includes a `reason` string when not ready.

## Attempt lifecycle

**Stale** (`isAttemptStale`):

- Timed: now is past `deadline + 120 seconds`.
- Untimed: now is past `startedAt + 24 hours`.

**Reap** (`reapStaleAttempts`), called at the start of generation: up to 100 `IN_PROGRESS` attempts that are stale. Compare-and-swap to `TIMED_OUT`. For each distinct topic on that paper, emit one `QUIZ_ABANDONED` learning event with `occurredAt = startedAt` (the abandon is dated when they began, not when the reaper ran).

**Resume** does not reopen `TIMED_OUT` or `COMPLETED`.

## Submit and grading

`submitAttempt`:

1. Load the attempt. Missing or wrong student → `not-found`.
2. Already `COMPLETED` → `replayed` (rebuild the result, no second write).
3. Grade each answer: `isCorrect = (selectedAnswer === correctAnswer)`. Unanswered counts as incorrect. Ordinary papers: `score = sum of marks of correct responses`, `totalMarks` from the assessment, `percentage` to 1 decimal.
4. `CBT_PRACTICE` ignores per-question marks and uses `scoreJambPaper` (below). The stored grade string is still the WAEC scale of that percentage, plus a separate `jamb` object.
5. Clamp timing with `evaluateAttemptTiming`: `timeSpentSeconds = min(clientReported, elapsed wall clock, allowed seconds)`. A soft overrun (past the deadline but inside the grace, or a client that reports a huge number) is still graded. Past grace, the reaper should already have timed it out; if submit wins the race first, the clamp still bounds the stored duration.
6. Transaction: `updateMany` where `id` and `status = IN_PROGRESS`, set `COMPLETED`, scores, `timeSpentSeconds`, `awayEvents`, `completedAt`. `questionResponse.createMany(..., skipDuplicates)`. `emitLearningEvents` for each response: kind `QUESTION_ANSWERED`, `correct`, `difficulty` copied from the question, `seconds`, `sourceId = questionId`, `subjectId`, `topicId`.
7. If the update matched zero rows, re-read. `COMPLETED` → replay. Anything else → `expired` (HTTP 409).

### WAEC grade of a percentage

From `GRADE_BOUNDARIES` / `getGrade` in `src/lib/utils.ts`. Applied to non-JAMB percentages and also stored on JAMB attempts as the letter beside the 400-point score.

| Percentage | Grade | Remark | Credit |
|---|---|---|---|
| ≥ 75 | A1 | Excellent | yes |
| ≥ 70 | B2 | Very Good | yes |
| ≥ 65 | B3 | Good | yes |
| ≥ 60 | C4 | Credit | yes |
| ≥ 55 | C5 | Credit | yes |
| ≥ 50 | C6 | Credit | yes |
| ≥ 45 | D7 | Pass | no |
| ≥ 40 | E8 | Pass | no |
| else | F9 | Fail | no |

The performance page uses a **different** coarse scale (`src/lib/performance.ts`): A ≥ 75, B ≥ 65, C ≥ 50, D ≥ 40, else F. Do not unify them silently; the results screen is A1–F9 and the history list is A–F.

### JAMB scoring

Per subject, `marks = (correct / total) * 100`. Paper score is the sum, rounded to 1 decimal. `percentage = round((rawScore / 400) * 1000) / 10` (one decimal).

Bands (`jambBand`):

| Score | Band |
|---|---|
| ≥ 300 | Excellent |
| ≥ 250 | Strong |
| ≥ 200 | Good |
| ≥ 160 | Fair |
| else | Needs work |

`JAMB_CUTOFFS` in `exam-types.ts` (140–280 by competitiveness) is display reference for marketing/UI, not an input to `scoreJambPaper`.

## Result payload

`buildAttemptResult` (`attempt-results.ts`):

```
attemptId, assessmentTitle, assessmentType, examYear,
jamb: null | { score, totalMarks, percentage, subjects[], band },
score, totalMarks, percentage, grade, gradeRemark, isCredit,
timeSpentSeconds, awayEvents, totalQuestions, correctCount,
results: [{
  questionId, questionText, questionImageUrl, options,
  selectedAnswer, correctAnswer, isCorrect, explanation,
  explanationImageUrl, topicId, topicTitle, timeSpentSeconds, flaggedForReview
}],
topicBreakdown: [{ topicId, topicTitle, correct, total, accuracy, status }]
```

Topic status on the breakdown (this is **not** the mastery-level scale): ≥ 80 strong, ≥ 60 competent, ≥ 40 developing, else weak.

## Topic practice from a lesson

When submit includes `practiceExit`, after grading:

`recordTopicPracticeResult` updates lesson mastery. Knowledge-check portion and practice portion combine as `0.3 * kc + 0.7 * practice`, and the stored practice component is the best of the last 3 practice scores (`src/lib/lesson-engine.ts`). On pass (lesson `passMarkPercent`, default 60), progress becomes `COMPLETED` and a `LESSON_COMPLETED` event is written once for that attempt. Then `markPlanFromLesson`.

## Exam calendar helpers

`src/lib/exam-target.ts` sitting dates (West Africa Time): WAEC 3 May, NECO 7 June, JAMB 12 April. Years until the sitting: SS3 = 0, SS2 = 1, SS1 = 2. `examYearRange` lists years from `max(2000, oldest known paper)` through the current year, newest first.

These dates feed “days to exam” on the study plan and dashboard. They are constants, not admin-editable.
