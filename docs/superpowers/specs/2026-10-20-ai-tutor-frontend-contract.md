# AI Tutor — Frontend Contract

Date: 2026-10-20

> **Full guide:** see [`2026-10-21-ai-tutor-frontend-integration.md`](./2026-10-21-ai-tutor-frontend-integration.md) for TypeScript types, SSE client sketch, UX recipes, and error handling.

## Auth & access

- Student Bearer (or session cookie) required.
- Feature gate: `aiTutor` (PREMIUM). Expect 403 with entitlement denial otherwise.
- If RAG is disabled/unconfigured, APIs return 503.

## Thread lifecycle

1. `POST /api/tutor/threads` with optional scope + `firstMessage`.
2. Load transcript: `GET /api/tutor/threads/{id}/messages` (or `GET /api/tutor/threads/{id}` which includes `messages[]`).
3. Each assistant message has `groundedOn[]`.
4. Follow-ups: `POST /api/tutor/threads/{id}/messages` **or** SSE stream.
5. Archive: `DELETE /api/tutor/threads/{id}`.

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
| `streamStarted` | `{ "streamId", "threadId", "userMessageId" }` |
| `userMessage` | message object (+ `streamId`) |
| `token` | `{ "text": "..." }` |
| `assistantMessage` | full message with `groundedOn` |
| `done` | `{ "ok": true\|false, "cancelled": bool, "streamId" }` |

Chitchat / curriculum-SQL turns may emit `assistantMessage` without tokens.

### Stop stream

`POST /api/tutor/threads/{id}/messages/stream/stop`  
Body (optional): `{ "streamId": "..." }` from `streamStarted`.

Stops the active reply for that thread. On cancel, the in-flight user message is removed and SSE ends with `done.cancelled: true`. Then send a new stream with the revised question.

Also abort the fetch (`AbortController`) on the client so the HTTP connection closes.

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
