"""Coverage questions presented as paper questions.

Mock exams sit questions from the coverage table. The grading and result code
reads ``Question``-shaped objects, so a coverage row is exposed under the same
attribute names, flagged with ``is_coverage`` so the response and the assessment
link are stored against the coverage table instead of ``Question``.
"""

from types import SimpleNamespace

from app.database.models import CoverageQuestion

COVERAGE_TIME_ESTIMATE_SECONDS = 90


def as_paper_question(question: CoverageQuestion) -> SimpleNamespace:
    return SimpleNamespace(
        is_coverage=True,
        id=question.id,
        subject_id=question.subject_id,
        topic_id=question.topic_id,
        exam_type=question.exam_type,
        exam_year=question.exam_year,
        question_number=question.question_number,
        question_text=question.question_text,
        question_image_url=question.question_image,
        question_type=question.question_type,
        options=question.options,
        correct_answer=question.correct_answer,
        explanation=question.explanation,
        explanation_image_url=question.solution_image,
        difficulty=question.difficulty,
        marks=question.marks,
        time_estimate_seconds=COVERAGE_TIME_ESTIMATE_SECONDS,
    )


def is_coverage(question) -> bool:
    return bool(getattr(question, "is_coverage", False))
