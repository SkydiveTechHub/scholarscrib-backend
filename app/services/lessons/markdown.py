"""Natural lesson-note parser.

Ports the authoring rules in the lesson-note spec: title boilerplate, the info
line, quiz checks, short answers, and worked examples. Fence dialects and SVG
sanitising stay out of this pass; a ``:::`` fence is reported as an error so a
note cannot silently drop a block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TITLE = re.compile(r"^(?:.+?\s+)?lesson note:\s*", re.IGNORECASE)
_INFO = re.compile(r"^\*\*([^*]+):\*\*\s*(.*)$")
_QUIZ = re.compile(r"^quiz\b", re.IGNORECASE)
_WORKED = re.compile(r"^worked examples?\b", re.IGNORECASE)
_QUESTION = re.compile(r"^(\d+)\.\s+(.*)$")
_OPTION = re.compile(r"^\s*([a-hA-H])\)\s*(.*)$")
_SHORT = re.compile(r"\*\((?:short answer|answer):\s*(.+)\)\*\s*$", re.IGNORECASE)
_EXAMPLE = re.compile(
    r"^\*\*Example\s+(\d+)\*\*:?\s*(.*)$|^\*\*Example\s+(\d+):\*\*\s*(.*)$",
    re.IGNORECASE,
)
_SOLUTION = re.compile(
    r"^\*\*Solution:\*\*\s*(.*)$|^\*\*Solution\*\*:?\s*(.*)$", re.IGNORECASE
)
_RULE = re.compile(r"^(-{3,}|\*{3,}|_{3,})\s*$")
_MARKERS = ("✔", "✓", "✅")


@dataclass
class LessonIssue:
    line: int
    message: str


@dataclass
class ParsedLesson:
    title: str
    blocks: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[LessonIssue] = field(default_factory=list)
    doc_info: dict[str, str] = field(default_factory=dict)


def validate_lesson_markdown(source: str) -> ParsedLesson:
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    parsed = ParsedLesson(title="")
    heading_at = next(
        (index for index, line in enumerate(lines) if line.startswith("# ")), None
    )
    if heading_at is None:
        parsed.errors.append(LessonIssue(1, "A lesson note needs a title (# heading)."))
        return parsed
    raw_title = lines[heading_at][2:].strip()
    parsed.title = _TITLE.sub("", raw_title).strip() or raw_title
    cursor = heading_at + 1
    while cursor < len(lines) and not lines[cursor].strip():
        cursor += 1
    if cursor < len(lines):
        info, consumed = _doc_info(lines[cursor])
        if info is not None:
            parsed.doc_info = info
            cursor += consumed
    _scan(lines, cursor, parsed)
    return parsed


def _doc_info(line: str) -> tuple[dict[str, str] | None, int]:
    parts = [part.strip() for part in line.split("|")]
    if not parts:
        return None, 0
    info: dict[str, str] = {}
    for part in parts:
        match = _INFO.match(part.strip())
        if match is None:
            return None, 0
        info[match.group(1).strip()] = match.group(2).strip()
    return info, 1


def _scan(lines: list[str], start: int, parsed: ParsedLesson) -> None:
    index = start
    concept_n = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(":::"):
            parsed.errors.append(
                LessonIssue(
                    index + 1,
                    "Fenced blocks are not accepted. "
                    "Write the note in the natural format.",
                )
            )
            index += 1
            continue
        if line.startswith("# ") or line.startswith("## "):
            title = line.lstrip("#").strip()
            if line.startswith("## ") and _QUIZ.match(title):
                index = _quiz(lines, index + 1, title, parsed)
                continue
            if line.startswith("## ") and _WORKED.match(title):
                index = _worked(lines, index + 1, parsed)
                continue
            concept_n += 1
            index = _concept(lines, index + 1, title, concept_n, parsed)
            continue
        if _RULE.match(line.strip()):
            index += 1
            continue
        if line.strip():
            concept_n += 1
            parsed.blocks.append(
                {"id": f"concept-{concept_n}", "type": "concept", "text": line.strip()}
            )
        index += 1


def _heading(line: str) -> bool:
    return line.startswith("# ") or line.startswith("## ") or line.startswith(":::")


def _concept(
    lines: list[str], start: int, title: str, number: int, parsed: ParsedLesson
) -> int:
    body: list[str] = []
    index = start
    while index < len(lines) and not _heading(lines[index]):
        if not _RULE.match(lines[index].strip()):
            body.append(lines[index])
        index += 1
    text = "\n".join(body).strip()
    if text:
        parsed.blocks.append(
            {"id": f"concept-{number}", "type": "concept", "title": title, "text": text}
        )
    return index


def _quiz(lines: list[str], start: int, heading: str, parsed: ParsedLesson) -> int:
    index = start
    prose: list[str] = []
    check_n = 0
    short_n = 0
    while index < len(lines) and not _heading(lines[index]):
        match = _QUESTION.match(lines[index])
        if match is None:
            if lines[index].strip() and not _RULE.match(lines[index].strip()):
                prose.append(lines[index].strip())
            index += 1
            continue
        if prose:
            parsed.blocks.append(
                {
                    "id": f"concept-quiz-{heading[:24]}",
                    "type": "concept",
                    "title": heading,
                    "text": " ".join(prose),
                }
            )
            prose = []
        number = int(match.group(1))
        question = [match.group(2).strip()]
        line_no = index + 1
        index += 1
        options: list[tuple[str, str, bool]] = []
        while (
            index < len(lines)
            and not _heading(lines[index])
            and _QUESTION.match(lines[index]) is None
        ):
            option = _OPTION.match(lines[index])
            if option:
                text, marked = _strip_mark(option.group(2).strip())
                options.append((option.group(1).upper(), text, marked))
            elif lines[index].strip() and not _RULE.match(lines[index].strip()):
                if options:
                    text, marked = _strip_mark(lines[index].strip())
                    key, previous, was = options[-1]
                    options[-1] = (key, f"{previous} {text}".strip(), was or marked)
                else:
                    question.append(lines[index].strip())
            index += 1
        prompt = " ".join(question).strip()
        short = _SHORT.search(prompt)
        if short and not options:
            short_n += 1
            parsed.blocks.append(
                {
                    "id": f"short-answer-{short_n}",
                    "type": "concept",
                    "text": _SHORT.sub("", prompt).strip(),
                    "reveal": short.group(1).strip(),
                }
            )
            continue
        if not options and short is None:
            parsed.errors.append(
                LessonIssue(
                    line_no,
                    f'Question {number} has no options and no "(Short answer: …)".',
                )
            )
            continue
        if len(options) == 1:
            parsed.errors.append(
                LessonIssue(
                    line_no,
                    f"Question {number} has only one option — "
                    "a check needs at least two.",
                )
            )
            continue
        marked = [item for item in options if item[2]]
        if not marked:
            parsed.errors.append(
                LessonIssue(
                    line_no,
                    f"Question {number} has no correct option — mark it with ✔.",
                )
            )
            continue
        if len(marked) > 1:
            parsed.errors.append(
                LessonIssue(
                    line_no,
                    f"Question {number} marks {len(marked)} correct options — "
                    "a check needs exactly one.",
                )
            )
            continue
        check_n += 1
        parsed.blocks.append(
            {
                "id": f"check-{check_n}",
                "type": "check",
                "text": prompt,
                "options": {key: text for key, text, _marked in options},
                "answer": marked[0][0],
            }
        )
    return index


def _strip_mark(text: str) -> tuple[str, bool]:
    marked = (
        any(marker in text for marker in _MARKERS)
        or text.endswith("*")
        or text.endswith("**")
    )
    cleaned = text
    for marker in _MARKERS:
        cleaned = cleaned.replace(marker, "")
    cleaned = cleaned.removesuffix("**").removesuffix("*").strip()
    return cleaned, marked


def _worked(lines: list[str], start: int, parsed: ParsedLesson) -> int:
    index = start
    example_n = 0
    while index < len(lines) and not _heading(lines[index]):
        match = _example_open(lines[index])
        if match is None:
            index += 1
            continue
        number, problem = match
        line_no = index + 1
        index += 1
        solution_at = None
        steps: list[str] = []
        while (
            index < len(lines)
            and not _heading(lines[index])
            and _example_open(lines[index]) is None
        ):
            solution = _solution_open(lines[index])
            if solution is not None and solution_at is None:
                solution_at = index + 1
                if solution:
                    steps.append(solution)
            elif lines[index].strip() and not _RULE.match(lines[index].strip()):
                steps.append(lines[index].strip())
            index += 1
        if solution_at is None:
            parsed.errors.append(
                LessonIssue(line_no, f"Example {number} has no solution.")
            )
            continue
        example_n += 1
        answer, kept = _answer(steps)
        parsed.blocks.append(
            {
                "id": f"example-{example_n}",
                "type": "example",
                "mode": "worked",
                "text": problem,
                "steps": kept,
                "answer": answer,
            }
        )
    return index


def _example_open(line: str) -> tuple[str, str] | None:
    match = _EXAMPLE.match(line.strip())
    if match is None:
        return None
    number = match.group(1) or match.group(3)
    problem = (match.group(2) if match.group(1) else match.group(4)) or ""
    return number, problem.strip()


def _solution_open(line: str) -> str | None:
    match = _SOLUTION.match(line.strip())
    if match is None:
        return None
    return (
        match.group(1) if match.group(1) is not None else match.group(2) or ""
    ).strip()


def _answer(steps: list[str]) -> tuple[str, list[str]]:
    if not steps:
        return "", []
    last = steps[-1]
    bold = re.search(r"\*\*(.+?)\*\*\s*$", last)
    answer = bold.group(1).strip() if bold else last
    return answer, steps[:-1]
