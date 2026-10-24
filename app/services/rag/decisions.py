"""TypeSafe Jev decision helpers for tutor graph judgment calls."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any, cast

from langchain_typesafe import Choice, Noul, TypeSafeClassifier
from langchain_typesafe.types import ClassifierResponse, Question
from pydantic import JsonValue

from app.core.config import settings
from app.core.logger import logger
from app.services.rag.llm import _export_provider_keys


@lru_cache(maxsize=1)
def get_jev_classifier() -> TypeSafeClassifier:
    _export_provider_keys()
    kwargs: dict[str, Any] = {
        "model": settings.jev_model or "jev-latest",
        "api_key": settings.typesafe_api_key,
    }
    if settings.typesafe_base_url:
        kwargs["base_url"] = settings.typesafe_base_url
    return TypeSafeClassifier(**kwargs)


def clear_jev_cache() -> None:
    get_jev_classifier.cache_clear()


def build_decision_state(
    question: str,
    *,
    history: str | None = None,
    context: str | None = None,
    answer: str | None = None,
    docs: list[str] | None = None,
) -> dict[str, Any]:
    """Normalize tutor inputs into JSON-friendly TypeSafe state."""
    state: dict[str, Any] = {"question": (question or "").strip()}
    if history:
        state["history"] = history[:2000]
    if context:
        state["context"] = context[:6000]
    if answer:
        state["answer"] = answer[:3000]
    if docs:
        state["documents"] = [
            {"id": f"doc_{i}", "text": text[:1500]} for i, text in enumerate(docs)
        ]
    return state


def _trace_entry(
    node: str,
    *,
    source: str,
    route: str | None = None,
    confidence: float | None = None,
    noul: float | None = None,
    label: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"node": node, "source": source}
    if route is not None:
        entry["route"] = route
    if confidence is not None:
        entry["confidence"] = confidence
    if noul is not None:
        entry["noul"] = noul
    if label is not None:
        entry["label"] = label
    if detail:
        entry["detail"] = detail
    return entry


async def decide_batch(
    state: Any,
    questions: dict[str, Question],
) -> ClassifierResponse | None:
    """Run one or more Jev questions; return None on disable/error (fail-open)."""
    if not settings.jev_ready:
        return None
    if not questions:
        return None
    try:
        classifier = get_jev_classifier()
        return await classifier.ainvoke({"state": state, "questions": questions})
    except Exception:  # noqa: BLE001
        logger.exception("Jev classifier request failed; falling back")
        return None


async def decide_choice(
    state: Any,
    *,
    question_id: str,
    instructions: str,
    criteria: dict[str, str],
) -> tuple[str | None, float | None, ClassifierResponse | None]:
    """Return (choice, confidence, raw) or (None, None, None) on failure."""
    choice_q: Question = Choice(
        instructions=instructions,
        criteria=cast(dict[str, JsonValue], criteria),
    )
    response = await decide_batch(state, {question_id: choice_q})
    if response is None:
        return None, None, None
    answer = response.choices.get(question_id)
    if answer is None:
        return None, None, response
    return answer.choice, answer.confidence, response


async def decide_noul(
    state: Any,
    *,
    question_id: str,
    instructions: str,
    criteria: dict[str, str] | None = None,
) -> tuple[float | None, ClassifierResponse | None]:
    """Return (noul probability, raw) or (None, None) on failure."""
    noul_kwargs: dict[str, Any] = {"instructions": instructions}
    if criteria:
        noul_kwargs["criteria"] = criteria
    noul_q: Question = Noul(**noul_kwargs)
    response = await decide_batch(state, {question_id: noul_q})
    if response is None:
        return None, None
    answer = response.nouls.get(question_id)
    if answer is None:
        return None, response
    return answer.noul, response


INTENT_CRITERIA = {
    "chitchat": (
        "Greeting, thanks, weather, small talk, or clearly non-academic chat "
        "that should be briefly acknowledged and redirected to study."
    ),
    "sql": (
        "Catalogue / inventory question about subjects, topics, lessons, or "
        "what content exists (lists, counts, curriculum structure)."
    ),
    "rag": (
        "Any academic or exam-prep question: explanations, past questions, "
        "lesson help, study skills, or subject content — with or without "
        "indexed materials."
    ),
    "action": (
        "The student wants to view or change their study plan, today's "
        "sessions, dashboard, performance, or topic standings "
        "(strong / weak topics) — not a content explanation."
    ),
}


async def decide_intent(
    question: str, *, history: str | None = None
) -> tuple[str | None, float | None, dict[str, Any]]:
    """Jev Choice for tutor route. Low confidence → caller should LLM-fallback."""
    state = build_decision_state(question, history=history)
    choice, confidence, _ = await decide_choice(
        state,
        question_id="intent",
        instructions=(
            "Route this student message for an exam-prep tutor. "
            "Pick exactly one of chitchat, sql, rag, or action."
        ),
        criteria=INTENT_CRITERIA,
    )
    meta = _trace_entry(
        "classify_intent",
        source="jev",
        route=choice,
        confidence=confidence,
    )
    if choice is None or confidence is None:
        return None, None, meta
    if confidence < settings.jev_confidence_threshold:
        meta["source"] = "jev_low_confidence"
        return None, confidence, meta
    if choice not in ("chitchat", "sql", "rag", "action"):
        return None, confidence, meta
    return choice, confidence, meta


async def decide_document_grades(
    question: str, doc_texts: list[str]
) -> tuple[list[float | None], dict[str, Any]]:
    """Parallel Nouls for each document; None scores mean API failure."""
    if not doc_texts:
        return [], _trace_entry("grade_documents", source="jev", detail={"count": 0})
    state = build_decision_state(question, docs=doc_texts)
    questions: dict[str, Question] = {
        f"doc_{i}": Noul(
            instructions=(
                f"Is document doc_{i} relevant and useful for answering "
                "the student's question? Yes only if it contains material "
                "that helps answer the question."
            ),
        )
        for i in range(len(doc_texts))
    }
    response = await decide_batch(state, questions)
    if response is None:
        return [None] * len(doc_texts), _trace_entry(
            "grade_documents", source="fallback", detail={"count": len(doc_texts)}
        )
    scores: list[float | None] = []
    detail: dict[str, float] = {}
    for i in range(len(doc_texts)):
        answer = response.nouls.get(f"doc_{i}")
        value = answer.noul if answer is not None else None
        scores.append(value)
        if value is not None:
            detail[f"doc_{i}"] = value
    return scores, _trace_entry(
        "grade_documents",
        source="jev",
        detail={"scores": detail, "threshold": settings.jev_grade_noul_threshold},
    )


def order_by_noul_scores[T](
    items: Sequence[T], scores: Sequence[float | None], *, top_k: int
) -> tuple[list[T], list[float]]:
    """Sort items by noul descending; drop None scores; keep top_k."""
    paired = [
        (item, score)
        for item, score in zip(items, scores, strict=False)
        if score is not None
    ]
    paired.sort(key=lambda row: row[1], reverse=True)
    clipped = paired[: max(0, top_k)]
    return [item for item, _ in clipped], [score for _, score in clipped]


async def decide_rerank(
    question: str, doc_texts: list[str], *, top_k: int
) -> tuple[list[int], list[float], dict[str, Any]]:
    """Score candidates with Jev Nouls and return ranked indices + scores."""
    if not doc_texts:
        return [], [], _trace_entry("rerank", source="jev", detail={"count": 0})
    scores, grade_meta = await decide_document_grades(question, doc_texts)
    if not any(score is not None for score in scores):
        return (
            [],
            [],
            _trace_entry(
                "rerank",
                source="fallback",
                detail={"count": len(doc_texts), "reason": "no_scores"},
            ),
        )
    indexed = list(range(len(doc_texts)))
    ordered_idx, ordered_scores = order_by_noul_scores(indexed, scores, top_k=top_k)
    return (
        ordered_idx,
        ordered_scores,
        _trace_entry(
            "rerank",
            source="jev",
            detail={
                "scores": (grade_meta.get("detail") or {}).get("scores", {}),
                "top_k": top_k,
                "kept": len(ordered_idx),
            },
        ),
    )


async def decide_should_rewrite(
    question: str, *, rewrite_count: int
) -> tuple[bool | None, dict[str, Any]]:
    """Whether a query rewrite is likely to find better material."""
    state = build_decision_state(
        question,
        context=f"rewrite_attempt={rewrite_count}",
    )
    noul, _ = await decide_noul(
        state,
        question_id="rewrite",
        instructions=(
            "Retrieved curriculum chunks were all irrelevant. "
            "Is a query rewrite likely to find better indexed lesson notes "
            "or past questions for this student question?"
        ),
    )
    meta = _trace_entry(
        "rewrite_gate",
        source="jev" if noul is not None else "fallback",
        noul=noul,
    )
    if noul is None:
        return None, meta
    return noul >= settings.jev_grade_noul_threshold, meta


async def decide_groundedness(
    question: str, context: str, answer: str
) -> tuple[bool | None, float | None, dict[str, Any]]:
    """Is the assistant answer supported by the provided context?"""
    state = build_decision_state(question, context=context, answer=answer)
    noul, _ = await decide_noul(
        state,
        question_id="grounded",
        instructions=(
            "Is the assistant answer supported by the provided context? "
            "Yes only if claims are grounded in the context and the answer "
            "does not invent unsupported facts."
        ),
    )
    meta = _trace_entry(
        "ground_answer",
        source="jev" if noul is not None else "fallback",
        noul=noul,
    )
    if noul is None:
        return None, None, meta
    return noul >= settings.jev_grade_noul_threshold, noul, meta


async def decide_worth_remembering(
    question: str, answer: str
) -> tuple[bool | None, float | None, dict[str, Any]]:
    """Is there a durable study preference/fact worth storing?"""
    state = build_decision_state(question, answer=answer)
    noul, _ = await decide_noul(
        state,
        question_id="remember",
        instructions=(
            "Is there a durable study preference or lasting fact about the "
            "student worth storing for future tutoring sessions? "
            "Yes for preferences, goals, weak topics, or personal study facts. "
            "No for one-off content questions or greetings."
        ),
    )
    meta = _trace_entry(
        "remember",
        source="jev" if noul is not None else "fallback",
        noul=noul,
    )
    if noul is None:
        return None, None, meta
    return noul >= settings.jev_confidence_threshold, noul, meta
