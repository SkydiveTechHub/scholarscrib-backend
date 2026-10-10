from pathlib import Path

import pytest

from app.services.lessons.markdown import (
    parse_lesson_markdown,
    strip_answer_marker,
    validate_lesson_markdown,
)
from app.services.lessons.svg import sanitize_svg

FIXTURE = Path(__file__).parent / "fixtures" / "measurement-and-units.md"
QUIZ = "## Quiz\n1. Q?\n a) x\n b) y ✔\n"


def _messages(parsed):
    return [issue.message for issue in parsed.errors]


# ── the real teacher's note ──────────────────────────────────


def test_fixture_note_uploads_without_errors():
    parsed = validate_lesson_markdown(FIXTURE.read_text(encoding="utf-8"))
    assert parsed.errors == []
    assert parsed.title == "Measurement and Units"
    assert parsed.doc_info["Class"] == "SSS1"
    kinds = [b["type"] for b in parsed.blocks]
    assert kinds.count("example") == 3
    assert kinds.count("check") >= 1


# ── afterCard on every check ─────────────────────────────────


def test_checks_carry_after_card_and_canonical_keys():
    parsed = validate_lesson_markdown(f"# T\n## Idea\nSome words.\n{QUIZ}")
    check = next(b for b in parsed.blocks if b["type"] == "check")
    assert check["afterCard"] == "idea-1"
    assert check["question"] == "Q?"
    assert "explanation" in check
    assert parsed.errors == []


# ── the 120-word cap ─────────────────────────────────────────


def test_long_section_is_split_with_a_warning():
    para = " ".join(["word"] * 70)
    parsed = validate_lesson_markdown(f"# T\n## Big\n{para}\n\n{para}\n{QUIZ}")
    assert parsed.errors == []
    assert len([b for b in parsed.blocks if b["type"] == "concept"]) == 2
    assert any("split into 2 cards" in w for w in parsed.warnings)


def test_single_overlong_paragraph_is_rejected():
    para = " ".join(["word"] * 130)
    parsed = validate_lesson_markdown(f"# T\n## Big\n{para}\n{QUIZ}")
    assert any("cards must be ≤ 120" in m for m in _messages(parsed))


# ── stray headings ───────────────────────────────────────────


def test_later_h1_is_not_a_concept_heading():
    parsed = parse_lesson_markdown("# Title\n## A\nbody\n# Second\nmore body\n")
    assert parsed.title == "Title"
    assert [b.get("title") for b in parsed.blocks] == ["A", None]


# ── fences, frontmatter, svg ─────────────────────────────────

ALL_FENCES = """# T
## Idea
Text.
:::tip
Exam: JAMB
Read the stem twice.
:::
:::mistake
Wrong: Ribosomes make energy.
Right: Mitochondria make energy.
:::
:::mnemonic
Phrase: My Very Easy
Encoded: Mercury
Encoded: Venus
:::
:::example
Problem: F = ma, m = 4, a = 5. Find F.
Step: Substitute.
Answer: 20 N
Mode: partial
:::
:::check
Q: Unit of force?
A) Joule
B) Newton
Correct: B
Why: Named after Newton.
:::
:::diagram
Title: Eye
Caption: Light path.
<svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="2"/></svg>
Hotspot: Pupil @ 5,5 — Lets light in.
:::
"""


def test_all_fence_types_parse():
    parsed = validate_lesson_markdown(ALL_FENCES)
    assert parsed.errors == []
    by_type = {b["type"]: b for b in parsed.blocks}
    assert by_type["tip"]["examType"] == "JAMB"
    assert by_type["mistake"]["right"].startswith("Mitochondria")
    assert by_type["mnemonic"]["encoded"] == ["Mercury", "Venus"]
    assert by_type["example"]["mode"] == "partial"
    assert by_type["check"]["afterCard"] == "example-1"
    diagram = by_type["diagram"]
    assert diagram["svg"].startswith("<svg viewBox=")
    assert diagram["hotspots"][0]["x"] == 5


@pytest.mark.parametrize(
    "fence, message",
    [
        (":::tip\n:::", "no text"),
        (":::mistake\nWrong: a\n:::", "both Wrong"),
        (":::mnemonic\nEncoded: a\n:::", "Phrase"),
        (":::example\nProblem: p\n:::", "Answer"),
        (":::check\nQ: q\nA) a\nB) b\nCorrect: C\n:::", "not one of"),
        (":::banana\nx\n:::", "Unknown fence"),
        (":::tip\nunclosed", "never closed"),
    ],
)
def test_malformed_fences_error(fence, message):
    parsed = parse_lesson_markdown(f"# T\n## A\ntext\n{fence}\n")
    assert any(message in m for m in _messages(parsed))


def test_frontmatter_is_parsed_and_validated():
    parsed = parse_lesson_markdown(
        "---\ntitle: Cells\npassMarkPercent: 70\npracticeCount: 5\n"
        "difficulty: BASIC\nbogus: 1\n---\n## A\ntext\n"
    )
    assert parsed.title == "Cells"
    assert parsed.meta["passMarkPercent"] == 70
    assert parsed.meta["practiceCount"] == 5
    assert any("Unknown frontmatter key" in w for w in parsed.warnings)
    bad = parse_lesson_markdown(
        "---\npassMarkPercent: 150\ndifficulty: HARD\n---\n## A\ntext\n"
    )
    assert len(bad.errors) == 2


def test_svg_xss_payloads_come_out_inert():
    payloads = [
        '<svg onload="alert(1)"><circle onclick="x()" r="1"/></svg>',
        "<svg><script>alert(1)</script><circle r='1'/></svg>",
        "<svg><foreignObject><div>hi</div></foreignObject></svg>",
        '<svg><a href="javascript:alert(1)"><text>x</text></a></svg>',
        '<svg><use href="http://evil/x.svg#a"/></svg>',
        '<svg><rect fill="url(http://evil/x)" width="1" height="1"/></svg>',
        "<svg><scr<use/>ipt>alert(1)</scr<use/>ipt></svg>",
        "<svg><style>@import 'x'</style></svg>",
    ]
    for payload in payloads:
        svg, _ = sanitize_svg(payload)
        low = svg.lower()
        for bad in (
            "script", "onload", "onclick", "foreignobject",
            "javascript", "<use", "evil", "<style",
        ):  # fmt: skip
            assert bad not in low, (payload, svg)


def test_svg_must_be_svg_and_unterminated_hostile_fails_closed():
    assert sanitize_svg("<div>no svg</div>")[0] == ""
    svg, _ = sanitize_svg("<svg><circle r='1'/><script>alert(1)")
    assert "script" not in svg
    assert svg.endswith("</svg>")


def test_svg_keeps_camel_case_and_safe_paint_urls():
    svg, _ = sanitize_svg(
        '<svg viewBox="0 0 1 1"><defs><linearGradient id="g"><stop offset="0"/>'
        '</linearGradient></defs><rect fill="url(#g)" width="1" height="1"/></svg>'
    )
    assert 'viewBox="0 0 1 1"' in svg
    assert "<linearGradient" in svg
    assert 'fill="url(#g)"' in svg


# ── the "mistakes that block the save" list ──────────────────


def test_missing_title_is_allowed():
    parsed = validate_lesson_markdown("## A\ntext\n## Quiz\n1. Q?\n a) x ✔\n b) y\n")
    assert parsed.errors == []
    assert parsed.title == ""


def test_quiz_accepts_paren_numbering_and_dot_letters():
    parsed = validate_lesson_markdown(
        "# T\n## A\ntext\n## Quiz\n1) Q?\n a. x\n b. y ✔\n"
    )
    assert parsed.errors == []
    assert parsed.blocks[-1]["answer"] == "B"


def test_short_answer_accepts_qualified_label():
    parsed = parse_lesson_markdown(
        "# T\n## A\nt\n## Quiz\n1. Say why. *(Short answer — sample: Because.)*\n"
    )
    block = parsed.blocks[-1]
    assert block["reveal"] == "Because."
    assert block["text"] == "Say why."


def test_theory_question_without_answer_is_a_card_not_an_error():
    parsed = validate_lesson_markdown(
        "# T\n## A\nt\n## Quiz\n1. State one safety rule.\n"
    )
    assert not any("no options" in m for m in _messages(parsed))
    assert any(b["id"].startswith("theory") for b in parsed.blocks)


def test_example_problem_may_wrap_before_solution():
    parsed = parse_lesson_markdown(
        "# T\n## Worked Examples\n**Example 1:** A long\nproblem here.\n"
        "**Solution:**\n1 + 1\n**2**\n"
    )
    ex = parsed.blocks[0]
    assert ex["problem"] == "A long problem here."
    assert ex["steps"] == ["1 + 1"]
    assert ex["answer"] == "2"


def test_trailing_bold_option_is_not_read_as_a_marker():
    assert strip_answer_marker("the term is **key**") == ("the term is **key**", False)
    assert strip_answer_marker("Mass ✔") == ("Mass", True)
    assert strip_answer_marker("Mass *") == ("Mass", True)


def test_check_marker_errors_carry_line_numbers():
    none = parse_lesson_markdown("# T\n## A\nt\n## Quiz\n1. Q?\n a) x\n b) y\n")
    assert none.errors[0].line == 5
    assert "no correct option" in none.errors[0].message
    two = parse_lesson_markdown("# T\n## A\nt\n## Quiz\n1. Q?\n a) x ✔\n b) y ✔\n")
    assert "marks 2 correct" in two.errors[0].message
    one = parse_lesson_markdown("# T\n## A\nt\n## Quiz\n1. Q?\n a) x ✔\n")
    assert "only one option" in one.errors[0].message


def test_example_without_solution_errors_at_its_line():
    parsed = parse_lesson_markdown("# T\n## Worked Examples\n**Example 1:** Do it.\n")
    assert parsed.errors[0].line == 3
    assert "no **Solution:**" in parsed.errors[0].message


def test_lint_requires_concept_and_check_and_forbids_leading_check():
    no_check = validate_lesson_markdown("# T\n## A\ntext\n")
    assert any("at least one knowledge check" in m for m in _messages(no_check))
    leading = validate_lesson_markdown(
        "# T\n:::check\nQ: q\nA) a\nB) b\nCorrect: A\n:::\n"
    )
    assert any("cannot be the first block" in m for m in _messages(leading))


def test_horizontal_rules_are_dropped():
    parsed = parse_lesson_markdown("# T\n## A\none\n---\ntwo\n")
    assert "---" not in parsed.blocks[0]["text"]


def test_info_line_only_directly_under_title():
    ok = parse_lesson_markdown("# T\n**Class:** SSS1 | **Term:** First\n## A\nx\n")
    assert ok.doc_info == {"Class": "SSS1", "Term": "First"}
    prose = parse_lesson_markdown("# T\nThis is **important** for WAEC.\n")
    assert prose.doc_info == {}
    assert prose.blocks
