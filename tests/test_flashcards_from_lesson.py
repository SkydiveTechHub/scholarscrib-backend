from app.services.lessons.markdown import validate_lesson_markdown
from app.services.srs import cards_from_blocks

LESSON = """# Biology Lesson Note: The Cell
## Learning Objectives
1. Define a cell.
2. Name the organelles.

## What is a Cell?
The cell is the basic unit of life.

### Reveal
Cells come from other cells.

:::check
Q: Which structure controls entry and exit?
A) Nucleus
B) Cell membrane
Correct: B
Why: It is selectively permeable.
:::

:::mistake
Wrong: Ribosomes make energy.
Right: Mitochondria make energy.
:::

:::mnemonic
Phrase: Mike Plays Rough Guitars
Encoded: Mitochondrion
Encoded: Ribosome
:::

## Worked Examples
Study each solution, then try the next one yourself.

**Example 1:** Why can a red blood cell not respire aerobically?
**Solution:**
Aerobic respiration needs mitochondria.
It has none, so it uses **anaerobic respiration**

## Quiz (2 Questions)
Answer all questions.

1. Which organelle makes protein?
   a) Mitochondrion
   b) Ribosome ✔
2. State one function of the nucleus. *(Short answer: It controls the cell.)*
"""


def _cards():
    parsed = validate_lesson_markdown(LESSON)
    assert parsed.errors == []
    return cards_from_blocks(parsed.blocks)


def _by_front(cards):
    return {c["payload"]["front"]: c["payload"]["back"] for c in cards}


def test_objectives_and_instruction_cards_never_become_flashcards():
    fronts = " | ".join(_by_front(_cards()))
    assert "Learning Objectives" not in fronts
    assert "Define a cell" not in fronts
    assert "Quiz" not in fronts
    assert "Worked Examples" not in fronts
    assert "Study each solution" not in fronts
    assert "Answer all questions" not in fronts


def test_every_study_block_still_produces_a_card():
    cards = _by_front(_cards())
    assert cards["What is a Cell?"] == (
        "The cell is the basic unit of life.\n\nCells come from other cells."
    )
    assert cards["Which structure controls entry and exit?"].startswith(
        "B) Cell membrane"
    )
    assert "selectively permeable" in cards["Which structure controls entry and exit?"]
    assert cards["Ribosomes make energy."] == "Mitochondria make energy."
    assert cards["Mike Plays Rough Guitars"] == "Mitochondrion\nRibosome"
    example = cards["Why can a red blood cell not respire aerobically?"]
    assert example.endswith("Answer: anaerobic respiration")
    assert "Aerobic respiration needs mitochondria." in example


def test_short_answer_card_asks_the_question_and_answers_on_the_back():
    cards = _by_front(_cards())
    assert cards["State one function of the nucleus."] == "It controls the cell."


def test_source_keys_are_unique_and_stable():
    keys = [c["sourceKey"] for c in _cards()]
    assert len(keys) == len(set(keys))
    assert keys == [c["sourceKey"] for c in _cards()]


def test_legacy_blocks_keyed_on_text_still_work():
    blocks = [
        {"id": "c1", "type": "concept", "title": "Force", "text": "A push or pull."},
        {
            "id": "k1",
            "type": "check",
            "text": "Unit of force?",
            "options": {"A": "N", "B": "J"},
            "answer": "A",
        },
        {
            "id": "e1",
            "type": "example",
            "text": "Find F.",
            "steps": ["F = ma"],
            "answer": "20 N",
        },
    ]
    cards = _by_front(cards_from_blocks(blocks))
    assert cards["Force"] == "A push or pull."
    assert cards["Unit of force?"] == "A) N"
    assert cards["Find F."] == "F = ma\nAnswer: 20 N"


def test_short_block_becomes_a_question_and_answer_card():
    cards = cards_from_blocks(
        [
            {
                "id": "s1",
                "type": "short",
                "question": "Define diffusion.",
                "answer": "Spreading out.",
            }
        ]
    )
    assert cards[0]["payload"]["front"] == "Define diffusion."
    assert cards[0]["payload"]["back"] == "Spreading out."
    assert cards[0]["cardType"] == "DEFINITION"
