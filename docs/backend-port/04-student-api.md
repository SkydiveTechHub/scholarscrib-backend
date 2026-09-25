# Student HTTP API

Base path `/api`. Auth means `auth()` from `src/lib/auth.ts` and a 401 when `session.user.id` is missing, unless a route says otherwise. Shared limits and entitlement errors are in [03-auth-and-access.md](./03-auth-and-access.md).

Handlers for Auth.js live at `GET` and `POST /api/auth/*` (`src/app/api/auth/[...nextauth]/route.ts`). They speak the Auth.js protocol (CSRF, callback, session). A FastAPI replacement should expose login, logout, session, and OAuth callback with the semantics in the auth doc, not a line-by-line clone of Auth.js URLs, unless the Next client still calls those URLs.

---

## Auth and account

### `POST /api/auth/register`

No session. Rate limit 5 per 10 minutes per IP.

Body (`registerSchema`):

| Field | Rule |
|---|---|
| `role` | literal `STUDENT`, default `STUDENT`. `TEACHER` is rejected. |
| `firstName`, `lastName` | string, min 2 |
| `email` | email, stored lowercased |
| `password` | min 6, bcrypt cost 12 |
| `classLevel` | `SS1` \| `SS2` \| `SS3` |
| `track` | `SCIENCE` \| `ARTS` \| `COMMERCIAL` |
| `state` | Nigerian state enum |

| Status | Body |
|---|---|
| 201 | `{ "message": "Account created successfully", "user": { id, email, firstName, lastName, classLevel, track } }` |
| 400 | `{ "error": "Validation failed", "details" }` |
| 409 | `{ "error": "An account with this email already exists" }` |
| 429 | rate limit |
| 500 | `{ "error": "Something went wrong. Please try again." }` |

Does not sign the user in.

### `PATCH /api/user/profile`

Body `updateProfileSchema`, all optional. Absent key = leave unchanged. `""` for `phone` or `state` stores NULL.

Fields: `firstName`, `lastName`, `phone` (Nigerian regex or `""`), `state` (enum or `""`), `classLevel`, `track`. **Email is not accepted.**

| Status | Body |
|---|---|
| 200 | `{ "message": "Profile updated", "user": <updated fields> }` |
| 400 | validation, or `{ "error": "Nothing to update" }` |
| 409 | phone unique violation |
| 401 / 500 | standard |

### `POST /api/user/complete-profile`

Body `completeProfileSchema`: required `classLevel`, `track`, `state`. Writes the profile and calls `updateSession({})` so the JWT cache refreshes. 200 `{ "message": "Profile completed" }`. 400 uses the first Zod message as `error` plus `details`.

### `POST /api/user/password`

Rate limit 5 / 15 minutes per user. Body: `currentPassword` min 1, `newPassword` min 6.

| Outcome | Status | Error string |
|---|---|---|
| ok | 200 | `{ "message": "Password changed" }` |
| Google-only | 400 | account has no password |
| wrong current | 400 | wrong password |
| validation | 400 | |

Then revokes every other device (best-effort).

### `POST /api/user/avatar`

Rate limit 10 / hour per user. `multipart/form-data` field `file`. JPEG, PNG, or WebP, max **2 MB**. Uploads to Cloudinary, stores the URL on `User.image`.

| Status | When |
|---|---|
| 200 | `{ "message": "Photo updated", "image": "<url>" }` |
| 400 | missing file, bad type, too large, or Cloudinary rejection |
| 503 | Cloudinary env not set |
| 429 / 401 / 500 | standard |

### `POST /api/user/devices`

Body is one of `{ "deviceId": "<id>" }` or `{ "allOthers": true }`. See the auth doc for revoke rules. Success `{ "revoked": <n> }`.

### `PATCH /api/user/notification-preferences`

Partial object, at least one of `studyReminders`, `streakReminders`, `announcements` (booleans). Upserts `NotificationPreference`. 200 returns the three booleans.

---

## Catalogue (public)

### `GET /api/subjects`

No auth. Query: `track`, `examType` (`waec` | `jamb` | `neco`, used as board flags). Reads the cached catalogue and filters in memory. 200 `{ "subjects": [...] }` including `_count.topics` and `_count.questions`. 500 `{ "error": "Failed to fetch subjects" }`.

### `GET /api/subjects/{subjectSlug}/topics/{topicSlug}`

No auth. 200 `{ subjectId, subjectName, topicId, topicTitle, questionCount }`. 404 subject or topic. 500 on unexpected failure.

### `GET /api/questions`

No auth. Query: `subjectId`, `topicId`, `examType`, `examYear`, `difficulty`, `page` default 1, `limit` default 20 max **50**.

200 `{ "questions": [...], "pagination": { page, limit, total, totalPages } }`.

**The question objects include `correctAnswer` and `explanation`.** Quiz generation strips those. This list does not. Treat that as a known exposure when designing the public API.

### `GET /api/questions/past-papers`

No auth. Query: `examType`, `subjectId`. Unions papers that already have questions with `ProviderCatalogue` rows not yet cached. Catalogue-only papers have `cached: false` and `questionCount: null`. Each paper: `examType`, `examYear`, `subjectId`, `subjectName`, `subjectSlug`, `trackCategory`, `questionCount`, `cached`.

---

## Assessments

Generation, scoring, and lifecycle detail is [06-assessments.md](./06-assessments.md). Contracts:

### `POST /api/assessments/generate`

Auth. 20/minute per user.

Body `generateQuizSchema`:

- `subjectId` or `subjectSlug` (one required)
- optional `topicIds[]`, `topicSlug`, `difficulty`, `examType`, `examYear` (2001–2100), `title`
- `count` integer 5–60, default 10
- `untimed` boolean. Ignored when `examType` is set (past papers stay timed).

| Result | Status |
|---|---|
| quiz or resumed quiz | 200 |
| subject, topic, or no questions | 404 |
| provider fetch scheduled and bank empty | 503 `{ "error", "preparing": true }` |
| validation / rate / auth | 400 / 429 / 401 |

200 body: `{ assessmentId, attemptId, title, source, resumed?, totalQuestions, timeLimitMinutes, deadlineAt?, questions }`. Questions omit answers.

### `POST /api/assessments/submit`

Auth. Body `submitAssessmentSchema`:

- `attemptId`
- `answers`: max 200, unique `questionId`, each `{ questionId, selectedAnswer, timeSpentSeconds (0–86400), flaggedForReview? }`
- `awayEvents` optional 0–10000
- `practiceExit` optional `{ subjectSlug, topicSlug }` — topic practice launched from a lesson

| Outcome | Status |
|---|---|
| graded or replayed | 200, same shape as the attempt GET |
| not found / not owner | 404 |
| attempt no longer in progress and not completed | 409 |
| validation | 400 |

After a **new** grade (not a replay), `after()` runs `awardAchievements` and `markPlanFromAttempt`. If `practiceExit` was sent, `recordTopicPracticeResult`; on pass, `markPlanFromLesson`.

### `GET /api/assessments/attempts/{attemptId}`

Auth. Owner only. 200 result payload (answers revealed). 404 otherwise.

### Mock exams

`GET /api/assessments/mock-exam/boards` — auth. 200 `{ "boards": { WAEC, JAMB, NECO } }` each `{ board, ready, qualifying, started, required, reason }`. Ready when ≥ 3 subjects each have ≥ 20 syllabus-tagged questions.

`GET /api/assessments/mock-exam/options?examType=` — auth. `examType` must be `WAEC`, `JAMB`, or `NECO` else 400. 200 `{ examType, subjects }` with per class/term objective counts.

`POST /api/assessments/mock-exam/scoped` — auth, 12/minute. Body: `examType`, `subjectId`, `from` and `to` as `{ classLevel, term }`, `count` 5–80 default 40.

| Status | Body |
|---|---|
| 200 | paper payload plus `scope`, `subject`, `requestedCount`, `short`, timing. The internal `outcome` field is stripped. |
| 404 | subject |
| 422 | `{ "error", "reason": "NO_QUESTIONS_IN_SCOPE", "scope" }` |

### JAMB CBT

`GET /api/assessments/jamb-cbt/options` — auth. 200 `{ spec, english, englishYears, subjects }`. Spec constants are in the assessments doc.

`POST /api/assessments/jamb-cbt/prepare` — auth, 20/minute. Body `subjectIds` length **3**, `examYear` 1978–2100. Schedules provider fetches and reports coverage. 200 even when the year is not ready: `{ outcome: "ok", examYear, ready, message, coverage, shortfalls }`. 400 bad selection or subjects unavailable. 500 English subject missing from the bank.

`POST /api/assessments/jamb-cbt/generate` — auth, 6/minute. Same body. 200 full 180-question paper (or a resumed one). 422 `{ reason: "INSUFFICIENT_QUESTIONS", ... }` when coverage is short or the draw came up short. 400 / 500 as prepare.

---

## Flashcards

Every route: auth + entitlement `flashcards` (STANDARD). Algorithm in [09-flashcards.md](./09-flashcards.md).

| Method | Path | Success |
|---|---|---|
| GET | `/api/flashcards` | 200 `{ decks }` summaries |
| POST | `/api/flashcards` | 201 `{ deck }` authored deck |
| GET | `/api/flashcards/stats` | 200 `{ stats }` |
| GET | `/api/flashcards/recommendations` | 200 `{ recommendations }` |
| GET | `/api/flashcards/preview?lessonId=` | 200 preview, no writes. 40/minute. 404 unknown lesson |
| POST | `/api/flashcards/generate` | body `{ lessonId }`. 201 `{ deck, counts, cardCount }`. 404 lesson. 422 no cards produced |
| POST | `/api/flashcards/review` | body `{ flashcardId, rating, responseTimeMs?, objectiveCorrect? }`. 200 `{ outcome, review }`. 404 if the card is not owned or enrolled. `after()` marks the study plan |
| POST | `/api/flashcards/decks/{deckId}/enroll` | body `{ enrolled: boolean }`. 200 `{ deckId, enrolled }`. 404 |
| DELETE | `/api/flashcards/decks/{deckId}` | owner only. 200 `{ deckId }`. 403 not owner. 404 |

`rating` is `AGAIN` | `HARD` | `GOOD` | `EASY`.

---

## Study plan

Every route: auth + entitlement `studyPlanner`. Behaviour in [08-study-plan.md](./08-study-plan.md).

### `GET /api/study-plan`

May replan if the Lagos day has rolled. 200 page data: `today`, `classLevel`, `termLabel`, `termSource`, `daysToExam`, `defaults`, `subjects`, `plan` or null.

### `POST /api/study-plan`

Creates a plan, deactivates any active plan, replans immediately. 201 `{ planId }`. 400/404 with `{ error }` for settings or missing subjects.

Body `studyPlanSettingsSchema`:

- `subjectIds` 1–20 strings
- `studyDays` 1–7 integers, each 1–7
- `weekdayMinutes` 0–480, `weekendMinutes` 0–600
- `targetExam` and `targetDate` both present or both absent
- `forceExamMode` default false

Extra rules in `planSettingsProblem`: exam date is SS3 only; date must be in the future; at least one study day must have ≥ 30 minutes.

### `PATCH /api/study-plan`

Same settings shape, updates the active plan and replans. 200 `{ "ok": true }`. 404 if no active plan.

### `PUT /api/study-plan/positions`

Body `{ "positions": [{ "subjectId", "topicId": string | null }] }` max 20. Null topic clears the position. Subject must be on the plan. Topic must sit at or below the student’s class. 200 `{ "ok": true }` then replan.

### `PATCH /api/study-plan/items/{id}`

Body `{ "status": "COMPLETED" | "SKIPPED" | "PENDING" }`. Manual status rules (lookback, not rewriting future items) are in the planner doc. 200 `{ status }`. 400/404.

---

## Learning, lessons, library, achievements

### `POST /api/learning-path/topics/{topicId}/pretest`

Auth. Two modes in one route:

- Start: body `{}` or no `attemptId`. Builds a 5-question `TOPIC_QUIZ` (topic first, then subject to pad). 200 `{ attemptId, assessmentId, title, source, alreadyPassed, totalQuestions, threshold: 80, questions }` without answers.
- Grade: `{ attemptId, answers: [{ questionId, selectedAnswer?, timeSpentSeconds? }] }`. Completes the attempt. If percentage ≥ 80, sets `PerformanceMetric.pretestPassedAt` and writes `PRETEST_PASSED`. 200 `{ passed, alreadyPassed, percentage, correctCount, totalQuestions, threshold, recorded }`.

404 topic, no questions, or unknown attempt. 400 bad answers.

### `PATCH /api/lessons/{lessonId}/progress`

Auth. Body optional: `status`, `completionPercent` 0–100, `checkpointData`, `masteryScore`, `timeSpentMinutes`. Resolves subject and topic from the lesson. Forward-only upsert of `StudentProgress`. 200 `{ progress }`. 404 unknown lesson.

### `GET /api/library`

JWT `sub` required (lighter than `auth()`). No `subjectId`: shelf for the student’s track. With `subjectId`: resource list. Rows with `isFree=false` are locked unless the tier can `premiumLibrary`: response sets `url: ""` and `locked: true`. Locked rows are still returned.

### `GET /api/achievements`

Auth. 200 `{ achievements: [...with earned and earnedAt], earned }`.

### `POST /api/achievements`

Auth. Runs `awardAchievements` (also run after submit). 200 `{ "checked": true, "newlyEarned": ["title", ...], "count" }`.

Metrics, computed at most once per call, only for criteria the student has not earned:

| `criteriaType` | Passes when |
|---|---|
| `questions_answered` | count of `QuestionResponse` for the student ≥ `criteriaValue` |
| `perfect_score` | any completed attempt with `percentage >= 100` |
| `streak_days` | Lagos-day streak of completed attempts ≥ `criteriaValue` (last 400 attempts) |
| `lessons_completed` | completed lesson progress count ≥ `criteriaValue` |
| `subject_mastery` | count of subjects at `STRONG` ≥ `criteriaValue` |
| `mock_score_70` | any completed `MOCK_EXAM` or `CBT_PRACTICE` with `percentage >= 70` |

Inserts use `createMany` with skip-duplicates.

### `POST /api/announcements/{id}/dismiss`

Auth. Upserts `AnnouncementDismissal` if the announcement exists. 200 `{ "ok": true }`. 404 if it does not.

---

## Billing and push

### `POST /api/billing/checkout`

Auth. 10/minute. Body `{ tier, period }` where `tier` is a purchasable tier and `period` is `MONTHLY` or `YEARLY`. Full flow in [10-billing.md](./10-billing.md).

| Status | Body |
|---|---|
| 200 | `{ "authorizationUrl" }` |
| 400 | validation, freemium, or user has no email |
| 503 | billing disabled |
| 502 | Paystack initialize failed |

### `GET /api/billing/callback?reference=`

No session. Browser return from Paystack. Verifies, applies, then **302** to `/settings/billing?status=success|failed|pending|missing`.

### `POST /api/billing/webhook`

No session. HMAC on the raw body. See billing doc. 200 `{ "received": true }` or with `duplicate` / `outcome`. 401 bad signature. 400 malformed JSON. 500 after deleting the idempotency row so Paystack retries.

### `POST /api/push/subscription`

Auth. 10/minute. Body: `endpoint` (https URL), `keys.p256dh`, `keys.auth` (base64url length bounds in `push-validators.ts`). Upsert by endpoint; may reassign the row to this user; stores `deviceId` from the session. 200 `{ "ok": true }`.

### `DELETE /api/push/subscription`

Auth. Body `{ "endpoint" }`. Deletes only if the row belongs to this user. 200 `{ "ok": true }`.

---

## Cron

Detail in [11-push-and-cron.md](./11-push-and-cron.md). All three are `POST`, bearer-authenticated, and return **204** when push is not configured.

| Path | Purpose | 200 body |
|---|---|---|
| `/api/cron/push/drain` | send queued announcement deliveries | `{ claimed, sent, failed, gone, retrying }` |
| `/api/cron/push/morning` | study-plan and due-card digest | `{ processed, notified, sent, skipped, done }` |
| `/api/cron/push/streak` | evening streak reminder | same shape as morning |

Intended schedule (comments on the routes, not enforced in code): drain every minute; morning every 5 minutes from 07:00–07:55 Lagos; streak every 5 minutes from 19:00–19:55 Lagos. `maxDuration` on the Next routes is 60s; the in-handler budget is 40s.
