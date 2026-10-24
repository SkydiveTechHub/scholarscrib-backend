# Astra AI Tutor — Frontend Integration Guide

Date: 2026-10-21  
Audience: Next.js / student app engineers wiring the chat UI to the ScholarsCrib API.

This guide is the implementation reference for the student tutor surface. The shorter contract note lives in [`2026-10-20-ai-tutor-frontend-contract.md`](./2026-10-20-ai-tutor-frontend-contract.md). Admin console chat is a separate API: [`2026-10-22-admin-astra-frontend-integration.md`](./2026-10-22-admin-astra-frontend-integration.md).

---

## 1. What you are integrating

Astra is a **scoped chat** product, not a generic chatbot.

| Concept | Meaning for UI |
|--------|----------------|
| **Thread** | One conversation, optionally bound to subject / topic / lesson / question |
| **Message** | `user` \| `assistant` \| `system` turn persisted in Postgres |
| **Scope** | Filters retrieval (Zilliz) so answers stay on-curriculum |
| **Citations (`groundedOn`)** | Sources the assistant used; show as chips / deep links |
| **Stream** | Optional SSE for typing UX; final message is always the source of truth |

Backend stack (for mental model only): LangGraph routes each turn to **chitchat**, **SQL catalogue**, or **RAG**; Jev makes judgment calls; the chat model generates text. The frontend only consumes HTTP + SSE.

```
UI ──► POST /api/tutor/... ──► graph ──► assistant message (+ citations)
         ▲                              │
         └──────── SSE tokens/status ◄──┘
```

---

## 2. Base URL, auth, and feature gate

### Base paths

Student API prefix: `/api`

| Surface | Path |
|--------|------|
| Tutor | `/api/tutor/*` |
| Lesson entry | `/api/lessons/{lessonId}/tutor` |

### Auth

Send the student session the same way as the rest of the app:

```http
Authorization: Bearer <studentJwt>
```

Cookie auth is also accepted where the existing student client already uses it. Unauthenticated → `401` `{ "error": "Unauthorized" }`.

### Entitlement: `aiTutor`

All tutor routes depend on `require_feature("aiTutor")`.

- Required tier: **PREMIUM** (not in beta-open features).
- Denial:

```http
HTTP/1.1 403
```

```json
{
  "error": "This feature is part of Premium.",
  "requiredTier": "PREMIUM",
  "feature": "aiTutor"
}
```

**UI:** Gate the Astra entry points with the same `can(tier, "aiTutor")` check as other paid features. On 403, open the upgrade / paywall flow using `requiredTier` + `feature`.

### Runtime availability

Even with PREMIUM, if the API has not enabled RAG:

| Status | Body | When |
|--------|------|------|
| `503` | `{ "error": "AI tutor is disabled" }` | `AI_TUTOR_ENABLED` false |
| `503` | `{ "error": "Vector store is not configured" }` | Missing Zilliz |

**UI:** Show a calm “Astra is temporarily unavailable” empty state; do not retry-loop.

### Daily message limit

Default **50 messages / UTC day** (`AI_TUTOR_DAILY_MESSAGE_LIMIT`).

Counted when a **user turn** is accepted (create-with-`firstMessage`, sync post, or stream post).

```http
HTTP/1.1 429
Retry-After: <seconds>
```

```json
{
  "error": "Too many requests. Please slow down and try again shortly."
}
```

**UI:** Disable send, show remaining-day copy if you track client-side, and honor `Retry-After` when present.

---

## 3. TypeScript types

```ts
type SourceType = "lesson" | "question" | "material";

type GroundedOn = {
  sourceType?: SourceType;
  docId?: string;
  subjectId?: string;
  topicId?: string;
  lessonId?: string;
  questionId?: string;
  resourceId?: string;
  title?: string;
  excerpt?: string; // truncated ~280 chars
};

type TutorActiveRun = {
  runId: string;
  status: "running" | "paused";
} | null;

type TutorToolEvent = {
  id: string;
  name: string;
  phase: "running" | "done" | "failed";
  summary: string;
};

type TutorUiBlock =
  | {
      id: string;
      component: "worked_example";
      props: { problem: string; steps: string[] };
    }
  | {
      id: string;
      component: "practice_question";
      props: { prompt: string; choices?: string[] };
    };

type TutorProposalField = {
  key: string;
  label: string;
  type: "string" | "number";
  required: boolean;
  maxLength?: number;
};

type TutorMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  groundedOn: GroundedOn[];
  ui: TutorUiBlock[];
  tools: TutorToolEvent[];
  createdAt: string | null;
};

/** `GET /api/tutor/threads/{id}/messages` — oldest-first cursor page */
type TutorMessagesPage = {
  messages: TutorMessage[];
  total?: number | null;
  currentPage?: string | null;
  currentPageBackwards?: string | null;
  previousPage?: string | null;
  nextPage?: string | null;
};

type TutorThread = {
  id: string;
  title: string | null;
  subjectId: string | null;
  topicId: string | null;
  questionId: string | null;
  lessonId: string | null;
  archived: boolean;
  /** `null` when the last turn is already on `messages`. */
  activeRun: TutorActiveRun;
  createdAt: string | null;
  updatedAt: string | null;
  messages?: TutorMessage[];
  /** Present when create ran a first turn */
  assistantMessage?: TutorMessage;
};

type TutorThreadCreateBody = {
  subjectId?: string;
  topicId?: string;
  questionId?: string;
  lessonId?: string;
  title?: string; // max 120
  firstMessage?: string; // max 8000
  wrongAnswerContext?: string; // max 500; use with questionId
};

type TutorMessageBody = {
  content: string; // 1..8000
};

type TutorFeedbackBody = {
  rating: "up" | "down";
  comment?: string; // max 1000
};

type TutorCheckpoint = {
  checkpointId: string | null;
  next: unknown[];
  createdAt: unknown;
  metadata: Record<string, unknown>;
  values: {
    question?: string;
    route?: string;
    summary?: string;
    decisionMeta?: Record<string, unknown>;
    decisionTrace?: Record<string, unknown>[];
  };
};
```

---

## 4. Endpoint map

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/tutor/threads` | Create thread (optional first turn) |
| `GET` | `/api/tutor/threads` | List threads (no messages) |
| `GET` | `/api/tutor/threads/{id}` | Thread + full message history |
| `GET` | `/api/tutor/threads/{id}/messages` | Cursor-paginated messages (`cursor`, `limit`, `orderBy`) |
| `DELETE` | `/api/tutor/threads/{id}` | Soft-archive (`archived: true`) |
| `POST` | `/api/tutor/threads/{id}/messages` | Sync turn (wait for full reply) |
| `POST` | `/api/tutor/threads/{id}/messages/stream` | SSE turn (preferred for chat UX) |
| `GET` | `/api/tutor/threads/{id}/runs/{runId}/stream` | Rejoin an in-flight or paused run (`Last-Event-ID`) |
| `POST` | `/api/tutor/threads/{id}/actions/resume` | Approve or reject a paused action card |
| `GET` | `/api/tutor/threads/{id}/history` | LangGraph checkpoints |
| `POST` | `/api/tutor/threads/{id}/replay` | Re-run from checkpoint |
| `POST` | `/api/tutor/threads/{id}/fork` | Branch from checkpoint |
| `POST` | `/api/tutor/messages/{messageId}/feedback` | Thumbs up/down |
| `POST` | `/api/lessons/{lessonId}/tutor` | Create lesson-scoped thread |

All JSON request/response bodies use **camelCase**.

### Paginated messages

`GET /api/tutor/threads/{id}/messages?cursor=&limit=50&orderBy=asc`

- Query: `limit` (1–100, default 50), `cursor` (omit for the first page; pass `nextPage` for the next), `orderBy` (`asc` | `desc`, default `asc`).
- `orderBy=asc` → oldest first; `desc` → newest first (both tie-break on `id`).
- Do not reuse a cursor across different `orderBy` values.
- Body: `TutorMessagesPage` (`messages`, `nextPage`, `previousPage`, …). Thread id is only in the path.
- For a one-shot full transcript (small threads), `GET /api/tutor/threads/{id}` still returns all `messages[]`.

### Action confirmation (study plan)

When Astra proposes a study-plan change, the turn **pauses** until the student approves.

SSE:

| event | data |
|-------|------|
| `actionProposal` | `{ actionId, checkpointId, tool, args, summary, destructive, fields? }` |
| `done` | `{ ok: true, paused: true, pendingAction }` |

`summary` is the plan Astra wrote for the card. Show it in full and keep the line breaks. `args` is the payload that runs on approve, without that text.

`fields` lists the editable scalars. Each `fields[].key` exists in `args`. Keys that are not listed are kept when resume sends only the edited keys. `type` is `string` or `number`. Destructive tools such as `delete_study_plan` omit `fields`. A resume whose `args` fail that schema, add a key, or change a non-editable key returns `400` `{ "error": "..." }` and leaves the proposal `pending`. `approved: false` ignores `args`. Omitted or `null` `args` runs the stored payload.

The assistant message stored for that turn keeps the summary in `content`. While it is waiting, `groundedOn` includes `{ kind: "actionProposal", status: "pending", ... }`. **Redraw the card only for that pending shape.**

When resume finishes (approve or dismiss), that same message is updated in place: `kind` is removed and `status` becomes `executed` or `rejected`. The summary text stays. A reload must not show Approve again. The resume body also returns `proposalMessage` (the updated row). SSE resume emits `proposalSettled` with that message before `assistantMessage`.

```http
POST /api/tutor/threads/{id}/actions/resume
```

```json
{ "approved": true, "actionId": "...", "args": null, "stream": false }
```

- `approved: false` dismisses the card.
- Optional `args` replaces the proposed payload (re-validated) before execution.
- Does **not** count as a new chat message.
- While a proposal is pending, another message returns **409** until resume.
- `stream: true` returns SSE: `actionExecuted` or `actionRejected`, then `proposalSettled`, then `assistantMessage`, then `done`.

v1 mutating tools: `create_study_plan`, `update_study_plan`, `delete_study_plan`, `set_study_plan_positions`, `set_study_plan_item_status`. Reads (`get_study_plan`, `list_subjects`, `get_dashboard_summary`, `get_performance_summary`, `get_topic_standings`) run without a card. Assessments and flashcards are not executable tools yet.

#### Topic standings (read tool + REST)

Astra answers “what am I weak / strong at?” via `get_topic_standings`. The same data is available outside chat for the performance UI:

```http
GET /api/performance/topics?status=all&subjectId={id}&limit=50
```

| Query | Values |
|-------|--------|
| `status` | `all` (default) \| `strong` \| `weak` \| `normal` |
| `subjectId` | Optional; repeatable (`?subjectId=a&subjectId=b`) |
| `limit` | Default 50, max 100 |

Response:

```ts
type TopicStandingsOut = {
  topics: Array<{
    topicId: string;
    title: string;
    slug: string;
    subjectId: string;
    subjectName: string;
    subjectSlug: string;
    mastery: number;
    level: string;
    status: "strong" | "weak" | "normal" | "emerging";
    confidence: number;
    observations: number;
  }>;
  status: "all" | "strong" | "weak" | "normal";
};
```

Bands (only topics with evidence): `strong` mastery ≥ 85, `weak` &lt; 50, `normal` 50–84, once confidence is high enough. Low-confidence rows appear only under `status=all` as `emerging`. No advanced-analytics gate on this list.

Tool stream copy for this read: “Checking your topic standings…”.

---

## 5. Thread lifecycle (recommended UX)

### 5.1 Sidebar / inbox

1. `GET /api/tutor/threads` → list by `updatedAt` (server order).
2. Show `title` (auto-generated from first message if omitted).
3. Optional chips from `subjectId` / `lessonId` / `questionId` if you resolve names client-side.
4. Opening a row → `GET /api/tutor/threads/{id}` for `messages[]`.
5. Archive → `DELETE` then remove from list (or keep under “Archived”).

List payload does **not** include messages — keep list light.

### 5.2 Create flows

#### A. Blank / general chat

```http
POST /api/tutor/threads
Content-Type: application/json
```

```json
{ "title": "Biology help" }
```

Or omit title and send `firstMessage` (title auto-generated).

**Recommended for streaming UX:** create with **no** `firstMessage`, then immediately `POST .../messages/stream` for the first user text.  
`firstMessage` on create runs the graph **synchronously** (no SSE).

#### B. Practice “why was I wrong?” deep-link

```json
{
  "questionId": "clx…",
  "subjectId": "clx…",
  "topicId": "clx…",
  "firstMessage": "Why was my answer wrong?",
  "wrongAnswerContext": "B"
}
```

Server may insert a hidden **`system`** message with the wrong option. In the transcript:

- Prefer **hiding** `role === "system"` in the bubble list, or
- Show a one-line context banner (“Reviewing your answer: B”).

Scope IDs must be valid or create returns `400` (`Unknown topic` / `Unknown question` / …).

#### C. Lesson drawer / classroom

```http
POST /api/lessons/{lessonId}/tutor
```

```json
{ "firstMessage": "Explain this lesson in simpler words" }
```

Server fills `lessonId`, and defaults `topicId` / `subjectId` from the lesson graph. Same response shape as `POST /api/tutor/threads`.

### 5.3 Create response

`201` → `TutorThread` including `messages[]`. If a first turn ran, `assistantMessage` is also set (same object as the last assistant in `messages`).

---

## 6. Sending messages

### 6.1 Sync (simple clients / tests)

```http
POST /api/tutor/threads/{threadId}/messages
Content-Type: application/json
```

```json
{ "content": "What is osmosis?" }
```

`200`:

```json
{
  "userMessage": { "id": "…", "role": "user", "content": "…", "groundedOn": [], "createdAt": "…" },
  "assistantMessage": { "id": "…", "role": "assistant", "content": "…", "groundedOn": […], "createdAt": "…" }
}
```

Block the composer until the response returns. Good for admin tools; **not** ideal for product chat.

### 6.2 Stream (preferred)

```http
POST /api/tutor/threads/{threadId}/messages/stream
Content-Type: application/json
Accept: text/event-stream
```

```json
{ "content": "What is osmosis?" }
```

Response: `Content-Type: text/event-stream` with headers:

- `Cache-Control: no-cache`
- `Connection: keep-alive`
- `X-Accel-Buffering: no`

Body format (standard SSE):

```
event: userMessage
data: {"id":"…","role":"user","content":"…","groundedOn":[],"createdAt":"…"}

event: status
data: {"node":"classify_intent"}

event: status
data: {"node":"classify_intent","route":"rag","confidence":0.91,"source":"jev"}

event: token
data: {"text":"Osmosis "}

event: token
data: {"text":"is "}

event: assistantMessage
data: {"id":"…","role":"assistant","content":"Osmosis is …","groundedOn":[…],"createdAt":"…"}

event: done
data: {"ok":true}
```

#### Event contract

| Event | When | UI action |
|-------|------|-----------|
| `userMessage` | User row persisted | Append user bubble (authoritative id) |
| `status` | Graph progress / Jev decision | Optional “Thinking…” / stage label |
| `token` | Chat-model stream chunk | Append to **draft** assistant text |
| `assistantMessage` | Final persisted assistant | **Replace** draft; set `groundedOn` |
| `done` | Stream finished | Unlock composer; clear abort state |

#### `status` payload (optional fields)

```ts
type TutorStatusEvent = {
  node: string; // e.g. classify_intent | grade_documents | ground_answer | remember
  route?: "chitchat" | "sql" | "rag" | string;
  confidence?: number; // Choice confidence 0..1
  noul?: number;       // yes-probability 0..1
  source?: "heuristic" | "jev" | "jev_low_confidence" | "llm" | "fallback" | string;
};
```

Suggested labels (keep casual; do not expose “Jev” to students):

| `node` | Copy idea |
|--------|-----------|
| `classify_intent` | Understanding your question… |
| `grade_documents` | Checking lesson notes… |
| `ground_answer` | Verifying the answer… |
| `remember` | Saving what helps you… |

#### Critical streaming rules

1. **Final text = `assistantMessage.content`**, not the concatenation of tokens.  
   After generation, a groundedness check may **replace** the streamed draft with a soft-refuse string (“I do not have enough grounded material…”). If you only keep tokens, the UI will lie.
2. Chitchat / SQL turns may emit **`assistantMessage` with no `token` events**. Always handle that path.
3. If the socket drops, the run keeps going. Rejoin with `GET .../runs/{runId}/stream` and `Last-Event-ID` (see 6.3). `GET /api/tutor/threads/{id}` is the fallback when that rejoin is `404`.
4. Closing the panel does not cancel the turn. `POST .../messages/stream/stop` is the only cancel. Stop keeps the user message and any partial assistant text already produced.
5. Create-with-`firstMessage` does **not** stream — use empty create + stream for first-turn typing UX.

#### Minimal browser client sketch

```ts
async function streamTutorMessage(
  threadId: string,
  content: string,
  handlers: {
    onUser: (m: TutorMessage) => void;
    onStatus?: (s: TutorStatusEvent) => void;
    onToken: (text: string) => void;
    onAssistant: (m: TutorMessage) => void;
    onDone: () => void;
  },
  signal?: AbortSignal,
) {
  const res = await fetch(`/api/tutor/threads/${threadId}/messages/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ content }),
    signal,
  });
  if (!res.ok || !res.body) throw await readApiError(res);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";

    for (const block of parts) {
      let event = "message";
      const dataLines: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;
      const data = JSON.parse(dataLines.join("\n"));
      if (event === "userMessage") handlers.onUser(data);
      else if (event === "status") handlers.onStatus?.(data);
      else if (event === "token") handlers.onToken(data.text ?? "");
      else if (event === "assistantMessage") handlers.onAssistant(data);
      else if (event === "done") handlers.onDone();
    }
  }
}
```

---

### 6.3 Runs, rejoin, tools, and study cards

Every SSE frame on the message stream and the rejoin stream has an `id` line that increases for the life of that run (`id: 1`, `id: 2`, …). `streamStarted.runId` is the same value as `streamId`.

`GET /api/tutor/threads/{id}` includes `activeRun`:

| `activeRun` | Meaning |
|-------------|---------|
| `{ runId, status: "running" }` | The graph is still producing this turn |
| `{ runId, status: "paused" }` | An `actionProposal` is waiting |
| `null` | The turn is on `messages` |

Disconnect without stop leaves `activeRun` running. Stop sets it `null`.

```http
GET /api/tutor/threads/{threadId}/runs/{runId}/stream
Last-Event-ID: 4
Authorization: Bearer <studentJwt>
```

The response replays frames after that id, then continues live. A missing or unknown id replays from the start. Dedupe by id. `404` `{ "error": "Run not found" }` when the run is already finished (or not this student's). Load the thread: the assistant message includes `groundedOn`, `tools`, `ui`, and any pending proposal.

While a tool runs, the stream emits `tool` beside `token` and `status`:

```json
{ "id": "tool_1", "name": "grade_documents", "phase": "running", "summary": "Checking lesson notes…" }
```

`phase` is `running`, `done`, or `failed`. `summary` is one short student-facing line. The event does not include chain-of-thought, SQL, or document text. Every `running` is closed with `done` or `failed` before `done` on the turn. `status` is unchanged (`node`, and sometimes `route`, `confidence`, `source`).

When Astra wants a fixed card, the stream emits `ui` and the same block is stored on the assistant message (`ui`):

```json
{ "id": "ui_1", "component": "worked_example", "props": { "problem": "…", "steps": ["…"] } }
```

| `component` | `props` |
|-------------|---------|
| `worked_example` | `problem` string, `steps` string array, at most 8 steps |
| `practice_question` | `prompt` string, optional `choices` string array, at most 4 choices. No answer and no mark scheme |

Any other component is dropped. Markdown in `token` / `content` is still the reply. The API does not send HTML.

---

## 7. Citations (`groundedOn`)

Assistant messages from the RAG path may include citations. Chitchat / SQL / soft-refuse usually return `[]`.

### Rendering

- Show a “Sources” row under the assistant bubble when `groundedOn.length > 0`.
- Prefer `title`, fall back to `sourceType`.
- Optional excerpt tooltip (`excerpt`).

### Deep links (suggested)

| `sourceType` | Navigate to |
|--------------|-------------|
| `lesson` | Classroom / lesson viewer (`lessonId`, `topicId`, `subjectId`) |
| `question` | Practice / question detail (`questionId`) |
| `material` | Library resource (`resourceId`) |

Ignore empty ids. Do not invent links when ids are missing.

---

## 8. Message roles in the transcript

| Role | Persist? | Show? |
|------|----------|-------|
| `user` | yes | yes |
| `assistant` | yes | yes + citations + feedback |
| `system` | yes (wrong-answer seed) | optional banner; avoid as a chat bubble |

Render markdown if the product already does for lesson notes; assistant content is plain text / markdown-ish from the model — sanitize as you do elsewhere.

---

## 9. Time travel (optional advanced UI)

Checkpoints are LangGraph snapshots for the thread (debugging / “regenerate from here”), not a second message store.

### List

```http
GET /api/tutor/threads/{id}/history
```

```json
{
  "checkpoints": [
    {
      "checkpointId": "…",
      "next": ["remember"],
      "createdAt": null,
      "metadata": {},
      "values": {
        "question": "What is osmosis?",
        "route": "rag",
        "summary": "",
        "decisionMeta": { "node": "classify_intent", "source": "jev", "route": "rag" },
        "decisionTrace": []
      }
    }
  ]
}
```

### Replay

```http
POST /api/tutor/threads/{id}/replay
{ "checkpointId": "…" }
```

Re-runs the graph from that checkpoint. Then **reload the thread** (`GET`) to refresh messages — do not assume the response includes the full transcript.

### Fork

```http
POST /api/tutor/threads/{id}/fork
{ "checkpointId": "…", "question": "Explain it with an analogy" }
```

Branches state; optional new `question`. Reload thread afterward.

`503` if the checkpointer is unavailable.

**Product note:** Ship chat + stream + citations first. Expose history/replay/fork only if you want a power-user “regenerate” affordance.

---

## 10. Feedback

```http
POST /api/tutor/messages/{messageId}/feedback
{ "rating": "up" | "down", "comment": "optional" }
```

`200`: `{ "ok": true, "id": "<feedbackId>" }`

- Only on **assistant** messages.
- One control per message is enough for v1 (no edit/delete of feedback in API yet).

---

## 11. Error handling cheat sheet

| HTTP | Typical `error` | Frontend |
|------|-----------------|----------|
| `400` | Validation / unknown refs / empty content | Inline form error |
| `401` | Unauthorized | Re-auth |
| `403` | Premium entitlement | Paywall (`feature`, `requiredTier`) |
| `404` | Thread / message / lesson not found | Toast + navigate back |
| `429` | Rate limit | Disable send; show retry |
| `503` | Tutor / vector store / checkpointer down | Unavailable state |

Validation failures use:

```json
{ "error": "Validation failed", "details": [ … FastAPI errors … ] }
```

---

## 12. Suggested screen composition

### Chat shell

1. **Left:** thread list (`GET /threads`), new chat, archive.  
2. **Center:** message list + composer.  
3. **Scope header:** subject / topic / lesson / question labels when ids present.  
4. **Sources panel** or inline chips under assistant messages.  
5. **Status line** under composer while streaming (`status` events).

### Composer

- Max length **8000** (match API).  
- Disable while stream in flight (unless you support cancel).  
- Empty content rejected with `400`.

### Empty states

| State | Copy direction |
|-------|----------------|
| No threads | “Ask Astra about a lesson or past question.” |
| Soft-refuse answer | Keep the text; suggest narrowing subject/topic |
| 503 | “Astra is offline right now.” |
| 403 | Upgrade to Premium |

### Soft-refuse string (recognize for styling)

The backend may reply with insufficient-grounded copy (exact text may evolve). Treat as a normal assistant message; optionally style differently when `groundedOn` is empty **and** content mentions lacking material.

---

## 13. End-to-end recipes

### Recipe 1 — New chat with streaming first message

```
POST /api/tutor/threads                    {}  → threadId
POST /api/tutor/threads/{id}/messages/stream   { content }
  → handle SSE → show assistantMessage
```

### Recipe 2 — Continue existing chat

```
GET  /api/tutor/threads/{id}
POST /api/tutor/threads/{id}/messages/stream { content }
```

### Recipe 3 — From a wrong practice answer

```
POST /api/tutor/threads {
  questionId, subjectId, topicId,
  firstMessage: "Why was my answer wrong?",
  wrongAnswerContext: selectedOption
}
→ navigate to /tutor/{threadId} with messages already filled
```

(If you need typing UX on this path, create without `firstMessage`, insert your own banner for the wrong option, then stream.)

### Recipe 4 — From a lesson

```
POST /api/lessons/{lessonId}/tutor { firstMessage? }
→ open thread drawer / route
```

---

## 14. What not to build against

- Do **not** call admin `/admin/api/rag/*` or `/admin/api/astra/*` from the student app. Admin chat is [`2026-10-22-admin-astra-frontend-integration.md`](./2026-10-22-admin-astra-frontend-integration.md).  
- Do **not** depend on LangSmith, Jev, or Milvus from the browser.  
- Do **not** treat token concatenation as final.  
- Do **not** assume every turn has citations.  
- Do **not** expect list endpoints to include full message bodies.

---

## 15. Quick QA checklist

- [ ] PREMIUM gate + 403 paywall  
- [ ] 503 unavailable state  
- [ ] Create blank → stream first message  
- [ ] Stream tokens update draft; `assistantMessage` replaces draft  
- [ ] Soft-refuse replaces streamed text correctly  
- [ ] Chitchat path works with no tokens  
- [ ] Citations render + deep link  
- [ ] System wrong-answer message hidden or bannered  
- [ ] Lesson + practice entry points set scope chips  
- [ ] Archive removes from inbox  
- [ ] Feedback posts on assistant id  
- [ ] 429 disables send  
- [ ] Abort mid-stream recovers via GET thread  
- [ ] “What am I weak at?” uses `get_topic_standings` (tool event, no approval card)  
- [ ] Performance page can load `GET /api/performance/topics` with status / subject filters  

---

## Related docs

- [`2026-10-22-admin-astra-frontend-integration.md`](./2026-10-22-admin-astra-frontend-integration.md) — admin console chat  
- [`2026-10-20-ai-tutor-frontend-contract.md`](./2026-10-20-ai-tutor-frontend-contract.md) — short contract  
- [`2026-10-20-ai-tutor-rag-design.md`](./2026-10-20-ai-tutor-rag-design.md) — backend design  
- Routes: `app/api/student/tutor.py`, `app/api/student/lessons.py`  
- Shapes: `app/api/schemas.py`, `app/api/responses.py`, `app/services/rag/schemas.py`
