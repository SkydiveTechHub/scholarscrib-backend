# AI Tutor RAG — Design Spec

Date: 2026-10-20  
Status: Implemented (phased)  
Stack: LangChain + LangGraph in-process, Zilliz Cloud (Milvus)

## Goal

Students ask questions about a subject/topic (or start from a practice question /
lesson) and receive grounded answers from indexed lessons, Q&A, and PDF materials.

## Locked decisions

- Provider-agnostic models via `LLM_MODEL` / `EMBEDDING_MODEL` (`provider:model`)
- Vectors on Zilliz Cloud
- Entitlement: `aiTutor` → PREMIUM
- In-process LangGraph (not Agent Server)

## Corpus

| Source | Chunking | Metadata keys |
|--------|----------|---------------|
| Lesson blocks (+ extras) | One block ≈ one chunk | `source_type=lesson`, subject/topic/lesson ids |
| Questions | Stem + options + answer + explanation | `source_type=question` |
| PDF materials | Extracted text, recursive split | `source_type=material`, `resource_id` |

## Graph

`retrieve` → `grade_documents` (Jev Noul filter) → (`rewrite_question` loop) → `rerank` (Jev Noul sort / top‑k; legacy Flashrank/Cohere only if Jev off) → `generate_answer`

Citations returned as `groundedOn` on assistant messages.

## Student API

| Method | Path |
|--------|------|
| POST | `/api/tutor/threads` |
| GET | `/api/tutor/threads` |
| GET | `/api/tutor/threads/{id}` |
| DELETE | `/api/tutor/threads/{id}` |
| POST | `/api/tutor/threads/{id}/messages` |
| POST | `/api/tutor/threads/{id}/messages/stream` (SSE) |
| POST | `/api/tutor/messages/{id}/feedback` |
| POST | `/api/lessons/{lessonId}/tutor` (wrapper) |

Create body may include `subjectId`, `topicId`, `questionId`, `lessonId`,
`firstMessage`, `wrongAnswerContext`.

## Admin / Cron

- `GET /admin/api/rag/jobs`
- `POST /admin/api/rag/reindex`
- `POST /api/cron/rag/reindex` (CRON_SECRET)

Lesson import, question CRUD, and material CRUD schedule background reindex when
RAG is enabled.

## Env

See `.env.example` (`AI_TUTOR_ENABLED`, `ZILLIZ_*`, `LLM_MODEL`, …).
Smoke: `python -m scripts.rag_smoke`.
