# AI Tutor — Frontend Contract

Date: 2026-10-28

> **Full guide:** see [`2026-10-21-ai-tutor-frontend-integration.md`](./2026-10-21-ai-tutor-frontend-integration.md) for TypeScript types, SSE client sketch, UX recipes, and error handling. Admin console chat is [`2026-10-22-admin-astra-frontend-integration.md`](./2026-10-22-admin-astra-frontend-integration.md).

## Auth & access

- Student Bearer (or session cookie) required.
- Feature gate: `aiTutor` (PREMIUM). Expect 403 with entitlement denial otherwise.
- If RAG is disabled/unconfigured, APIs return 503.

## Thread lifecycle

1. `POST /api/tutor/threads` with optional scope + `firstMessage`.
2. Load transcript: `GET /api/tutor/threads/{id}/messages` (cursor-paginated; query `cursor`, `limit`, `orderBy`) or `GET /api/tutor/threads/{id}` which includes full `messages[]`.
3. Each assistant message has `groundedOn[]`.
4. Follow-ups: `POST /api/tutor/threads/{id}/messages` **or** SSE stream.
5. Archive: `DELETE /api/tutor/threads/{id}`.

### Thread shape — new fields

Every `TutorThread` now includes:

| Field | Type | Notes |
|-------|------|-------|
| `examType` | `"WAEC" \| "JAMB" \| "NECO" \| "CUSTOM" \| null` | Snapshotted from the Question at creation; `null` if no `questionId` |
| `examYear` | `number \| null` | Exam year, e.g. `2023`; `null` if not set on the question |

Use these to group or label question-scoped threads (e.g. "WAEC 2023") in the sidebar without a separate lookup.

### Practice deep-link

```json
{
  "questionId": "...",
  "subjectId": "...",
  "topicId": "...",
  "firstMessage": "Why was my answer wrong?",
  "wrongAnswerContext": "B"
}
```

### Question-scoped session resume (stream only)

When `questionId` is sent to `POST /api/tutor/threads/stream` and the student already has a thread for that question, the API **resumes the existing thread** instead of creating a new one. The `firstMessage` is appended as a new turn.

The SSE flow is identical in both cases. Read `threadId` from the `streamStarted` event — it may be an existing thread id:

```json
{ "streamId": "...", "runId": "...", "threadId": "existing-or-new-id", "userMessageId": "...", "threadTitle": "..." }
```

Always navigate to `/tutor/{threadId}` from `streamStarted`, not from an assumed new-thread id. The sync endpoint (`POST /api/tutor/threads`) always creates a new thread.

### Lesson drawer

`POST /api/lessons/{lessonId}/tutor` with `{ "firstMessage": "...", "messages" omitted }`  
Server fills `lessonId` / topic / subject.

## Citation shape (`groundedOn`)

```ts
type GroundedOn = {
  sourceType: "lesson" | "question" | "material";
  docId: string;
  subjectId?: string;
  topicId?: string;
  lessonId?: string;
  questionId?: string;
  resourceId?: string;
  title?: string;
  excerpt?: string;
};
```

Suggested deep links:

- lesson → classroom topic page + lesson
- question → practice / question bank
- material → `/library` resource

## SSE stream

`POST /api/tutor/threads/{id}/messages/stream`  
`Content-Type: text/event-stream`  
Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`

Events:

| event | data |
|-------|------|
| `streamStarted` | `{ "streamId", "runId", "threadId", "userMessageId", "threadTitle" }` — `threadId` may be an existing thread when resuming a question-scoped session |
| `userMessage` | message object (+ `streamId`, `runId`) |
| `token` | `{ "text": "..." }` |
| `assistantMessage` | full message with `groundedOn` |
| `done` | `{ "ok": true\|false, "cancelled": bool, "streamId", "runId" }` |

Chitchat / curriculum-SQL turns may emit `assistantMessage` without tokens.

### Stop stream

`POST /api/tutor/threads/{id}/messages/stream/stop`  
Body (optional): `{ "streamId": "..." }` from `streamStarted`.

Stops the active reply for that thread. SSE ends with `done.cancelled: true`. The user message stays, and any partial assistant text already produced is stored. Closing the socket does not cancel the run; rejoin it with `GET /api/tutor/threads/{id}/runs/{runId}/stream` (`Last-Event-ID`). A finished run is `404` — load the thread instead. See the integration guide for `activeRun`, `tool`, `ui`, and editable `fields`.

## Time travel

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/tutor/threads/{id}/history` | Checkpoint list |
| POST | `/api/tutor/threads/{id}/replay` | `{ "checkpointId" }` |
| POST | `/api/tutor/threads/{id}/fork` | `{ "checkpointId", "question?" }` |

## Feedback

`POST /api/tutor/messages/{messageId}/feedback`  
Body: `{ "rating": "up" | "down", "comment"?: string }`

## Rate limits

Daily message cap (`AI_TUTOR_DAILY_MESSAGE_LIMIT`, default 50) → HTTP 429.
