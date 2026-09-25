# Admin HTTP API

Base path `/admin/api`. Cookie and owner rules are in [03-auth-and-access.md](./03-auth-and-access.md).

Default errors:

- **401** `{ "error": "Unauthorized" }` — missing cookie (proxy) or inactive/unknown admin (`requireAdminApi` / `requireOwnerApi`)
- **403** `{ "error": "Owner access required" }` — `requireOwnerApi` when the admin is active but not owner
- **403** `{ "error": "Not permitted" }` — route checked a capability and it failed

Audit writes never fail the mutation. If the insert throws, it is logged and swallowed.

Catalogue invalidation: question create/update/delete/import, lesson import, and provider backfill call `revalidateTag("catalogue")`. The Python service must drop the same cache.

## Auth

`GET` and `POST /admin/api/auth/*` — Auth.js handlers for the admin instance. Successful credentials login updates `lastLoginAt`.

## Team (owner)

### `GET /admin/api/admins`

200 `{ "admins": [{ id, email, username, isOwner, isActive, lastLoginAt, createdAt }] }`.

### `POST /admin/api/admins`

Body `{ "identifier": string min 3, "password": string min 12 }`. Identifier becomes email or username (see auth doc). Always `isOwner: false`. bcrypt cost 12.

| Status | Body |
|---|---|
| 201 | `{ "admin": TeamAdminRow }` |
| 400 | validation or identifier message |
| 409 | `{ "error": "That email or username is already taken" }` |

Audit `admin.create` / entity `Admin` / summary `Created admin {email or username}`.

### `PATCH /admin/api/admins/{id}/status`

Body `{ "isActive": boolean }`. Deactivating an owner → **403** `{ "error": "The owner account cannot be deactivated" }`. 200 `{ "ok": true }`. 404 unknown admin. Audit `admin.deactivate` or `admin.reactivate`.

## Students

### `PATCH /admin/api/students/{id}`

Any active admin. Body `studentProfileSchema`: required `firstName`, `lastName`; optional `email`, `phone`, `classLevel`, `track`, `state`. No `tier`, no `schoolId`.

200 `{ "ok": true }`. 404. 400 with `details`. 409 duplicate email or phone. 500 generic save failure. Audit `student.update` with a summary of changed fields.

### `DELETE /admin/api/students/{id}`

Owner. Audit `student.delete` is written **before** the delete, including impact counts, because cascade will remove the evidence. Then `User` delete cascades attempts, events, devices, subscriptions, push, and so on. 200 `{ "ok": true }`. 404.

### `POST /admin/api/students/{id}/status`

Any active admin. Body `{ "isActive": boolean, "reason"?: string }`. Suspending (`isActive: false`) requires `reason` length 3–500. Sets or clears `suspendedAt` / `suspendedReason` together with `isActive`. 200 `{ "ok": true }`. Audit `student.suspend` or `student.reactivate`.

### `POST /admin/api/students/{id}/tier`

Any active admin. Body `{ "tier": "FREEMIUM"|"STANDARD"|"PREMIUM", "period"?: "MONTHLY"|"YEARLY" default MONTHLY, "note"?: string max 280 }`.

This does **not** write `User.tier` by itself as the only change:

- `FREEMIUM` → every `ACTIVE` subscription for the user becomes `REVOKED`, then tier resolution writes `User.tier`.
- `STANDARD` or `PREMIUM` → `grantComp`: a `Subscription` with `source=COMP`, `amountKobo=0`, `status=ACTIVE`, `grantedById` = acting admin, `startsAt`/`endsAt` from term arithmetic, optional `note`. Then `User.tier` is set.

Audit `student.tier`. 200 `{ "ok": true }`.

### `POST /admin/api/students/{id}/force-signout`

Owner. Sets `sessionsValidFrom = now` and deletes all `PushSubscription` rows for the user. Does not rotate the password. Audit `student.force_signout`. 200 `{ "ok": true }`.

## Questions

### `GET /admin/api/questions`

Query: `page` default 1 min 1, `pageSize` default 20 clamped 1–100, optional `subjectId`, `examType`, `examYear`, `difficulty`, `search`.

200 `{ questions, pagination: { page, pageSize, total, totalPages } }`. 500 `{ "error": "Failed to list questions" }`.

### `POST /admin/api/questions`

Body is the create schema below. Unknown subject or topic that does not belong to the subject → 400. 201 `{ "id" }`. Audit `question.create`. Bust catalogue.

### `GET /admin/api/questions/{id}`

200 full question with subject and topic. 404. 500.

### `PATCH /admin/api/questions/{id}`

Partial shape, at least one field. Invariants are checked on the **merged** row, so a patch that changes `options` without resending `correctAnswer` still has to leave a valid objective question. 200 `{ "id" }`. 404. 400 (may include `issues`). Audit `question.update` listing field names. Bust catalogue.

### `GET /admin/api/questions/{id}/usage`

200 `{ responseCount, assessmentCount, deletable }`. `deletable` is false when either count is non-zero.

### `DELETE /admin/api/questions`

Ids from repeated `?id=` or body `{ "ids": string[] }` length 1–100. 400 if neither is present.

Deletes what it can. Refuses rows that have responses or assessment slots. 200 `{ deleted, refused: [{ id, responseCount, assessmentCount }], notFound }`. Audit `question.delete` only when `deleted` is non-empty. Bust catalogue if anything was deleted.

### `POST /admin/api/questions/import`

Body `{ "questions": [ ... 1 to 500 ], "skipDuplicates"?: boolean default true }`.

Each row uses `subjectCode` and optional `topicSlug` rather than ids. The topic must belong to that subject. A duplicate, when skipping, is the same `subjectId + examType + examYear + questionText`.

200 `{ message, imported, skipped, errors: [{ index, reason }] }`. Audit `question.import` with no `entityId`. Bust catalogue if `imported > 0`.

The admin UI’s paste parser accepts a bare JSON array or `{ questions }`. The route itself only accepts the object schema.

### Question field schema

| Field | Create | Update |
|---|---|---|
| `subjectId` | required | optional |
| `topicId` | nullish | optional |
| `examType` | `WAEC\|JAMB\|NECO\|CUSTOM` | optional |
| `examYear` | 1990–2030, nullish | optional |
| `questionNumber` | ≥ 1, nullish | optional |
| `questionText` | min 5 | optional |
| `questionImageUrl` | url, nullish | optional |
| `questionType` | default `OBJECTIVE` | optional |
| `options` | record, nullish | optional |
| `correctAnswer` | required | optional |
| `explanation` | min 5 | optional |
| `explanationImageUrl` | url, nullish | optional |
| `difficulty` | default `INTERMEDIATE` | optional |
| `marks` | default 1 | optional |
| `timeEstimateSeconds` | default 90 | optional |

Objective invariant (`src/lib/admin-question.ts`): at least 4 options, and `correctAnswer` uppercased must be one of the option keys.

Import rows share the text/options rules and add `subjectCode` plus optional `topicSlug`.

## Lessons

### `GET /admin/api/lessons/{topicId}`

200 `{ topicTitle, lesson: null | { title, blockCount, authored, updatedAt, markdown } }`. `markdown` is included only when the lesson was authored (`createdBy !== "system"`). 404 `{ "error": "Unknown topic" }`.

### `POST /admin/api/lessons/import`

Body `{ "topicId", "markdown" (1–200_000 chars), "confirm": true }`. `confirm` must be the literal `true`.

The server re-parses markdown with `validateLessonMarkdown`. It does not trust a client-supplied block array. It creates subtopic `"Core Concepts"` if the topic has none, then updates the canonical lesson or creates it.

200 `{ message, lessonId, blockCount, warnings }`. 400 validation or markdown issues. 404 unknown topic. Audit `lesson.import` / entity `Lesson`. Bust catalogue.

Markdown rules live in `src/lib/lesson-markdown/`. Port that parser if admins will keep authoring in the same format: frontmatter, fenced blocks, id generation, and SVG sanitising (`svg-sanitiser.ts`). The classroom renders `blocks` JSON; `content` markdown is the authoring source.

## Materials

### `GET /admin/api/materials?subjectId=`

400 if `subjectId` is missing. 200 ordered `SubjectResource` rows.

### `POST /admin/api/materials`

Body: `subjectId`, `title` 2–200, optional `description` ≤ 600, `resourceType` `PDF|IMAGE|VIDEO|LINK`, `url`, optional `author` ≤ 120, `isFree` default true. URL checked per type. `orderIndex = max + 1`. 201 the row. Audit `material.create` / entity `SubjectResource`.

### `PATCH /admin/api/materials/{id}`

Partial. URL is validated against the merged type (a type change and a URL change in one patch must agree). Audit `material.update`. 200 row. 404.

### `DELETE /admin/api/materials/{id}`

Audit, then delete. 200 `{ "ok": true }`. 404.

### `POST /admin/api/materials/sign`

Body `{ "type": MaterialType }`. Only `PDF` and `IMAGE` (`requiresUpload`). `VIDEO` and `LINK` are URL-only and return 400.

Signing parameters (`src/lib/admin-material.ts`, `src/lib/cloudinary.ts`):

| Type | Folder | Formats | Max bytes (advisory) | Cloudinary `resource_type` |
|---|---|---|---|---|
| PDF | `prepwell/materials/pdf` | `pdf` | 50 MB | `raw` |
| IMAGE | `prepwell/materials/image` | `jpg,jpeg,png,webp` | 5 MB | `image` |

The signature covers `folder`, `allowed_formats`, and `timestamp`. 200 `{ cloudName, apiKey, timestamp, signature, folder, allowedFormats, maxBytes, resourceType }`. `maxBytes` is not part of the signature; the client is expected to honour it. 503 if Cloudinary is not configured.

URL rules: every URL must be `https:`. Video hosts are only `youtube.com`, `youtu.be`, and `vimeo.com`.

## Announcements

### `GET /admin/api/announcements`

200 up to 50 rows (shape `AnnouncementRow` in `src/lib/announcement-data.ts`).

### `POST /admin/api/announcements`

`maxDuration` 60s. Body:

- `title` 1–60
- `body` 1–180
- `url` optional internal path or null (not an arbitrary external URL)
- `audience` filter object
- `expiresInDays` 1–30, default 7

Audience (`src/lib/push-audience.ts`): optional `examTargets`, `classLevels`, `tracks`, `tiers`, `userIds` (max 1000). Empty filter means all active students. SQL always requires `role=STUDENT` and `isActive=true`, then ANDs the optional filters (`src/lib/push-audience-sql.ts`).

Side effects, in order:

1. Insert `Announcement` status `QUEUED`.
2. Insert one `AnnouncementDelivery` per matching push subscription whose user has announcements enabled (missing preference = enabled).
3. If zero recipients, set status `SENT` immediately.
4. Audit `announcement.send`.
5. If recipients > 0 and push is configured, `after(drainAnnouncements)`.

201 `{ id, recipientCount }`.

The UI asks for a typed confirmation at ≥ 500 devices. The API does not enforce that threshold.

### `POST /admin/api/announcements/{id}/cancel`

Allowed only while status is `QUEUED` or `SENDING`. Pending deliveries become `CANCELLED`. 200 `{ "ok": true }`. 409 if not cancellable. Audit `announcement.cancel`.

### `POST /admin/api/announcements/preview`

Body `{ audience }`. 200 `{ students, subscribedStudents, devices }`. 400 `{ "error": "Invalid audience" }`.

### `POST /admin/api/announcements/test`

Push must be configured else 503 `{ "error": "Push is not configured" }`. Body: `title`, `body`, `url`, `contact` (email or phone of a student). Sends immediately to that student’s subscriptions and **ignores** the announcements preference. 200 `{ devices, sent, student }` (student is `"First Last"`). 404 if no student matches. Audit `announcement.test` / entity `User`.

Drain rules are in [11-push-and-cron.md](./11-push-and-cron.md).

## Academic terms

### `GET /admin/api/academic-terms`

200 array ordered by `startsOn`. Dates are `YYYY-MM-DD`.

### `POST /admin/api/academic-terms`

Body `{ session: "YYYY/YYYY", term: FIRST|SECOND|THIRD, startsOn, endsOn }`.

`validateTermRanges` checks the candidate against all existing rows:

- session years are consecutive
- `endsOn` > `startsOn`
- `(session, term)` unique
- no date overlap with another term

201 the term. 400 with joined error strings. Audit `academic-term.create`.

### `PATCH /admin/api/academic-terms/{id}`

Same schema, validated as if this id were replaced. 404 if missing. Audit `academic-term.update`. 200 the term.

### `DELETE /admin/api/academic-terms/{id}`

404 if nothing was deleted. Audit `academic-term.delete`. 200 `{ "ok": true }`.

## Provider backfill

### `POST /admin/api/provider/backfill`

Any active admin. Next `maxDuration` is 300 seconds; the handler can run a long ingest. Body:

```json
{
  "subjectSlug": "physics",
  "examType": "WAEC",
  "examYear": 2019,
  "reset": false,
  "clearBlock": false
}
```

`examYear` is 2001–2026 inclusive.

Order:

1. If `clearBlock`, `clearProviderBlock` (circuit `BLOCKED` → open).
2. If `reset`, `resetFailedFetch` (`FAILED` → eligible to run again).
3. `ensureQuestionsCached(filter, 50)`.
4. `saturate(filter)`.

200 `{ ledger, wasReset, blockCleared }`. Audit `provider.backfill` / entity `ProviderFetch`. Bust catalogue.

Algorithm constants are in [12-question-provider.md](./12-question-provider.md).

## Audit action list

| Action | Entity |
|---|---|
| `admin.create`, `admin.deactivate`, `admin.reactivate` | `Admin` |
| `student.update`, `student.suspend`, `student.reactivate`, `student.tier`, `student.force_signout`, `student.delete` | `User` |
| `question.create`, `question.update`, `question.delete`, `question.import` | `Question` (`import` has no id) |
| `lesson.import` | `Lesson` |
| `material.create`, `material.update`, `material.delete` | `SubjectResource` |
| `academic-term.create`, `academic-term.update`, `academic-term.delete` | `AcademicTerm` |
| `announcement.send`, `announcement.cancel` | `Announcement` |
| `announcement.test` | `User` |
| `provider.backfill` | `ProviderFetch` |

The filter dropdown in `src/lib/admin-audit-filter.ts` is a subset. It omits `material.*`, `academic-term.*`, and `provider.backfill`. The writer accepts the full set. Port the writer’s union, not only the dropdown.
