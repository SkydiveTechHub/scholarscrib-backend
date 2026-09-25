from dataclasses import dataclass, field

from app.domain import GRADE_BOUNDARIES


def waec_grade(percentage: float) -> tuple[str, str, bool]:
    for threshold, grade, remark, credit in GRADE_BOUNDARIES:
        if percentage >= threshold:
            return grade, remark, credit
    return "F9", "Fail", False


def coarse_grade(percentage: float) -> str:
    """Performance history letters. Separate from the A1–F9 results scale."""
    if percentage >= 75:
        return "A"
    if percentage >= 65:
        return "B"
    if percentage >= 50:
        return "C"
    if percentage >= 40:
        return "D"
    return "F"


def jamb_band(score: float) -> str:
    if score >= 300:
        return "Excellent"
    if score >= 250:
        return "Strong"
    if score >= 200:
        return "Good"
    if score >= 160:
        return "Fair"
    return "Needs work"


@dataclass
class JambSectionScore:
    subject_id: str
    subject_name: str
    correct: int
    total: int
    marks: float


@dataclass
class JambPaperScore:
    score: float
    total_marks: int
    percentage: float
    subjects: list[JambSectionScore] = field(default_factory=list)
    band: str = ""


def score_jamb_paper(sections: list[tuple[str, str, int, int]]) -> JambPaperScore:
    """Each section is (subject_id, name, correct, total).

    Marks are correct/total*100.
    """
    scored: list[JambSectionScore] = []
    raw = 0.0
    for subject_id, name, correct, total in sections:
        marks = 0.0 if total == 0 else (correct / total) * 100
        raw += marks
        scored.append(
            JambSectionScore(
                subject_id=subject_id,
                subject_name=name,
                correct=correct,
                total=total,
                marks=round(marks, 1),
            )
        )
    score = round(raw, 1)
    percentage = round((score / 400) * 1000) / 10
    return JambPaperScore(
        score=score,
        total_marks=400,
        percentage=percentage,
        subjects=scored,
        band=jamb_band(score),
    )


def topic_breakdown_status(accuracy: float) -> str:
    if accuracy >= 80:
        return "strong"
    if accuracy >= 60:
        return "competent"
    if accuracy >= 40:
        return "developing"
    return "weak"


def round_percentage(correct_marks: float, total_marks: float) -> float:
    if total_marks <= 0:
        return 0.0
    return round((correct_marks / total_marks) * 1000) / 10
