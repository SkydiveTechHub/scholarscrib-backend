"""Unit tests for RAG document loaders (no Zilliz / LLM)."""

from langchain_core.documents import Document

from app.ai.core.schemas import citation_from_metadata
from app.ai.rag.chunking import content_hash, split_if_needed
from app.ai.rag.loaders.lessons import lesson_documents
from app.ai.rag.loaders.questions import question_documents
from app.database.models import Lesson, Question


def test_lesson_documents_from_blocks():
    lesson = Lesson(
        id="les1",
        subtopic_id="sub1",
        title="Cell structure",
        blocks=[
            {"type": "concept", "title": "Nucleus", "body": "Controls the cell."},
            {"type": "tip", "text": "Remember DNA is in the nucleus."},
        ],
        key_points=["Nucleus holds DNA"],
    )
    docs = lesson_documents(
        lesson, subject_id="sub", topic_id="top", topic_title="Cells"
    )
    assert len(docs) >= 2
    assert all(d.metadata["source_type"] == "lesson" for d in docs)
    assert docs[0].metadata["lesson_id"] == "les1"
    assert "Nucleus" in docs[0].page_content


def test_question_documents_include_answer():
    question = Question(
        id="q1",
        subject_id="sub",
        topic_id="top",
        exam_type="JAMB",
        exam_year=2020,
        question_text="What is 2+2?",
        question_type="OBJECTIVE",
        options={"A": "3", "B": "4", "C": "5", "D": "6"},
        correct_answer="B",
        explanation="Basic arithmetic.",
        difficulty="BASIC",
    )
    docs = question_documents(question)
    assert len(docs) == 1
    text = docs[0].page_content
    assert "2+2" in text
    assert "Correct answer: B" in text
    assert "Basic arithmetic" in text
    assert docs[0].metadata["doc_id"] == "question:q1"


def test_split_if_needed_adds_hash():
    doc = Document(page_content="short", metadata={"doc_id": "x"})
    parts = split_if_needed(doc)
    assert len(parts) == 1
    assert parts[0].metadata["content_hash"] == content_hash("short")


def test_citation_from_metadata():
    cite = citation_from_metadata(
        {
            "source_type": "lesson",
            "doc_id": "lesson:1:block:0",
            "subject_id": "s",
            "lesson_id": "1",
            "title": "Nucleus",
        },
        excerpt="Controls the cell",
    )
    assert cite["sourceType"] == "lesson"
    assert cite["lessonId"] == "1"
    assert "Controls" in cite["excerpt"]
