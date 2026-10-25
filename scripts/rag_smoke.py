"""Smoke-test embeddings + Zilliz upsert/search.

Usage (with AI tutor env configured):

    .venv/bin/python -m scripts.rag_smoke
    .venv/bin/python -m scripts.rag_smoke --jev
"""

from __future__ import annotations

import argparse
import asyncio

from langchain_core.documents import Document

from app.ai.core.llm import get_embeddings
from app.ai.rag.vectorstore import (
    clear_vectorstore_cache,
    delete_by_expr,
    get_vectorstore,
    rag_ready,
    similarity_search,
    upsert_documents,
)
from app.core.config import settings


async def _smoke_jev() -> None:
    from app.ai.core.decisions import (
        build_decision_state,
        clear_jev_cache,
        decide_intent,
        decide_noul,
    )

    print("jev_ready:", settings.jev_ready)
    print("jev_model:", settings.jev_model)
    if not settings.jev_ready:
        raise SystemExit("Set TYPESAFE_API_KEY and JEV_ENABLED=true for --jev smoke.")

    clear_jev_cache()
    route, confidence, meta = await decide_intent(
        "Explain osmosis in plant cells with a short example"
    )
    print("choice_intent:", route, "confidence:", confidence, "meta:", meta)

    context = "Osmosis is the movement of water across a semi-permeable membrane."
    noul, _ = await decide_noul(
        build_decision_state(
            "What is osmosis?",
            context=context,
            answer="Osmosis is water moving across a membrane.",
        ),
        question_id="grounded",
        instructions=("Is the assistant answer supported by the provided context?"),
    )
    print("noul_grounded:", noul)
    print("jev ok")


def _smoke_vectorstore() -> None:
    print("ai_tutor_enabled:", settings.ai_tutor_enabled)
    print("rag_ready:", rag_ready())
    print("llm_model:", settings.llm_model)
    print("embedding_model:", settings.embedding_model)
    print("collection:", settings.milvus_collection)
    if not rag_ready():
        raise SystemExit(
            "Set AI_TUTOR_ENABLED=true, ZILLIZ_URI, ZILLIZ_TOKEN, and provider keys."
        )

    emb = get_embeddings()
    vector = emb.embed_query("photosynthesis chloroplast")
    print("embedding_dim:", len(vector))

    clear_vectorstore_cache()
    store = get_vectorstore()
    print("vectorstore:", type(store).__name__)

    doc_id = "smoke:rag:1"
    delete_by_expr(f'doc_id == "{doc_id}"')
    upsert_documents(
        [
            Document(
                page_content=(
                    "Photosynthesis converts light energy into chemical energy "
                    "in chloroplasts of plant cells."
                ),
                metadata={
                    "doc_id": doc_id,
                    "source_type": "lesson",
                    "subject_id": "smoke",
                    "topic_id": "smoke",
                    "lesson_id": "smoke",
                    "title": "Smoke test",
                    "chunk_index": 0,
                },
            )
        ]
    )
    hits = similarity_search("Where does photosynthesis happen?", k=3)
    print("hits:", len(hits))
    for hit in hits:
        print("-", hit.metadata.get("doc_id"), hit.page_content[:80])
    delete_by_expr(f'doc_id == "{doc_id}"')
    print("ok")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG / Jev smoke tests")
    parser.add_argument(
        "--jev",
        action="store_true",
        help="Run one live Choice + one Noul against TypeSafe (requires API key)",
    )
    args = parser.parse_args()
    if args.jev:
        asyncio.run(_smoke_jev())
        return
    _smoke_vectorstore()


if __name__ == "__main__":
    main()
