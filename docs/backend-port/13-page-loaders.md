# Page loaders that are not HTTP routes

The dashboard, classroom, performance, settings, and parts of flashcards load through server components. They call the functions below directly. A FastAPI backend that replaces Prisma-in-Next must expose equivalent reads, or those pages stay coupled to the database.

None of these are `"use server"` actions. They assume the caller already has a user id from `auth()` and has passed the layout gates (signed in, profile complete, device not revoked).

Suggested paths are proposals so the frontend has something stable to call. Match the return objects the pages already destructure; do not invent fields.

## Dashboard

`getDashboardData(userId)` in `src/lib/dashboard.ts`. Page size of recent attempts: `DASHBOARD_ATTEMPTS_PAGE_SIZE = 5`.

Includes, broadly: greeting name, streak (Lagos days of completed attempts), tier, continue-learning recommendation (`keepLearning`), gap summary, today’s plan items if a plan exists, recent attempts, achievement highlights. It calls the learning engines and the planner read models. Port it as one aggregate endpoint, for example `GET /api/dashboard`, rather than making the client reassemble it.

## Performance

`getPerformanceData` in `src/lib/performance.ts`. Attempt page size 10. Grade letters on this page are A/B/C/D/F at 75/65/50/40, not A1–F9.

Subject drill-in uses `src/lib/analytics/subject-view.ts`, which runs the analytics engines in [07-learning.md](./07-learning.md). `advancedAnalytics` (PREMIUM) gates the richer subject view in the UI. Preserve that check on the endpoint, not only in the component.

## Classroom

| Function | Page | Proposed route |
|---|---|---|
| `getClassroomSubjects` | `/classroom` | `GET /api/classroom/subjects` |
| `getSubjectPageData` | `/classroom/{subjectSlug}` | `GET /api/classroom/subjects/{slug}` |
| `getTopicPageData` | topic overview | `GET /api/classroom/subjects/{slug}/topics/{topicSlug}` |
| `getTopicStudyData` | study | `.../study` |
| `getTopicQuizData` | quiz launcher | `.../quiz` |
| `getTopicPracticeData` | practice launcher | `.../practice` |

Files: `src/lib/classroom-data.ts`, `src/lib/classroom-topic.ts`. Pure helpers in `src/lib/classroom.ts` (`toNotes`, neighbour topics, resource picking) can stay in whichever process renders notes.

Subject page data includes the topic graph coloured by path state (LOCKED / READY / STARTED / MASTERED / DECAYED), per-topic mastery, and which lesson is canonical. Topic study data includes lesson blocks, checkpoint, and unlock flags. Quiz and practice launchers do not create attempts; creation is `POST /api/assessments/generate` and the pretest route.

`getTopicPracticeResult` is a read of the last practice outcome for the results screen. The write is submit’s `practiceExit`.

## Library and catalogue

| Function | Route today | Gap |
|---|---|---|
| `getLibraryShelf` / `getLibraryShelfTolerant` | `GET /api/library` | shelf is already routed |
| `getSubjectResources` | `GET /api/library?subjectId=` | already routed, including the premium URL blanking |
| `getSubjectCatalogue` | `GET /api/subjects` | public catalogue |

The library page can keep using `/api/library`. No new route is required unless you want the shelf shape and the resource shape split.

## Settings

`getSettingsProfile` in `src/lib/settings.ts`: name, email, phone, state, class, track, image, tier, tier expiry, devices, notification preferences, whether the account has a password (Google-only hides the password form). Writes already exist (`/api/user/*`, billing checkout). Add `GET /api/user/profile` (or `/api/settings`) for the read. The current profile route is PATCH only.

Device list for the settings UI is loaded here, not via `POST /api/user/devices` (that route only revokes).

## Achievements

`getStudentAchievements` (page) and `getAchievementsApiPayload` (`GET /api/achievements`) are different shapes. The page includes locked/earned presentation fields; the API payload is the slimmer `{ achievements, earned }`. Either serve the page shape from the existing GET or add a query flag. Awarding stays `POST /api/achievements` and the submit side effect.

## Flashcards

`GET /api/flashcards` returns deck summaries. The study screen uses `getDeckPageData` / `getStudyQueue` (due then new, daily new budget 20). Add `GET /api/flashcards/decks/{deckId}` for that payload if the client cannot import `src/lib/flashcards.ts`.

## Announcements banner

The dashboard banner is not the push payload. It reads active announcements the student has not dismissed. Look up the query in `src/lib/announcement-data.ts` (student-facing selector, separate from the admin list of 50). Expose `GET /api/announcements` for undismissed, unexpired announcements visible to this user’s audience. Dismiss stays `POST /api/announcements/{id}/dismiss`.

## Public SEO pages

`/learn/...` and `/past-questions/...` are server-rendered and anonymous (`src/lib/seo/*`). They read subjects, topics, and sample questions for crawlers. If Next remains the website, leave those queries in Next. If FastAPI becomes the only database client, add public read endpoints that return the same fields and **do not** include `correctAnswer` on samples intended for HTML (check `src/lib/seo/samples.ts` and `question-scope.ts` before copying the admin list behaviour).

## Admin pages

The admin console’s tables (student search, audit log, question browser, lesson browser, dashboard stats) are also server components: `src/lib/admin-data.ts`, `admin-student-data.ts`, `admin-audit-data.ts`, `admin-question-data.ts`, `admin-stats.ts`, `admin-lesson-browse.ts`. Mutations are already HTTP. Reads to add if the console moves off Prisma:

| Read | Module |
|---|---|
| Student search and detail | `admin-student-data.ts` |
| Audit log with the filter subset | `admin-audit-data.ts`, `admin-audit-filter.ts` |
| Question list (also `GET /admin/api/questions`) | already routed |
| Lesson browse tree | `admin-lesson-browse.ts` |
| Console home counts | `admin-stats.ts` |

Student detail must not leak `passwordHash`. Audit reads are append-only views of `AdminAudit`.

## Scripts that stay out of the request path

These are operator tools. A FastAPI port does not need HTTP wrappers unless you want them in the admin UI (backfill already has one).

| Script | Role |
|---|---|
| `prisma/seed.ts` | seed curriculum, achievements, admin owner |
| `scripts/import-questions.ts` | bulk questions |
| `scripts/sync-provider-catalogue.ts` | fill `ProviderCatalogue` |
| `scripts/repromote-provider-questions.ts` | re-run promotion |
| `scripts/seed-flashcards.ts` | seed decks |

## Cutover note

Until these reads move, the Next app still needs `DATABASE_URL` and will keep its own Prisma client. The risky window is dual writers (Next submit and FastAPI submit). Pick one writer per table before turning on the second service: attempts, learning events, subscriptions, and push deliveries are the ones where double-writes corrupt state.
