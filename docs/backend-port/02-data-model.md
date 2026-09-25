# Data model

Postgres enums and tables are defined in [`prisma/schema.prisma`](../../prisma/schema.prisma). This file explains what each table is for, which invariants the application relies on, and which columns are denormalised caches. A FastAPI rewrite should map these tables, not redesign them, if it is going to share the database with the current app during the cutover.

IDs are `cuid` strings unless noted. `onDelete: Cascade` is stated where losing the parent must delete children. Where it is omitted, Prisma’s default is `Restrict`.

## Enums

| Enum | Values |
|---|---|
| `Role` | `STUDENT`, `TEACHER`, `ADMIN`. Only `STUDENT` is assigned. `ADMIN` cannot be dropped in Postgres; real admins are the `Admin` table. `TEACHER` is not accepted by registration. |
| `ClassLevel` | `SS1`, `SS2`, `SS3` |
| `Track` | `SCIENCE`, `ARTS`, `COMMERCIAL` |
| `TrackCategory` | `CORE`, `SCIENCE`, `ARTS`, `COMMERCIAL`, `VOCATIONAL` (subject catalogue, not the student) |
| `SubscriptionTier` | `FREEMIUM`, `STANDARD`, `PREMIUM` |
| `BillingPeriod` | `MONTHLY`, `YEARLY` |
| `SubscriptionSource` | `PAYSTACK`, `COMP` |
| `SubscriptionStatus` | `PENDING`, `ACTIVE`, `FAILED`, `ABANDONED`, `REVOKED` |
| `Term` | `FIRST`, `SECOND`, `THIRD` |
| `ExamType` | `WAEC`, `JAMB`, `NECO`, `CUSTOM` |
| `QuestionType` | `OBJECTIVE`, `THEORY`, `FILL_IN_BLANK`. Quiz generation selects `OBJECTIVE` only. |
| `Difficulty` | `BASIC`, `INTERMEDIATE`, `ADVANCED` |
| `QuestionProvider` | `SDASH` |
| `ProviderFetchStatus` | `PENDING`, `SATURATED`, `FAILED` |
| `ProviderQuestionStatus` | `PENDING`, `PROMOTED`, `REJECTED` |
| `ProviderCircuitState` | `OK`, `EXHAUSTED`, `BLOCKED` |
| `AssessmentType` | `TOPIC_QUIZ`, `SUBJECT_TEST`, `PAST_PAPER`, `MOCK_EXAM`, `CBT_PRACTICE`, `CUSTOM` |
| `ProgressStatus` | `NOT_STARTED`, `IN_PROGRESS`, `COMPLETED` |
| `AttemptStatus` | `IN_PROGRESS`, `COMPLETED`, `ABANDONED`, `TIMED_OUT` |
| `PlanItemActivity` | `LESSON`, `PRACTICE`, `REVISION`, `MOCK_EXAM`, `PAST_QUESTIONS` |
| `PlanItemStatus` | `PENDING`, `COMPLETED`, `SKIPPED`, `MISSED` |
| `PlanCompletionSource` | `AUTO`, `MANUAL` |
| `SchoolType` | `PUBLIC`, `PRIVATE` |
| `MasteryLevel` | `WEAK`, `DEVELOPING`, `COMPETENT`, `STRONG` |
| `FlashcardType` | `DEFINITION`, `FORMULA`, `IMAGE`, `DIAGRAM`, `FILL_IN_BLANK`, `COMPARE_CONTRAST`, `TRUE_FALSE`, `SCENARIO`, `PROCESS` |
| `FlashcardSource` | `AUTHORED`, `LESSON`, `AI` (`AI` is unused by current generators) |
| `FlashcardState` | `NEW`, `LEARNING`, `REVIEW`, `RELEARNING` |
| `ReviewRating` | `AGAIN`, `HARD`, `GOOD`, `EASY` |
| `EdgeKind` | `PREREQUISITE`, `STRONG_RELATED`, `RELATED` |
| `LearningEventKind` | `QUESTION_ANSWERED`, `QUIZ_ABANDONED`, `LESSON_BLOCK_COMPLETED`, `LESSON_COMPLETED`, `CARD_REVIEWED`, `PRETEST_PASSED` |
| `MaterialType` | `PDF`, `IMAGE`, `VIDEO`, `LINK` |
| `AnnouncementStatus` | `QUEUED`, `SENDING`, `SENT`, `CANCELLED` |
| `DeliveryStatus` | `PENDING`, `SENT`, `FAILED`, `GONE`, `CANCELLED` |

## Identity and account

### `User`

Student (and any future teacher). Important columns:

- `email` unique, nullable (Google can exist before an email is stored; credentials login requires one). Stored lowercased by registration.
- `phone` unique, nullable. Nigerian format `^(\+234|0)[789]\d{9}$`. Empty string is stored as NULL so the unique index does not collide on blanks.
- `passwordHash` nullable. Null means Google-only; password change returns `no-password`.
- `role` default `STUDENT`.
- `classLevel`, `track`, `state` nullable until `/complete-profile`. Profile is complete only when all three are set and `state` is in the Nigerian list (`src/lib/profile-completion.ts`).
- `tier` default `FREEMIUM`, plus `tierUpdatedAt`. **Denormalised.** Writers: Paystack settlement and admin tier changes. Readers: JWT cache and entitlement checks. Entitlement denial re-reads this column because the JWT can be up to 60s stale.
- `isActive` default true. `suspendedAt`, `suspendedReason`. Inactive users cannot pass credentials login, and live JWTs are revoked on the next profile refresh.
- `sessionsValidFrom`. Tokens whose issue time is `<=` this instant are rejected. Null means “no force sign-out”. Force sign-out sets it to now. It does not change the password.

Relations: progress, attempts, plans, metrics, achievements, OAuth `Account`s, unused `Session`s, learning events, topic mastery, flashcards, devices, push, subscriptions.

Indexes: `(classLevel, track)`, `tier`, `isActive`, `state`.

### `Account`

Auth.js OAuth link. Unique `(provider, providerAccountId)`. Cascade-deletes with the user. Stores Google tokens. Credentials users have no row.

### `Session`, `VerificationToken`

Adapter tables. The app never reads them for authorization. Email verification is not implemented; `User.emailVerified` is unused.

### `UserDevice`

One browser session. `id` is copied onto the student JWT as `deviceId`.

- `label` is a display string captured at registration.
- `lastSeenAt` updated at most every 15 minutes.
- `revokedAt` set when the device is over the limit, the user signs it out, or an admin/password flow revokes it.
- Index `(userId, revokedAt)`.

### `School`

Optional `User.schoolId`. Not written by current student or admin profile routes (admin student schema deliberately omits `schoolId`).

## Curriculum

### `Subject`

`name`, `slug`, and `code` are each unique. Flags `isWaec`, `isJamb`, `isNeco`. `trackCategory` decides who sees the subject: students see `CORE` plus their own track (`src/lib/subjects.ts`). No track on the user means the unfiltered catalogue.

### `SubjectResource`

Library item. `isFree` default true. Premium gating is applied at read time (URL blanked), not by omitting the row. `orderIndex` is `max(existing)+1` on create. Cascade with subject.

### `CurriculumLevel`

One row per `(subjectId, classLevel, term)`. Topics hang off a level, which is how “this topic is SS2 second term” is expressed. Nine slots exist in code: SS1–SS3 × three terms (`src/lib/curriculum-scope.ts`).

### `Topic`

Unique `(subjectId, slug)`. `orderIndex` within the subject. `estimatedMinutes` default 45. `waecWeight` and `jambWeight` (floats, default 0) feed the exam-mode planner.

`prerequisiteTopicId` is a single-parent chain (“PrerequisiteChain”). The graph used by the path engine is `TopicEdge`, which can express weighted prerequisites. Both exist; path logic uses edges.

### `TopicEdge`

Unique `(prereqTopicId, topicId)`. `kind` default `PREREQUISITE`. `strength` default 1, meaning “the prerequisite must reach 100% of the gate”. `rationale` is author-facing copy.

### `Subtopic` → `Lesson` → `LessonResource`

A topic’s classroom lesson is the canonical lesson under a subtopic (import creates `"Core Concepts"` if none exists).

`Lesson.content` is markdown/MDX text. Structured fields are JSON:

- `blocks` — ordered lesson blocks (concept, diagram, example, tip, mistake, mnemonic, check)
- `keyPoints`, `workedExamples`, `examTips`, `mnemonics`, `knowledgeChecks`, `prerequisites`, `revisionDays`

`createdBy` is `"system"` for seeded lessons. Admin browse returns markdown only when `createdBy !== "system"`. `passMarkPercent` default 60. `practiceCount` default 7. `revisionDays` default interpreted as `[1, 3, 7, 14]` when null.

`LessonResource.resourceType` is a free string (`image`, `video`, `pdf`, `diagram`), not the `MaterialType` enum.

## Questions and attempts

### `Question`

`options` is JSON `{ "A": "...", "B": "...", ... }`. `correctAnswer` is the key. `marks` default 1. `timeEstimateSeconds` default 90.

Indexes that generation depends on: `(subjectId, questionType)`, `(subjectId, questionType, examType)`, `(subjectId, topicId)`, `(examType, examYear)`, `(subjectId, examType, examYear)`, `(subjectId, difficulty)`.

Deletion is refused while `QuestionResponse` or `AssessmentQuestion` rows exist.

### `Assessment`

A generated paper. `totalMarks` is the question count for ordinary quizzes and **400** for JAMB CBT. `timeLimitMinutes` null means untimed. `passMarkPercent` default 50 (JAMB papers set 50 explicitly). `createdBy` is not the student id in current generation (attempts carry `studentId`).

### `AssessmentQuestion`

Join. Unique `(assessmentId, questionId)`. `orderIndex` is the paper order. Cascade with the assessment. Question delete is restricted by this FK plus the usage check.

### `AssessmentAttempt`

`studentId` + `assessmentId`. Status machine: `IN_PROGRESS` → `COMPLETED` (graded) or `TIMED_OUT` (reaped). `ABANDONED` exists in the enum; the reaper writes `TIMED_OUT` and records abandonment as a learning event, not as this status.

`awayEvents` is client-reported focus-loss count, clamped by the submit schema (0–10000). Score columns are null until completion.

Indexes: `(studentId, assessmentId)`, `(studentId, status)`, `(studentId, status, completedAt)`.

### `QuestionResponse`

Unique `(attemptId, questionId)`. `selectedAnswer` nullable (skipped). `isCorrect` compared as string equality with `correctAnswer`. `flaggedForReview` default false.

## Progress

### `StudentProgress`

Unique `(studentId, subjectId, topicId, lessonId)`. Lesson checkpoint:

- `status`, `completionPercent` (0–100), `timeSpentMinutes`, `lastAccessedAt`
- `checkpointData` JSON: visited blocks, check results, practice
- `masteryScore` last computed 0–100
- `revisionDueAt`

Writes are forward-only: a `COMPLETED` row is not demoted, and `completionPercent` does not decrease (`src/lib/lesson-progress-rules.ts`).

### `PerformanceMetric`

Unique `(studentId, subjectId, topicId)`. `masteryLevel` default `WEAK`. `pretestPassedAt` set when a 5-question pretest scores ≥ 80%. This is the readiness flag the path engine reads; it is separate from `TopicMastery`.

## Learning evidence

### `LearningEvent`

Append-only. Primary key `seq` bigint identity. Never updated. Deleted only by user cascade.

Columns: `studentId`, `subjectId`, `topicId?`, `kind`, `correct?`, `score?` (0..1 for non-binary evidence), `difficulty?`, `seconds?`, `sourceId?` (question, lesson, or card id — audit only), `occurredAt`.

Indexes: `(studentId, topicId, seq)`, `(studentId, seq)`.

### `TopicMastery`

Composite PK `(studentId, topicId)`. Decayed sufficient statistics, not a second source of truth. Reset `cursorSeq` to 0 to force a full replay. A row whose `scoringVersion` ≠ `SCORING_VERSION` (currently **2** in `src/engines/learning/evidence.ts`) is refolded from cursor 0. The column default of 1 is intentional and inert: the store always writes the constant.

Per channel (`acc`, `lesson`, `srs`): weighted outcome, weighted mass, and an observation count. Observations are **not** decayed. `decayAnchor` is the instant the sums are decayed to. `lastEffortAt` ignores mere lesson access. `cursorSeq` is the highest event already folded.

## Study plan

### `StudyPlan`

One active plan per student is an application rule (`isActive`), enforced by deactivating previous rows on create, not by a partial unique index.

- `subjectIds` JSON string array (1–20 in the API)
- `targetExam` + `targetDate` both set or both null
- `forceExamMode` default false
- `studyDays` int array, ISO weekday 1=Monday … 7=Sunday, default all seven
- `weekdayMinutes` / `weekendMinutes`
- `plannedThrough` date, `lastReplannedAt`
- `outline` JSON `OutlineWeek[]`, `overload` JSON
- Index `(studentId, isActive)`

### `StudyPlanItem`

Calendar date (`@db.Date`), subject, optional topic, `activityType`, `durationMinutes`, `status`, optional `notes`, `completedAt`, `completionSource`, `carriedFromDate` (item moved forward from a missed day).

### `StudyPlanPosition`

“My class is on this topic.” Unique `(studyPlanId, subjectId)`. Overrides the calendar guess for that subject. Topic must be at or below the student’s class.

### `AcademicTerm`

App-wide school calendar. Unique `(session, term)`. `session` is `YYYY/YYYY` with consecutive years. `startsOn` / `endsOn` are dates. Ranges must not overlap. If no configured term covers “now” or the next 60 days, the planner falls back to a built-in Nigerian calendar (`src/engines/planner/term-context.ts`).

## Gamification and reference

### `Achievement` / `StudentAchievement`

Catalogue row: unique `title`, `criteriaType` string, `criteriaValue` int. Earned unique `(studentId, achievementId)`. Criteria implemented in `awardAchievements`: `questions_answered`, `perfect_score`, `streak_days`, `lessons_completed`, `subject_mastery`, `mock_score_70`.

### `JambCombination`

Reference data (`courseOfStudy`, `faculty`, `requiredSubjects` JSON, optional alternates). Not written by API routes.

## Flashcards

### `FlashcardDeck`

`source` `AUTHORED` or `LESSON`. Unique `(lessonId, source)` so a lesson has one generated deck. `createdBy` is the owner; only the owner can delete. `slug` optional.

### `Flashcard`

`payload` JSON is the card body. `sourceKey` identifies the lesson block. Unique `(deckId, sourceKey)` — Postgres unique indexes allow multiple NULLs, so authored cards with null keys coexist. Re-sync matches on `sourceKey` and updates in place so SRS state survives.

### `FlashcardReview`

Per `(studentId, flashcardId)`. Scheduling state: `state`, `easeFactor` default 2.5, `stability` days, `difficulty` 1–10 default 5, `intervalDays`, `repetitions`, `lapses`, `retention` (predicted recall at schedule time), `dueAt`, `lastReviewedAt`.

### `FlashcardReviewLog`

Append-only review. `rating`, optional `responseTimeMs`, `scheduledDays`, `objectiveCorrect`.

### `FlashcardEnrollment`

Unique `(studentId, deckId)`. Enrollment grants review rights, not delete rights.

## Admin

### `Admin`

Separate from `User`. `email` and `username` each unique and nullable (at least one is set by the identifier parser). `passwordHash` required. `isOwner` default false. `isActive` default true. `lastLoginAt`. Self-relation `createdById`.

No `Account` / `Session` rows. JWT only.

### `AdminAudit`

`actorId` cascades with the admin. `action` and `entity` are strings (not enums). `entityId` optional. `summary` text. Failures to write the audit are swallowed so the mutation still commits (`src/lib/admin-audit.ts`).

## Question provider

### `ProviderFetch`

Coverage ledger. Unique `(provider, cacheKey)`. The row’s existence is the cache hit, not the presence of questions. Status `PENDING` means in flight or not yet saturated; `SATURATED` and `FAILED` are terminal until an admin resets a failure. `startedAt` doubles as the lease timestamp (see provider doc). Counts: `drawCount`, `rawCount`, `newInLastDraw`, `promotedCount`, `rejectedCount`. Optional `subjectId`, `examType`, `examYear` denormalised from the key.

### `ProviderQuestion`

Verbatim payload, written before mapping. Unique `(fetchId, providerQuestionId)` and `(fetchId, fingerprint)`. Dedupe is **per fetch**, not global: the same stem in WAEC 2019 and WAEC 2021 must stage twice. `questionId` unique when promoted. `mapperVersion` lets a later mapper reprocess `PENDING` rows.

### `ProviderCatalogue`

Papers the provider claims to hold, filled by `scripts/sync-provider-catalogue.ts`, not by the request path. Unique `(provider, subjectId, examType, examYear)`. The past-paper picker unions these with papers already in `Question`.

### `ProviderState`

One row per provider (PK = provider). Circuit breaker plus last `creditsRemaining`.

## Billing

### `Subscription`

Source of truth for paid access. `reference` unique, prefixed `pw_` plus a UUID without hyphens. `amountKobo`, `currency` default `NGN`, optional `channel`. `paidAt`, `startsAt`, `endsAt`. Comp rows set `source=COMP`, `amountKobo=0`, `grantedById`, optional `note`.

Indexes: `(userId, endsAt)`, `status`.

### `PaystackEvent`

PK `eventKey` = `{reference}:{eventType}`. Insert collision means duplicate delivery. Payload stored as JSON. On a failed apply, the row is deleted so Paystack’s retry is not treated as a duplicate.

## Push

### `PushSubscription`

Unique `endpoint`. Same browser re-subscribing updates the row and may move `userId` to the latest account. `p256dh`, `auth` are the Web Push keys. `failureCount` consecutive non-gone failures; deleted at 5. `deviceId` is `UserDevice.id` but **not a foreign key** (older tokens have none). Revoking the device deletes matching subscriptions explicitly.

### `NotificationPreference`

PK `userId`. Missing row means all toggles on: `studyReminders`, `streakReminders`, `announcements`. Announcements toggle affects push only; the in-app banner still shows.

### `Announcement`

Created by an admin. `audience` JSON filter. `expiresAt` = now + `expiresInDays` (1–30, default 7). Counts: `recipientCount`, `sentCount`, `failedCount`. Status starts `QUEUED`. Zero recipients becomes `SENT` immediately.

### `AnnouncementDelivery`

Unique `(announcementId, subscriptionId)`. `subscriptionId` is **not** a foreign key. `claimedAt` is the worker lease. Attempts counted; max 3.

### `AnnouncementDismissal`

PK `(announcementId, userId)`.

### `ReminderLog`

PK `(userId, kind, dayKey)`. `kind` is `morning` or `streak`. `dayKey` is the Lagos date. A row means “processed today”, whether or not a push was sent. Insert is `ON CONFLICT DO NOTHING`, which is the claim.
