"""LangGraph tutor: intent → chitchat | SQL | RAG, with summary + memory."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict
from uuid import uuid4

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logger import logger
from app.services.rag.decisions import (
    decide_document_grades,
    decide_groundedness,
    decide_intent,
    decide_should_rewrite,
    decide_worth_remembering,
)
from app.services.rag.llm import get_chat_model, get_grader_model
from app.services.rag.prompts import (
    CHITCHAT_SYSTEM,
    GRADE_PROMPT,
    INTENT_PROMPT,
    REMEMBER_PROMPT,
    REWRITE_PROMPT,
    SUMMARIZE_PROMPT,
    TUTOR_SYSTEM,
)
from app.services.rag.rerank import rerank_documents
from app.services.rag.schemas import GroundedCitation, citation_from_metadata
from app.services.rag.sql_agent import run_sql_agent
from app.services.rag.store import get_memory_store, memory_namespace
from app.services.rag.vectorstore import similarity_search_with_score

INSUFFICIENT_GROUNDED = (
    "I do not have enough grounded material for that yet. "
    "Try asking about a topic that has lesson notes or past questions "
    "indexed, or narrow the question to a specific subject or topic."
)


class GradeDocuments(BaseModel):
    binary_score: str = Field(description="Relevance score: 'yes' or 'no'")


class IntentRoute(BaseModel):
    route: Literal["chitchat", "sql", "rag"] = Field(description="chitchat | sql | rag")


def _append_trace(
    left: list[dict[str, Any]] | None, right: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    return (left or []) + (right or [])


class TutorState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    question: str
    documents: list[Document]
    citations: list[GroundedCitation]
    rewrite_count: int
    subject_id: str | None
    topic_id: str | None
    question_id: str | None
    lesson_id: str | None
    user_id: str | None
    route: str | None
    summary: str
    sql_result: str
    long_term_context: str
    decision_trace: Annotated[list[dict[str, Any]], _append_trace]
    decision_meta: dict[str, Any]
    skip_rewrite: bool


def _milvus_expr(state: TutorState) -> str | None:
    clauses: list[str] = []
    if state.get("subject_id"):
        clauses.append(f'subject_id == "{state["subject_id"]}"')
    if state.get("topic_id"):
        clauses.append(f'topic_id == "{state["topic_id"]}"')
    if state.get("lesson_id"):
        clauses.append(f'lesson_id == "{state["lesson_id"]}"')
    if not clauses:
        return None
    return " and ".join(clauses)


def _latest_user_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


def _message_text(message: BaseMessage) -> str:
    content = message.content
    return content if isinstance(content, str) else str(content)


def _heuristic_route(question: str) -> str | None:
    text = question.lower().strip()
    if not text:
        return "chitchat"
    chitchat_starts = (
        "hi",
        "hello",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
        "thanks",
        "thank you",
        "what's the weather",
        "whats the weather",
        "how are you",
    )
    is_chitchat = any(
        text == s or text.startswith(f"{s} ") or text.startswith(f"{s}!")
        for s in chitchat_starts
    )
    if is_chitchat and len(text) < 80:
        return "chitchat"
    sql_cues = (
        "what subjects",
        "which subjects",
        "list topics",
        "how many topics",
        "what topics",
        "curriculum",
        "which lessons",
        "list lessons",
        "subject list",
    )
    if any(cue in text for cue in sql_cues):
        return "sql"
    return None


def _llm_intent_route(question: str) -> str:
    grader = get_grader_model()
    prompt = INTENT_PROMPT.format(question=question)
    try:
        result = grader.with_structured_output(IntentRoute).invoke(
            [{"role": "user", "content": prompt}]
        )
        route = (
            result.route
            if isinstance(result, IntentRoute)
            else getattr(result, "route", "rag")
        )
    except Exception:  # noqa: BLE001
        route = "rag"
    if route not in ("chitchat", "sql", "rag"):
        route = "rag"
    return route


def _llm_grade_document(question: str, doc: Document) -> bool:
    grader = get_grader_model()
    prompt = GRADE_PROMPT.format(question=question, context=doc.page_content[:4000])
    try:
        result = grader.with_structured_output(GradeDocuments).invoke(
            [{"role": "user", "content": prompt}]
        )
        score = (
            result.binary_score
            if isinstance(result, GradeDocuments)
            else getattr(result, "binary_score", "no")
        )
    except Exception:  # noqa: BLE001
        score = "yes" if question.lower()[:20] in doc.page_content.lower() else "no"
    return str(score).lower().startswith("y")


async def classify_intent_node(state: TutorState) -> dict[str, Any]:
    question = state.get("question") or _latest_user_text(state["messages"])
    heuristic = _heuristic_route(question)
    if heuristic:
        meta = {
            "node": "classify_intent",
            "source": "heuristic",
            "route": heuristic,
            "confidence": 1.0,
        }
        return {
            "question": question,
            "route": heuristic,
            "decision_meta": meta,
            "decision_trace": [meta],
        }

    route: str | None = None
    meta: dict[str, Any]
    if settings.jev_ready:
        route, confidence, meta = await decide_intent(question)
        if route is not None:
            return {
                "question": question,
                "route": route,
                "decision_meta": meta,
                "decision_trace": [meta],
            }
        # low confidence or API miss → LLM fallback
        route = _llm_intent_route(question)
        meta = {
            **meta,
            "source": "llm",
            "route": route,
            "confidence": confidence,
        }
        return {
            "question": question,
            "route": route,
            "decision_meta": meta,
            "decision_trace": [meta],
        }

    route = _llm_intent_route(question)
    meta = {
        "node": "classify_intent",
        "source": "llm",
        "route": route,
    }
    return {
        "question": question,
        "route": route,
        "decision_meta": meta,
        "decision_trace": [meta],
    }


def route_after_intent(
    state: TutorState,
) -> Literal["chitchat", "sql_agent", "load_memory"]:
    route = state.get("route") or "rag"
    if route == "chitchat":
        return "chitchat"
    if route == "sql":
        return "sql_agent"
    return "load_memory"


def chitchat_node(state: TutorState) -> dict[str, Any]:
    model = get_chat_model()
    history = [
        msg for msg in state["messages"] if isinstance(msg, HumanMessage | AIMessage)
    ][-6:]
    response = model.invoke([SystemMessage(content=CHITCHAT_SYSTEM), *history])
    return {
        "messages": [response],
        "documents": [],
        "citations": [],
    }


def sql_agent_node(state: TutorState) -> dict[str, Any]:
    question = state.get("question") or _latest_user_text(state["messages"])
    try:
        result = run_sql_agent(question)
    except Exception:  # noqa: BLE001
        logger.exception("SQL agent failed")
        result = (
            "I could not query the curriculum catalogue right now. "
            "Try asking about a subject or topic by name."
        )
    return {
        "sql_result": result,
        "messages": [AIMessage(content=result)],
        "documents": [],
        "citations": [],
    }


async def load_memory_node(state: TutorState) -> dict[str, Any]:
    user_id = state.get("user_id")
    if not user_id:
        return {"long_term_context": ""}
    store = get_memory_store()
    if store is None:
        return {"long_term_context": ""}
    question = state.get("question") or ""
    try:
        items = await store.asearch(
            memory_namespace(user_id), query=question or None, limit=5
        )
    except Exception:  # noqa: BLE001
        logger.exception("long-term memory search failed")
        return {"long_term_context": ""}
    lines: list[str] = []
    for item in items:
        value = getattr(item, "value", None) or {}
        data = value.get("data") if isinstance(value, dict) else None
        if data:
            lines.append(str(data))
    return {"long_term_context": "\n".join(lines)}


def summarize_node(state: TutorState) -> dict[str, Any]:
    """Compress older turns into ``summary`` (messages stay intact for UI)."""
    messages = [
        msg for msg in state["messages"] if isinstance(msg, HumanMessage | AIMessage)
    ]
    threshold = settings.rag_summarize_after_messages
    keep = settings.rag_keep_recent_messages
    if len(messages) <= threshold:
        return {"summary": state.get("summary") or ""}

    older = messages[:-keep] if keep > 0 else messages
    prior = state.get("summary") or ""
    blob = "\n".join(
        f"{'User' if isinstance(m, HumanMessage) else 'Astra'}: {_message_text(m)}"
        for m in older
    )
    model = get_grader_model()
    prompt = SUMMARIZE_PROMPT.format(prior=prior or "(none)", messages=blob[:6000])
    try:
        response = model.invoke([{"role": "user", "content": prompt}])
        content = response.content
        summary = content if isinstance(content, str) else str(content)
    except Exception:  # noqa: BLE001
        summary = prior
    return {"summary": (summary or "").strip()}


def retrieve_node(state: TutorState) -> dict[str, Any]:
    query = state.get("question") or _latest_user_text(state["messages"])
    scored = similarity_search_with_score(
        query, k=settings.rag_retrieve_k, expr=_milvus_expr(state)
    )
    scored.sort(key=lambda item: item[1])
    documents = [doc for doc, _ in scored]
    qid = state.get("question_id")
    if qid:
        documents.sort(
            key=lambda doc: 0 if doc.metadata.get("question_id") == qid else 1
        )
    return {"question": query, "documents": documents, "skip_rewrite": False}


async def grade_documents_node(state: TutorState) -> dict[str, Any]:
    question = state["question"]
    documents = list(state.get("documents") or [])[: settings.rag_retrieve_k]
    trace: list[dict[str, Any]] = []
    kept: list[Document] = []
    skip_rewrite = False

    if settings.jev_ready and documents:
        texts = [doc.page_content for doc in documents]
        scores, meta = await decide_document_grades(question, texts)
        trace.append(meta)
        if any(s is not None for s in scores):
            threshold = settings.jev_grade_noul_threshold
            for doc, score in zip(documents, scores, strict=False):
                if score is not None and score >= threshold:
                    kept.append(doc)
        else:
            # full API miss → per-doc LLM fallback
            for doc in documents:
                if _llm_grade_document(question, doc):
                    kept.append(doc)
            trace.append(
                {
                    "node": "grade_documents",
                    "source": "llm",
                    "detail": {"count": len(documents)},
                }
            )
    else:
        for doc in documents:
            if _llm_grade_document(question, doc):
                kept.append(doc)
        trace.append(
            {
                "node": "grade_documents",
                "source": "llm" if documents else "empty",
                "detail": {"count": len(documents)},
            }
        )

    if (
        not kept
        and state.get("rewrite_count", 0) < settings.rag_max_rewrite_loops
        and settings.jev_ready
    ):
        should_rewrite, rewrite_meta = await decide_should_rewrite(
            question, rewrite_count=state.get("rewrite_count", 0)
        )
        trace.append(rewrite_meta)
        if should_rewrite is False:
            skip_rewrite = True
        # None (API fail) → allow rewrite (fail-open to existing loop)

    meta = trace[-1] if trace else {"node": "grade_documents", "source": "empty"}
    return {
        "documents": kept,
        "skip_rewrite": skip_rewrite,
        "decision_meta": meta,
        "decision_trace": trace,
    }


def route_after_grade(
    state: TutorState,
) -> Literal["rerank", "rewrite_question"]:
    if state.get("documents"):
        return "rerank"
    if state.get("skip_rewrite"):
        return "rerank"
    if state.get("rewrite_count", 0) >= settings.rag_max_rewrite_loops:
        return "rerank"
    return "rewrite_question"


def rewrite_question_node(state: TutorState) -> dict[str, Any]:
    model = get_grader_model()
    prompt = REWRITE_PROMPT.format(question=state["question"])
    response = model.invoke([{"role": "user", "content": prompt}])
    content = response.content
    rewritten = content if isinstance(content, str) else str(content)
    return {
        "question": rewritten.strip() or state["question"],
        "rewrite_count": state.get("rewrite_count", 0) + 1,
        "skip_rewrite": False,
    }


def rerank_node(state: TutorState) -> dict[str, Any]:
    docs = list(state.get("documents") or [])
    top = rerank_documents(state.get("question") or "", docs)
    citations = [
        citation_from_metadata(doc.metadata, excerpt=doc.page_content) for doc in top
    ]
    return {"documents": top, "citations": citations}


def generate_answer_node(state: TutorState) -> dict[str, Any]:
    docs = state.get("documents") or []
    if not docs:
        return {
            "messages": [AIMessage(content=INSUFFICIENT_GROUNDED)],
            "citations": [],
        }

    context = "\n\n---\n\n".join(
        f"[{doc.metadata.get('title') or doc.metadata.get('source_type')}] "
        f"{doc.page_content}"
        for doc in docs
    )
    memory = (state.get("long_term_context") or "").strip()
    summary = (state.get("summary") or "").strip()
    model = get_chat_model()
    keep = settings.rag_keep_recent_messages
    history = [
        msg for msg in state["messages"] if isinstance(msg, HumanMessage | AIMessage)
    ]
    if summary and len(history) > keep:
        history = history[-keep:]
    system_bits = [TUTOR_SYSTEM]
    if summary:
        system_bits.append(f"Thread summary:\n{summary}")
    if memory:
        system_bits.append(f"What we know about this student:\n{memory}")
    messages = [
        SystemMessage(content="\n\n".join(system_bits)),
        SystemMessage(content=f"Context:\n{context}"),
        *history,
    ]
    response = model.invoke(messages)
    return {
        "messages": [response],
        "citations": state.get("citations") or [],
    }


async def ground_answer_node(state: TutorState) -> dict[str, Any]:
    """Refuse unsupported RAG answers when Jev groundedness Noul is low."""
    if state.get("route") in ("chitchat", "sql"):
        return {}
    docs = state.get("documents") or []
    if not docs:
        return {}

    answer = ""
    for message in reversed(state.get("messages") or []):
        if isinstance(message, AIMessage):
            answer = _message_text(message)
            break
    if not answer:
        return {}

    question = state.get("question") or ""
    context = "\n\n---\n\n".join(doc.page_content[:2000] for doc in docs)
    if not settings.jev_ready:
        return {}

    grounded, _noul, meta = await decide_groundedness(question, context, answer)
    if grounded is False:
        return {
            "messages": [AIMessage(content=INSUFFICIENT_GROUNDED)],
            "citations": [],
            "decision_meta": meta,
            "decision_trace": [meta],
        }
    return {
        "decision_meta": meta,
        "decision_trace": [meta],
    }


async def remember_node(state: TutorState) -> dict[str, Any]:
    """Persist durable facts into the long-term store (cross-thread)."""
    user_id = state.get("user_id")
    if not user_id:
        return {}
    store = get_memory_store()
    if store is None:
        return {}
    question = state.get("question") or ""
    answer = ""
    for message in reversed(state.get("messages") or []):
        if isinstance(message, AIMessage):
            answer = _message_text(message)
            break
    if not question or not answer:
        return {}
    # Skip chitchat memorisation noise
    if state.get("route") == "chitchat":
        return {}

    trace: list[dict[str, Any]] = []
    if settings.jev_ready:
        worth, _noul, meta = await decide_worth_remembering(question, answer)
        trace.append(meta)
        if worth is False:
            return {
                "decision_meta": meta,
                "decision_trace": trace,
            }
        # worth True or None (API fail) → continue to extract

    model = get_grader_model()
    prompt = REMEMBER_PROMPT.format(question=question[:500], answer=answer[:800])
    try:
        response = model.invoke([{"role": "user", "content": prompt}])
        content = response.content
        text = (content if isinstance(content, str) else str(content)).strip()
    except Exception:  # noqa: BLE001
        return {"decision_trace": trace} if trace else {}
    if not text or text.upper() == "NONE":
        return {"decision_trace": trace} if trace else {}
    for line in text.splitlines():
        fact = line.strip(" -•\t")
        if not fact or fact.upper() == "NONE":
            continue
        try:
            await store.aput(
                memory_namespace(user_id),
                str(uuid4()),
                {"data": fact[:500]},
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to write long-term memory")
            break
    out: dict[str, Any] = {}
    if trace:
        out["decision_meta"] = trace[-1]
        out["decision_trace"] = trace
    return out


def build_tutor_graph(checkpointer=None, store=None):
    graph = StateGraph(TutorState)
    graph.add_node("classify_intent", classify_intent_node)
    graph.add_node("chitchat", chitchat_node)
    graph.add_node("sql_agent", sql_agent_node)
    graph.add_node("load_memory", load_memory_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("grade_documents", grade_documents_node)
    graph.add_node("rewrite_question", rewrite_question_node)
    graph.add_node("rerank", rerank_node)
    graph.add_node("generate_answer", generate_answer_node)
    graph.add_node("ground_answer", ground_answer_node)
    graph.add_node("remember", remember_node)

    graph.add_edge(START, "classify_intent")
    graph.add_conditional_edges("classify_intent", route_after_intent)
    graph.add_edge("chitchat", "remember")
    graph.add_edge("sql_agent", "remember")
    graph.add_edge("load_memory", "summarize")
    graph.add_edge("summarize", "retrieve")
    graph.add_edge("retrieve", "grade_documents")
    graph.add_conditional_edges("grade_documents", route_after_grade)
    graph.add_edge("rewrite_question", "retrieve")
    graph.add_edge("rerank", "generate_answer")
    graph.add_edge("generate_answer", "ground_answer")
    graph.add_edge("ground_answer", "remember")
    graph.add_edge("remember", END)

    return graph.compile(checkpointer=checkpointer, store=store)


_compiled = None


def get_tutor_graph(checkpointer=None, store=None):
    global _compiled
    if checkpointer is not None or store is not None:
        return build_tutor_graph(checkpointer=checkpointer, store=store)
    if _compiled is None:
        _compiled = build_tutor_graph()
    return _compiled
