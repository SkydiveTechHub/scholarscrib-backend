# AI Tutor — Frontend Contract

Date: 2026-10-20

> **Full guide:** see [`2026-10-21-ai-tutor-frontend-integration.md`](./2026-10-21-ai-tutor-frontend-integration.md) for TypeScript types, SSE client sketch, UX recipes, and error handling.

## Auth & access

- Student Bearer (or session cookie) required.
- Feature gate: `aiTutor` (PREMIUM). Expect 403 with entitlement denial otherwise.
- If RAG is disabled/unconfigured, APIs return 503.

## Thread lifecycle

1. `POST /api/tutor/threads` with optional scope + `firstMessage`.
2. Render `messages[]`; each assistant message has `groundedOn[]`.
3. Follow-ups: `POST /api/tutor/threads/{id}/messages` **or** SSE stream.
4. Archive: `DELETE /api/tutor/threads/{id}`.

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
| `userMessage` | message object |
| `status` | `{ "node", "route?", "confidence?", "noul?", "source?" }` (progress / decisions) |
| `token` | `{ "text": "..." }` |
| `assistantMessage` | full message with `groundedOn` |
| `done` | `{ "ok": true }` |

Chitchat / curriculum-SQL turns may emit `assistantMessage` without tokens.

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
