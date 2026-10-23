# Admin Astra — Frontend Integration Guide

Date: 2026-10-22  
Audience: admin console engineers wiring the Astra chat panel to the ScholarsCrib API.

The student tutor guide is [`2026-10-21-ai-tutor-frontend-integration.md`](./2026-10-21-ai-tutor-frontend-integration.md). Admin Astra reuses the same SSE event names and message shape. Threads, auth, and tools are separate. A student session cannot open these routes.

---

## 1. What you are integrating

Admin Astra is an operator chat. It can read every admin domain on its own. A change is applied only after the signed-in admin approves a card. The executor calls the same services the console forms already call, and the audit log stays the source of truth.

| Concept | Meaning for UI |
|--------|----------------|
| **Thread** | One conversation owned by one admin. No subject, lesson, or question scope |
| **Message** | `user` or `assistant` turn. There is no `system` seed |
| **Read** | Runs during the turn. Shows up as a `tool` event, then the reply |
| **Approval card** | A mutation paused on `actionProposal`. Nothing is written until resume |
| **Stream** | Preferred send path. The persisted assistant message is the source of truth |

```
UI ──► POST /admin/api/astra/... ──► admin graph ──► reply or approval card
         ▲                                              │
         └──────── SSE tokens / tool / proposal ◄──────┘
```

There is no Premium gate, no daily message cap, and no lesson-drawer entry. Time travel, fork, and feedback are not on this surface.

---

## 2. Base URL and auth

Prefix: `/admin/api/astra`

Send the admin session the same way as the rest of the console:

```http
Authorization: Bearer <adminJwt>
```

The cookie `scholarscrib.admin-session` is also accepted. A student JWT, a missing token, or an inactive admin is `401` `{ "error": "Unauthorized" }`.

A thread belongs to the admin who created it. Another admin's id returns `404` `{ "error": "Thread not found" }`.

Owner-only work (team, catalogue writes, student delete, force sign-out) is omitted from the model unless `admin.isOwner` is true. The execute step checks that again. The chat route itself stays `require_admin`; it does not return `403` for a non-owner.

---

## 3. TypeScript types

Message rows match the student tutor. `ui` is always an empty array on this surface.

```ts
type AdminAstraActiveRun = {
  runId: string;
  status: "running" | "paused";
} | null;

type AdminAstraToolEvent = {
  id: string;
  name: string;
  phase: "running" | "done" | "failed";
  summary: string;
};

type AdminAstraProposalField = {
  key: string;
  label: string;
  type: "string" | "number" | "boolean";
  required: boolean;
  maxLength?: number;
};

type AdminAstraProposal = {
  kind?: "actionProposal";
  status: "pending" | "executed" | "rejected";
  actionId: string;
  checkpointId: string | null;
  tool: string;
  args: Record<string, unknown>;
  summary: string;
  destructive: boolean;
  fields?: AdminAstraProposalField[];
};

type AdminAstraMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  groundedOn: AdminAstraProposal[];
  ui: [];
  tools: AdminAstraToolEvent[];
  createdAt: string | null;
  /** Present on the live SSE / sync payload while a card is waiting. */
  paused?: boolean;
  pendingAction?: AdminAstraProposal;
};

type AdminAstraMessagesPage = {
  messages: AdminAstraMessage[];
  total?: number | null;
  currentPage?: string | null;
  currentPageBackwards?: string | null;
  previousPage?: string | null;
  nextPage?: string | null;
};

type AdminAstraThread = {
  id: string;
  title: string | null;
  archived: boolean;
  /** `null` when the last turn is already on `messages`. */
  activeRun: AdminAstraActiveRun;
  createdAt: string | null;
  updatedAt: string | null;
  messages?: AdminAstraMessage[];
  /** Present when create ran a first turn */
  assistantMessage?: AdminAstraMessage;
};

type AdminAstraThreadCreateBody = {
  title?: string; // max 120
  firstMessage?: string; // max 8000
};

type AdminAstraMessageBody = {
  content: string; // 1..8000
};

type AdminAstraResumeBody = {
  approved: boolean;
  actionId?: string;
  args?: Record<string, unknown> | null;
  stream?: boolean;
};
```

---

## 4. Endpoint map

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/admin/api/astra/threads` | Create a thread (`201`). Optional synchronous first turn |
| `GET` | `/admin/api/astra/threads` | List this admin's open threads (no messages) |
| `GET` | `/admin/api/astra/threads/{id}` | Thread plus the full transcript |
| `GET` | `/admin/api/astra/threads/{id}/messages` | Cursor page (`cursor`, `limit`, `orderBy`) |
| `DELETE` | `/admin/api/astra/threads/{id}` | Soft-archive. `{ "ok": true }` |
| `POST` | `/admin/api/astra/threads/{id}/messages` | Sync turn |
| `POST` | `/admin/api/astra/threads/{id}/messages/stream` | SSE turn |
| `POST` | `/admin/api/astra/threads/{id}/messages/stream/stop` | Cancel the live run |
| `GET` | `/admin/api/astra/threads/{id}/runs/{runId}/stream` | Rejoin (`Last-Event-ID`) |
| `POST` | `/admin/api/astra/threads/{id}/actions/resume` | Approve or reject the paused card |

JSON bodies are **camelCase**.

### Paginated messages

`GET /admin/api/astra/threads/{id}/messages?cursor=&limit=50&orderBy=asc`

- `limit` is 1–100, default 50.
- Omit `cursor` for the first page. Pass `nextPage` for the next page.
- `orderBy` is `asc` (default, oldest first) or `desc`. Tie-break is `id`.
- Do not reuse a cursor across different `orderBy` values.
- For a small thread, `GET /admin/api/astra/threads/{id}` returns every message.

The list endpoint omits archived threads and omits `messages`.

### Approval card

A mutation does not write. The turn pauses until resume.

SSE:

| event | data |
|-------|------|
| `actionProposal` | `{ actionId, checkpointId, tool, args, summary, destructive, fields?, kind, status }` |
| `assistantMessage` | the summary row, plus `paused: true` and `pendingAction` |
| `done` | `{ ok: true, paused: true, pendingAction, streamId, runId }` |

`summary` is the text on the card. Keep the line breaks. `args` is the payload that runs on approve. `summary` is not inside `args`.

`fields` lists the scalars the admin may edit. Each `fields[].key` exists in `args`. `type` is `string`, `number`, or `boolean`. Keys that are not listed stay as stored when resume sends only the edited keys. A changed identity id, an unknown key, or a value that fails the field type returns `400` `{ "error": "Invalid action payload" }` and leaves the proposal `pending`.

`fields` is omitted when the tool is destructive or owner-only. Those cards are approve or reject only.

```http
POST /admin/api/astra/threads/{id}/actions/resume
```

```json
{ "approved": true, "actionId": "…", "args": null, "stream": false }
```

- `approved: false` dismisses the card. `args` is ignored.
- Omitted or `null` `args` runs the stored payload.
- Resume does not create a user message.
- While a proposal is pending, another message returns **409** `{ "error": "Approve or dismiss the pending action before sending another message" }`.
- `stream: true` returns SSE: `actionExecuted` or `actionRejected`, then `proposalSettled`, then `assistantMessage`, then `done`.

Sync resume `200`:

```json
{
  "ok": true,
  "approved": true,
  "assistantMessage": { "id": "…", "role": "assistant", "content": "…", "groundedOn": [] },
  "proposalMessage": { "id": "…", "role": "assistant", "content": "…", "groundedOn": [{ "status": "executed" }] },
  "actionResult": { "ok": true, "tool": "update_student", "result": {} }
}
```

`proposalMessage` is the original card row, updated in place. `kind` is removed and `status` is `executed` or `rejected`. The summary text stays. A reload must not show Approve again. `assistantMessage` is the new outcome row.

A failed execute still settles the card. `actionResult` is `{ "ok": false, "error": "…", "tool": "…" }` and the outcome text explains the failure. Show that error. Do not offer Approve again.

---

## 5. Thread lifecycle

### 5.1 Sidebar

1. `GET /admin/api/astra/threads` — server order is `updatedAt` descending.
2. Show `title`. A blank create is titled `New chat` until the first message, which replaces that title.
3. Open a row with `GET /admin/api/astra/threads/{id}`.
4. If `activeRun.status === "running"`, rejoin the stream (section 6.3).
5. If `activeRun.status === "paused"`, or any assistant `groundedOn` item has `kind === "actionProposal"` and `status === "pending"`, render the card and lock the composer.
6. Archive with `DELETE`, then drop the row. Archived threads are absent from the list and return `404` on later reads.

### 5.2 Create

```http
POST /admin/api/astra/threads
Content-Type: application/json
```

```json
{ "title": "Student lookup" }
```

`201` returns the thread. `messages` is included. When `firstMessage` is set, the graph runs **synchronously** and `assistantMessage` is the last assistant row.

For a typing first turn, create with no `firstMessage`, then `POST .../messages/stream`.

---

## 6. Sending messages

### 6.1 Sync

```http
POST /admin/api/astra/threads/{threadId}/messages
Content-Type: application/json
```

```json
{ "content": "How many students signed up this month?" }
```

`200` is `{ userMessage, assistantMessage }`. If the turn paused, `assistantMessage.paused` is true and `assistantMessage.pendingAction` is the card. `assistantMessage.content` is the summary.

### 6.2 Stream

```http
POST /admin/api/astra/threads/{threadId}/messages/stream
Content-Type: application/json
Accept: text/event-stream
```

```json
{ "content": "Find Ada Lovelace and show her tier" }
```

Headers: `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`.

```
event: streamStarted
data: {"streamId":"…","runId":"…","threadId":"…","userMessageId":"…","threadTitle":"Find Ada Lovelace"}

event: userMessage
data: {"id":"…","role":"user","content":"…","groundedOn":[],"ui":[],"tools":[],"createdAt":"…","streamId":"…","runId":"…"}

event: status
data: {"node":"classify_intent"}

event: status
data: {"node":"pick_domain","route":"students","confidence":1,"source":"heuristic"}

event: tool
data: {"id":"tool_1","name":"search_students","phase":"running","summary":"Working on that…"}

event: tool
data: {"id":"tool_1","name":"search_students","phase":"done","summary":"Working on that…"}

event: token
data: {"text":"Ada "}

event: assistantMessage
data: {"id":"…","role":"assistant","content":"…","groundedOn":[],"ui":[],"tools":[…],"createdAt":"…"}

event: done
data: {"ok":true,"cancelled":false,"streamId":"…","runId":"…"}
```

| Event | When | UI action |
|-------|------|-----------|
| `streamStarted` | Run accepted | Store `runId` (same value as `streamId`) for rejoin and stop |
| `userMessage` | User row persisted | Append the user bubble |
| `status` | Graph node | Optional stage label |
| `tool` | A read or a proposal tool started or finished | One line under the draft. Pair `running` with a later `done` or `failed` on the same `id` |
| `token` | Chat-model chunk | Append to the **draft** |
| `actionProposal` | Mutation is waiting | Open the card from this payload |
| `assistantMessage` | Final row | **Replace** the draft. If `paused`, keep the card |
| `done` | Turn finished or paused | Unlock the composer unless `paused` or `cancelled` |

`status.node` values you will see:

| `node` | Copy idea |
|--------|-----------|
| `classify_intent` | Reading the request… |
| `pick_domain` | Choosing the area… |
| `action_agent` | Drafting… |
| `action_tools` | Looking that up… |
| `inspect_action_tools` | Preparing the change… |
| `await_action_approval` | Waiting for approval… |
| `execute_action` | Applying the change… |
| `action_outcome` | Finishing… |
| `chitchat` | — |

`pick_domain` may also send `route` as one or two domain names joined by a comma (`students`, `questions,rag`). `source` is `heuristic` or `llm`.

`tool.summary` from the server is the generic line `Working on that…`. Map `tool.name` in the client if you want a specific label. The event never includes row dumps, SQL, or secrets.

#### Streaming rules

1. Final text is `assistantMessage.content`. Token concatenation can miss the last replacement.
2. A short chitchat reply can emit `assistantMessage` with no `token` events.
3. A paused turn emits `actionProposal`, then `assistantMessage` with `paused: true`, then `done` with `paused: true`. The composer stays locked.
4. Closing the panel does not cancel the turn. `POST .../messages/stream/stop` is the cancel. Body `{ "streamId": "<runId>" }` is optional; omit it to stop the live run on that thread. The response is `{ "ok": true, "cancelled": true, "streamId": "…" }`. Stop keeps the user message and any partial assistant text already produced. The matching `done` frame is `{ "ok": false, "cancelled": true, ... }`.
5. Create-with-`firstMessage` does not stream.

While a reply is still running, another send returns **409** `{ "error": "A reply is still running" }`.

### 6.3 Rejoin

Every frame on the message stream and the rejoin stream has an `id` line (`id: 1`, `id: 2`, …). Resume-action SSE frames do not.

```http
GET /admin/api/astra/threads/{threadId}/runs/{runId}/stream
Last-Event-ID: 4
Authorization: Bearer <adminJwt>
```

The response replays frames after that id, then continues live. A missing or unknown id replays from the start. Dedupe by id. `404` `{ "error": "Run not found" }` when the run is already finished or belongs to another admin. Load the thread: the assistant message holds `tools` and any pending proposal in `groundedOn`.

`GET /admin/api/astra/threads/{id}` `activeRun`:

| `activeRun` | Meaning |
|-------------|---------|
| `{ runId, status: "running" }` | The graph is still producing this turn |
| `{ runId, status: "paused" }` | An `actionProposal` is waiting |
| `null` | The turn is on `messages` |

---

## 7. Rendering the card

Redraw the card only while `groundedOn` contains `{ kind: "actionProposal", status: "pending" }`. After resume, that same object has no `kind` and `status` is `executed` or `rejected`.

| Card | When | Controls |
|------|------|----------|
| Editable | `fields` is a non-empty array | Show `summary`, inputs for `fields`, Approve, Reject |
| Approve only | `destructive` is true, or `fields` is absent | Show `summary` and `args` read-only. Approve, Reject |

Editable tools:

| Tool | Fields |
|------|--------|
| `update_student` | `firstName`, `lastName`, `email`, `phone`, `classLevel`, `track`, `state` |
| `set_student_status` | `isActive` (boolean), `reason` |
| `create_question` | `questionText`, `explanation`, `correctAnswer`, `difficulty`, `marks`, `examYear`, `questionNumber` |
| `update_question` | same question scalars, all optional |
| `create_material` | `title`, `description`, `url`, `author`, `isFree` |
| `update_material` | same material scalars |
| `create_academic_term` | `session`, `term`, `startsOn`, `endsOn` (`YYYY-MM-DD`) |
| `update_academic_term` | same term scalars |

Send only the keys the admin changed. Identity ids (`userId`, `questionId`, `ids`, `materialId`, `termId`, `subjectId`, `curriculumId`, `topicId`, `adminId`, `announcementId`, `entityId`) are frozen. Changing one is `400` and the card stays pending.

Approve-only tools include deletes, imports, tier changes, announcements, provider backfill, reindex, catalogue writes, team changes, and force sign-out.

On approve, `assistantMessage.content` is a short result sentence, such as “Deleted that student.” The card summary stays on `proposalMessage` and is not repeated. `actionResult.result` is a compact JSON snapshot (ids and labels, capped lists). Show the sentence, and keep the raw result behind a disclosure. It never includes a password, token, or upload signature.

Creating an admin emails a reset link. The card names the email only. `actionResult.result.resetEmailSent` tells you whether that mail went out. There is no password to display.

---

## 8. What the assistant can do

The model is bound to one or two domains per turn. Reads return immediately. Mutations always become a card.

| Domain | Reads | Mutations |
|--------|-------|-----------|
| `students` | `search_students`, `get_student`, `get_student_deletion_impact` | `update_student`, `set_student_status`, `set_student_tier`. Owner: `delete_student`, `force_signout_student` |
| `questions` | `list_questions`, `get_question`, `question_form_options`, `question_coverage`, `preview_question_export` | `create_question`, `update_question`, `delete_questions`, `import_questions` |
| `lessons` | `lesson_tree`, `browse_lessons`, `get_topic_lesson` | `import_lesson` |
| `materials` | `list_material_subjects`, `list_materials` | `create_material`, `update_material`, `delete_material` |
| `announcements` | `list_announcements`, `preview_announcement_audience` | `send_announcement`, `test_announcement`, `cancel_announcement` |
| `terms` | `list_academic_terms` | `create_academic_term`, `update_academic_term`, `delete_academic_term` |
| `catalogue` | `list_catalogue_subjects`, `list_curriculums`, `list_topics` | Owner: create, update, and delete for subjects, curriculums, and topics |
| `dashboard` | `admin_stats`, `admin_overview`, `admin_analytics` | — |
| `audit` | `audit_log`, `audit_actors` | — |
| `provider` | — | `provider_backfill` |
| `rag` | `list_rag_jobs` | `reindex_content` |
| `team` | Owner: `list_admins` | Owner: `create_admin`, `set_admin_status` |

Question export is a count plus a five-row preview. The CSV and ZIP stay on the existing export route. Lesson reads return a title and a block count, not the full markdown. Material upload signing stays a browser call to `/admin/api/materials/sign`; `create_material` needs a URL that already exists. Login, logout, forgot password, and reset password are not tools.

A non-owner who asks for an owner-only area gets a normal assistant message: “That area is limited to the owner account.” Hide owner prompts in the composer when the signed-in admin is not the owner.

---

## 9. Errors

| HTTP | Typical `error` | Frontend |
|------|-----------------|----------|
| `400` | `content is required`, `Invalid action payload`, validation | Inline on the composer or the card. Leave a pending card up |
| `401` | Unauthorized | Re-auth |
| `404` | Thread not found, Run not found | Toast and leave the thread, or load the thread after a finished rejoin |
| `409` | Reply still running, or a card is still pending | Keep the composer locked. Rejoin or show the card |
| `422` | FastAPI validation (`details`) | Inline form error |

A tool failure after approval is `200` with `actionResult.ok === false`. It is not an HTTP error.

---

## 10. Screen composition

1. **Left:** this admin's threads, new chat, archive.
2. **Center:** transcript and composer. Max length **8000**.
3. **Stage line** while `status` and `tool` events arrive.
4. **Card** docked on the pending assistant message. One pending card blocks send.
5. **Outcome** row after resume. Settled cards stay in history as read-only.

| State | Copy direction |
|-------|----------------|
| No threads | “Ask Astra to look up a student, a question, or the dashboard.” |
| Owner-only refusal | Keep the assistant text. Point owners at the team or catalogue screens |
| `409` pending | “Approve or dismiss the change first.” |
| `401` | Send the admin back through console login |

---

## 11. Recipes

### New chat, streaming first message

```
POST /admin/api/astra/threads                         {}
POST /admin/api/astra/threads/{id}/messages/stream    { content }
  → streamStarted … assistantMessage … done
```

### Continue, then approve

```
GET  /admin/api/astra/threads/{id}
POST /admin/api/astra/threads/{id}/messages/stream    { content }
  → actionProposal + done.paused
POST /admin/api/astra/threads/{id}/actions/resume
     { approved: true, actionId, stream: true }
  → actionExecuted, proposalSettled, assistantMessage, done
```

### Reload onto a waiting card

```
GET /admin/api/astra/threads/{id}
  → activeRun.status === "paused"
  → last assistant groundedOn[0].kind === "actionProposal"
```

### Rejoin a dropped stream

```
GET /admin/api/astra/threads/{id}                     → activeRun.runId
GET /admin/api/astra/threads/{id}/runs/{runId}/stream
    Last-Event-ID: <last id you rendered>
```

---

## 12. Out of this surface

- Student `/api/tutor/*` threads are a different product and a different table.
- History, replay, fork, and feedback routes are not mounted.
- Do not build Cloudinary signing, password reset, or CSV export inside the chat. Those stay on their existing console routes.
- Do not treat token concatenation as the final answer.
- Do not show Approve on a `groundedOn` item whose `status` is `executed` or `rejected`.

---

## 13. QA checklist

- [ ] Student session receives `401` on `GET /admin/api/astra/threads`
- [ ] Create blank, then stream the first message
- [ ] `streamStarted.runId` is what rejoin and stop use
- [ ] Tokens fill a draft; `assistantMessage` replaces it
- [ ] A read (`search_students`, `admin_stats`) finishes with no card
- [ ] A mutation opens a card and locks the composer (`409` on a second send)
- [ ] Reject writes nothing and the card becomes `rejected`
- [ ] Approve calls through once; the audit row matches the console action
- [ ] Editing a frozen id (`userId` on delete, tier, or force sign-out) returns `400` and the card stays pending
- [ ] Editable fields (`update_student`, `set_student_status`) round-trip on resume
- [ ] A non-owner does not see team, catalogue writes, delete, or force sign-out
- [ ] Stop keeps the user message
- [ ] Archive removes the thread from the list
- [ ] Reload on `activeRun: paused` redraws the card from `groundedOn`

---

## Related

- Student tutor: [`2026-10-21-ai-tutor-frontend-integration.md`](./2026-10-21-ai-tutor-frontend-integration.md)
- Admin HTTP map: [`../../backend-port/05-admin-api.md`](../../backend-port/05-admin-api.md)
- Routes: `app/api/admin/astra.py`
- Shapes: `app/api/schemas.py` (`AdminAstra*`), `app/api/responses.py` (`AdminAstraThreadOut`)
- Cards: `app/services/admin_astra/proposals.py`
- Tools: `app/services/admin_astra/tools/`
