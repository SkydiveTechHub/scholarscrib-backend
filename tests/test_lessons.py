from app.services.console import audience_matches
from app.services.lessons.markdown import validate_lesson_markdown


class _User:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", "u1")
        self.class_level = kwargs.get("class_level")
        self.track = kwargs.get("track")
        self.tier = kwargs.get("tier", "FREEMIUM")


def test_quiz_requires_a_marked_option():
    parsed = validate_lesson_markdown(
        """# Physics Lesson Note: Measurement
## Quiz
1. Which is a fundamental quantity?
   a) Speed
   b) Mass
"""
    )
    assert parsed.title == "Measurement"
    assert parsed.errors
    assert "no correct option" in parsed.errors[0].message


def test_quiz_check_and_short_answer():
    parsed = validate_lesson_markdown(
        """# Measurement and Units
**Class:** SSS1 | **Term:** First Term
## Quiz
1. Which is fundamental?
   a) Speed
   b) Mass ✔
8. Convert 3,000 g to kilograms. *(Short answer: 3 kg)*
"""
    )
    assert parsed.errors == []
    assert parsed.doc_info["Class"] == "SSS1"
    check = next(block for block in parsed.blocks if block["type"] == "check")
    assert check["answer"] == "B"
    assert check["options"]["B"] == "Mass"
    short = next(block for block in parsed.blocks if block.get("reveal"))
    assert short["reveal"] == "3 kg"
    assert "3,000 g" in short["text"]


def test_worked_example_keeps_the_bold_answer():
    parsed = validate_lesson_markdown(
        """# Motion
## Worked examples
**Example 2:** A trip takes 2 hours.
**Solution:**
2 hours = 7,200 s
Total = **9,000 seconds**
"""
    )
    assert parsed.errors == []
    example = parsed.blocks[0]
    assert example["type"] == "example"
    assert example["answer"] == "9,000 seconds"
    assert example["steps"] == ["2 hours = 7,200 s"]


def test_audience_filter_matches_class_and_exam():
    student = _User(class_level="SS2", track="SCIENCE", tier="STANDARD")
    assert audience_matches({}, student, None)
    assert audience_matches({"classLevels": ["SS2"]}, student, "WAEC")
    assert not audience_matches({"classLevels": ["SS3"]}, student, "WAEC")
    assert not audience_matches({"examTargets": ["JAMB"]}, student, "WAEC")
    assert audience_matches(
        {"examTargets": ["WAEC"], "tiers": ["STANDARD"]}, student, "WAEC"
    )
