# ruff: noqa: E501  (author-facing messages read better on one line)
"""Lesson-note parser — markdown to lesson blocks, plus the authoring lint.

A port of the frontend parser (``src/lib/lesson-markdown``) so the server, which
is the authority on what gets saved, accepts exactly what the admin preview
accepts. Two dialects, which may be mixed freely in one file:

* the *natural* teacher's markdown: ``# Title``, an info line, ``## Quiz``,
  ``## Worked Examples`` and plain ``## Heading`` concept cards;
* ``:::type`` fences for ``example``, ``tip``, ``mistake``, ``mnemonic``,
  ``check`` and ``diagram`` blocks.

The parser owns syntax; ``lint_blocks`` owns pedagogy (card length, a concept, a
check, ``afterCard`` targets) and mirrors ``lintLessonBlocks`` in
``lesson-engine.ts``. ``validate_lesson_markdown`` runs both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.lessons.svg import sanitize_svg

MAX_CARD_WORDS = 120
EXAM_TYPES = ("WAEC", "JAMB", "NECO")
DIFFICULTIES = ("BASIC", "INTERMEDIATE", "ADVANCED")
FENCE_TYPES = ("example", "tip", "mistake", "mnemonic", "check", "diagram")
TEXT_KEYS = ("title", "summary", "subject", "topic")
NUMBER_KEYS = ("estimatedMinutes", "passMarkPercent", "practiceCount")


@dataclass
class LessonIssue:
    line: int | None
    message: str


@dataclass
class ParsedLesson:
    title: str
    blocks: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[LessonIssue] = field(default_factory=list)
    doc_info: dict[str, str] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def warn(self, line: int | None, message: str) -> None:
        self.warnings.append(f"Line {line}: {message}" if line else message)

    def error(self, line: int | None, message: str) -> None:
        self.errors.append(LessonIssue(line, message))


# ─── small helpers ───────────────────────────────────────────


def slugify(text: str) -> str:
    return (
        re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", text.lower()))[:40] or "block"
    )


def make_id_factory():
    used: set[str] = set()

    def next_id(slug: str) -> str:
        n = 1
        while f"{slug}-{n}" in used:
            n += 1
        used.add(f"{slug}-{n}")
        return f"{slug}-{n}"

    return next_id


def word_count(text: str) -> int:
    return len(text.split())


def block_word_count(block: dict) -> int:
    kind = block["type"]
    if kind == "concept":
        return word_count(block.get("text", "")) + word_count(block.get("reveal") or "")
    if kind == "diagram":
        return word_count(block.get("caption") or "")
    if kind == "example":
        return (
            word_count(block.get("problem", ""))
            + sum(word_count(s) for s in block.get("steps", []))
            + word_count(block.get("answer", ""))
        )
    if kind == "tip":
        return word_count(block.get("text", ""))
    if kind == "mistake":
        return word_count(block.get("wrong", "")) + word_count(block.get("right", ""))
    if kind == "mnemonic":
        return word_count(block.get("phrase", "")) + sum(
            word_count(e) for e in block.get("encoded", [])
        )
    if kind == "short":
        return (
            word_count(block.get("question", ""))
            + word_count(block.get("answer", ""))
            + word_count(block.get("explanation") or "")
        )
    if kind == "check":
        return word_count(block.get("question", ""))
    return 0


def is_horizontal_rule(line: str) -> bool:
    return re.fullmatch(r"\s*(-{3,}|\*{3,}|_{3,})\s*", line) is not None


# ─── lint (mirrors lintLessonBlocks) ─────────────────────────


def lint_blocks(blocks: list[dict]) -> list[LessonIssue]:
    issues: list[LessonIssue] = []
    if not blocks:
        return [LessonIssue(None, "Lesson has no blocks.")]

    if blocks[0]["type"] == "check":
        issues.append(
            LessonIssue(
                None,
                f"{blocks[0]['id']}: A lesson cannot start with a knowledge check.",
            )
        )

    seen: set[str] = set()
    non_check_ids: set[str] = set()
    has_concept = has_check = False

    for block in blocks:
        bid = block["id"]
        if bid in seen:
            issues.append(LessonIssue(None, f'{bid}: Duplicate block id "{bid}".'))
        seen.add(bid)

        if block["type"] == "concept":
            has_concept = True

        if block["type"] != "check":
            non_check_ids.add(bid)
            words = block_word_count(block)
            if words > MAX_CARD_WORDS:
                issues.append(
                    LessonIssue(
                        None,
                        f'{bid}: "{bid}" is {words} words — cards must be ≤ {MAX_CARD_WORDS}. '
                        "Split it into two cards.",
                    )
                )
            continue

        has_check = True
        after = block.get("afterCard") or ""
        if not after:
            issues.append(
                LessonIssue(
                    None, f'{bid}: Check "{bid}" must reference the card it follows.'
                )
            )
        elif after not in non_check_ids:
            issues.append(
                LessonIssue(
                    None, f'{bid}: Check "{bid}" references unknown card "{after}".'
                )
            )
        answer = block.get("answer") or ""
        if not answer:
            issues.append(
                LessonIssue(None, f'{bid}: Check "{bid}" has no correct answer.')
            )
        elif answer not in block.get("options", {}):
            issues.append(
                LessonIssue(
                    None,
                    f'{bid}: Check "{bid}" answer "{answer}" is not one of its options.',
                )
            )

    if not has_concept:
        issues.append(LessonIssue(None, "A lesson needs at least one concept card."))
    if not has_check:
        issues.append(
            LessonIssue(None, "A lesson should include at least one knowledge check.")
        )
    return issues


# ─── frontmatter ─────────────────────────────────────────────


def _parse_frontmatter(lines: list[str], parsed: ParsedLesson) -> tuple[list[str], int]:
    """Fills ``parsed.meta``; returns ``(body_lines, body_offset)``."""
    if not lines or lines[0].strip() != "---":
        return lines, 0
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        parsed.error(1, "Frontmatter opened with --- but never closed.")
        return lines, 0

    for i in range(1, close):
        raw = lines[i]
        if not raw.strip():
            continue
        sep = raw.find(":")
        if sep == -1:
            parsed.error(
                i + 1, f'Frontmatter line "{raw.strip()}" is not "key: value".'
            )
            continue
        key, value = raw[:sep].strip(), raw[sep + 1 :].strip()
        if key in TEXT_KEYS:
            parsed.meta[key] = value
        elif key in NUMBER_KEYS:
            try:
                num = float(value)
            except ValueError:
                num = 0
            if not num > 0 or num != num or num == float("inf"):
                parsed.error(i + 1, f'{key} must be a positive number, got "{value}".')
                continue
            if key == "passMarkPercent" and num > 100:
                parsed.error(
                    i + 1, f'passMarkPercent must be at most 100, got "{value}".'
                )
                continue
            parsed.meta[key] = int(num + 0.5)
        elif key == "difficulty":
            if value not in DIFFICULTIES:
                parsed.error(
                    i + 1,
                    f'difficulty must be one of {", ".join(DIFFICULTIES)}, got "{value}".',
                )
                continue
            parsed.meta[key] = value
        else:
            parsed.warn(i + 1, f'Unknown frontmatter key "{key}" — ignored.')
    return lines[close + 1 :], close + 1


# ─── natural-format recognisers ──────────────────────────────

_PREFIX = re.compile(r"^(?:[A-Za-z][A-Za-z\s]*\s)?lesson\s+notes?\s*:\s*", re.I)
_INFO_INSIDE = re.compile(r"^\*\*([^*:]+):\*\*\s*(.*)$")
_INFO_OUTSIDE = re.compile(r"^\*\*([^*:]+)\*\*:\s*(.*)$")
_SHORT_ANSWER = re.compile(r"\*\((?:short answer|answer)\b[^:)]*:\s*(.+)\)\*", re.I)
_QUESTION = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_OPTION = re.compile(r"^\s*([A-Ha-h])(?:\)\s*|\.\s+)(.*)$")
_EXAMPLE_OPEN = re.compile(
    r"^\s*\*\*\s*Example\s*([\w.]+?)\s*:?\s*\*\*\s*:?\s*(.*)$", re.I
)
_SOLUTION_OPEN = re.compile(r"^\s*\*\*\s*Solutions?\s*:?\s*\*\*\s*:?\s*(.*)$", re.I)
_TRAILING_BOLD = re.compile(r"\*\*(.+?)\*\*\s*$")


def strip_lesson_note_prefix(title: str) -> str:
    return _PREFIX.sub("", title, count=1).strip() or title.strip()


def parse_info_line(line: str) -> dict[str, str] | None:
    trimmed = line.strip()
    if not trimmed.startswith("**"):
        return None
    segments = [s.strip() for s in trimmed.split("|") if s.strip()]
    if not segments:
        return None
    info: dict[str, str] = {}
    for segment in segments:
        match = _INFO_INSIDE.match(segment) or _INFO_OUTSIDE.match(segment)
        if match is None:
            return None
        key, value = match.group(1).strip(), match.group(2).strip()
        if not key or not value or value.startswith(":"):
            return None
        info[key] = value
    return info


def strip_answer_marker(text: str) -> tuple[str, bool]:
    """Removes a trailing correct-answer marker; returns ``(text, marked)``."""
    glyph = re.search(r"\s*[✔✓✅☑]\s*$", text)
    if glyph:
        return text[: glyph.start()].strip(), True
    star = re.search(r"\s*(\*{1,2})\s*$", text)
    if not star:
        return text.strip(), False
    run, rest = star.group(1), text[: star.start()]
    same_length_runs = re.findall(rf"(?<!\*)\*{{{len(run)}}}(?!\*)", rest)
    if len(same_length_runs) % 2 == 1:  # closes an earlier emphasis span — markup
        return text.strip(), False
    return rest.strip(), True


def _is_terminator(line: str) -> bool:
    return re.match(r"^#{1,2}\s", line) is not None or line.strip().startswith(":::")


def is_quiz_heading(title: str) -> bool:
    return re.match(r"^quiz\b", title.strip(), re.I) is not None


def is_worked_heading(title: str) -> bool:
    return re.match(r"^worked\s+examples?\b", title.strip(), re.I) is not None


def _heading_card(heading: str, next_id, lines: list[str]) -> dict | None:
    if not lines:
        return None
    return {
        "type": "concept",
        "id": next_id(slugify(heading)),
        "title": heading,
        "text": "\n".join(lines),
    }


def _section_body(lines: list[str]) -> list[str]:
    end = 0
    while end < len(lines) and not _is_terminator(lines[end]):
        end += 1
    return lines[:end]


def _parse_quiz(
    lines, start_line, heading, next_id, previous_id, parsed
) -> tuple[list[dict], int]:
    body = _section_body(lines)
    preamble: list[str] = []
    questions: list[dict] = []

    for i, raw in enumerate(body):
        if is_horizontal_rule(raw):
            continue
        q = _QUESTION.match(raw)
        if q:
            questions.append(
                {
                    "label": q.group(1),
                    "line": start_line + i,
                    "stem": [q.group(2).strip()],
                    "options": [],
                }
            )
            continue
        current = questions[-1] if questions else None
        opt = _OPTION.match(raw) if current else None
        if opt and current:
            current["options"].append(
                {"key": opt.group(1).upper(), "text": [opt.group(2).strip()]}
            )
            continue
        if not raw.strip():
            continue
        if not current:
            preamble.append(raw.strip())
        elif current["options"]:
            current["options"][-1]["text"].append(raw.strip())
        else:
            current["stem"].append(raw.strip())

    blocks: list[dict] = []
    rubric = _heading_card(heading, next_id, preamble)
    if rubric:
        blocks.append(rubric)
    last_non_check = blocks[-1]["id"] if blocks else previous_id

    for q in questions:
        stem = " ".join(q["stem"]).strip()
        label, line, options = q["label"], q["line"], q["options"]

        if not options:  # a theory question — never an error
            short = _SHORT_ANSWER.search(stem)
            if short:
                # The student types an answer, then marks it against this one.
                block = {
                    "type": "short",
                    "id": next_id("short-answer"),
                    "question": _SHORT_ANSWER.sub("", stem, count=1).strip(),
                    "answer": short.group(1).strip(),
                }
            else:  # no model answer to mark against, so it stays a plain card
                block = {"type": "concept", "id": next_id("theory"), "text": stem}
            blocks.append(block)
            last_non_check = block["id"]
            continue

        if len(options) < 2:
            parsed.error(
                line,
                f"Question {label} has only one option — a check needs at least two.",
            )
            continue

        keys = [o["key"] for o in options]
        dup = next((k for k in keys if keys.count(k) > 1), None)
        if dup:
            parsed.error(
                line,
                f"Question {label} repeats option {dup.lower()}) — each option needs its own letter.",
            )
            continue

        cleaned: dict[str, str] = {}
        marked: list[str] = []
        for o in options:
            text, is_answer = strip_answer_marker(" ".join(o["text"]).strip())
            cleaned[o["key"]] = text
            if is_answer:
                marked.append(o["key"])

        if not marked:
            parsed.error(
                line, f"Question {label} has no correct option — mark it with ✔."
            )
            continue
        if len(marked) > 1:
            parsed.error(
                line,
                f"Question {label} marks {len(marked)} correct options — a check needs exactly one.",
            )
            continue
        if not last_non_check:
            parsed.error(
                line,
                f"Question {label} cannot be the lesson's first block — put the quiz after a card.",
            )
            continue

        aside = _SHORT_ANSWER.search(stem)
        blocks.append(
            {
                "type": "check",
                "id": next_id("check"),
                "question": _SHORT_ANSWER.sub("", stem, count=1).strip()
                if aside
                else stem,
                "options": cleaned,
                "answer": marked[0],
                "explanation": aside.group(1).strip() if aside else "",
                "afterCard": last_non_check,
            }
        )
    return blocks, len(body)


def _parse_worked(
    lines, start_line, heading, next_id, parsed
) -> tuple[list[dict], int]:
    body = _section_body(lines)
    preamble: list[str] = []
    examples: list[dict] = []

    for i, raw in enumerate(body):
        if is_horizontal_rule(raw) or not raw.strip():
            continue
        opened = _EXAMPLE_OPEN.match(raw)
        if opened:
            examples.append(
                {
                    "label": opened.group(1),
                    "line": start_line + i,
                    "problem": opened.group(2).strip(),
                    "working": [],
                    "has_solution": False,
                }
            )
            continue
        current = examples[-1] if examples else None
        if not current:
            preamble.append(raw.strip())
            continue
        solution = _SOLUTION_OPEN.match(raw)
        if solution:
            current["has_solution"] = True
            if solution.group(1).strip():
                current["working"].append(solution.group(1).strip())
            continue
        if current["has_solution"]:
            current["working"].append(raw.strip())
        else:
            current["problem"] = f"{current['problem']} {raw.strip()}".strip()

    blocks: list[dict] = []
    rubric = _heading_card(heading, next_id, preamble)
    if rubric:
        blocks.append(rubric)

    for ex in examples:
        if not ex["has_solution"]:
            parsed.error(
                ex["line"],
                f"Example {ex['label']} has no **Solution:** — an example needs an answer.",
            )
            continue
        if not ex["working"]:
            parsed.error(
                ex["line"],
                f"Example {ex['label']} has an empty **Solution:** — an example needs an answer.",
            )
            continue
        steps, last = ex["working"][:-1], ex["working"][-1]
        bold = _TRAILING_BOLD.search(last)
        blocks.append(
            {
                "type": "example",
                "id": next_id("example"),
                "problem": ex["problem"],
                "steps": steps,
                "answer": (bold.group(1) if bold else last).strip(),
                "mode": "worked",
            }
        )
    return blocks, len(body)


# ─── fences ──────────────────────────────────────────────────

_SINGLE_LABELS = {
    "problem", "answer", "mode", "title", "exam", "wrong", "right",
    "phrase", "q", "correct", "why", "after", "caption",
}  # fmt: skip
_SCALAR_LABELS = {"exam", "mode", "correct", "after"}


def _read_fence(body: list[str], open_line: int, parsed: ParsedLesson) -> dict:
    f = {
        "singles": {},
        "steps": [],
        "encoded": [],
        "hotspots": [],
        "options": {},
        "prose": [],
        "raw": body,
    }
    last = ("prose", "")

    for i, line in enumerate(body):
        line_no = open_line + i + 1
        option = re.match(r"^([A-H])\)\s?(.*)$", line)
        if option:
            letter = option.group(1)
            if letter in f["options"]:
                parsed.error(line_no, f"Option {letter}) appears twice in this check.")
            f["options"][letter] = option.group(2).strip()
            last = ("option", letter)
            continue

        labelled = re.match(r"^([A-Za-z]+):\s?(.*)$", line)
        if labelled:
            key, value = labelled.group(1).lower(), labelled.group(2).strip()
            if key in ("step", "encoded", "hotspot"):
                bucket = {"step": "steps", "encoded": "encoded", "hotspot": "hotspots"}[
                    key
                ]
                f[bucket].append(value)
                last = (key, "")
                continue
            if key in _SINGLE_LABELS:
                if key in f["singles"]:
                    parsed.error(
                        line_no,
                        f'"{labelled.group(1)}" appears more than once in this block.',
                    )
                    continue
                f["singles"][key] = value
                last = ("prose", "") if key in _SCALAR_LABELS else ("single", key)
                continue

        if not line.strip():
            continue
        if line.strip().startswith("<"):  # markup never continues a labelled field
            f["prose"].append(line)
            last = ("prose", "")
            continue

        kind, key = last
        text = line.strip()
        if kind == "single":
            f["singles"][key] += f"\n{text}"
        elif kind == "step":
            f["steps"][-1] += f"\n{text}"
        elif kind == "encoded":
            f["encoded"][-1] += f"\n{text}"
        elif kind == "hotspot":
            f["hotspots"][-1] += f"\n{text}"
        elif kind == "option":
            f["options"][key] += f"\n{text}"
        else:
            f["prose"].append(text)
    return f


def _parse_hotspot(raw: str, index: int) -> dict:
    head, *rest = re.split(r"\s+—\s+|\s+--\s+", raw)
    coords = re.search(r"@\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", head)
    label = re.sub(r"@\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?", "", head).strip()
    spot = {
        "id": f"{slugify(label or 'hotspot')}-{index + 1}",
        "label": label or "Part",
        "text": " — ".join(rest).strip(),
    }
    if coords:
        spot["x"], spot["y"] = float(coords.group(1)), float(coords.group(2))
    return spot


def _build_fence_block(
    kind, f, block_id, open_line, previous_id, parsed
) -> dict | None:
    get = lambda key: f["singles"].get(key, "")  # noqa: E731

    if kind == "example":
        mode = get("mode").lower()
        block = {
            "type": "example",
            "id": block_id,
            "problem": get("problem"),
            "steps": f["steps"],
            "answer": get("answer"),
            "mode": mode if mode in ("partial", "solo") else "worked",
        }
        if get("title"):
            block["title"] = get("title")
        if not block["problem"]:
            parsed.error(open_line, "An example needs a Problem: line.")
            return None
        if not block["answer"]:
            parsed.error(open_line, "An example needs an Answer: line.")
            return None
        return block

    if kind == "tip":
        text = "\n".join(f["prose"]).strip()
        if not text:
            parsed.error(open_line, "A tip fence has no text.")
            return None
        block = {"type": "tip", "id": block_id, "text": text}
        exam = get("exam").upper()
        if exam in EXAM_TYPES:
            block["examType"] = exam
        elif exam:
            parsed.warn(
                open_line,
                f'Exam tag "{exam}" is not one of {", ".join(EXAM_TYPES)} — dropped.',
            )
        return block

    if kind == "mistake":
        if not get("wrong") or not get("right"):
            parsed.error(
                open_line, "A mistake fence needs both Wrong: and Right: lines."
            )
            return None
        return {
            "type": "mistake",
            "id": block_id,
            "wrong": get("wrong"),
            "right": get("right"),
        }

    if kind == "mnemonic":
        if not get("phrase"):
            parsed.error(open_line, "A mnemonic fence needs a Phrase: line.")
            return None
        return {
            "type": "mnemonic",
            "id": block_id,
            "phrase": get("phrase"),
            "encoded": f["encoded"],
        }

    if kind == "check":
        question, options = get("q"), dict(f["options"])
        answer = get("correct").strip().upper()
        if not question:
            parsed.error(open_line, "A check needs a Q: line.")
            return None
        if len(options) < 2:
            parsed.error(open_line, "A check needs at least two options.")
            return None
        if answer not in options:
            parsed.error(
                open_line, f'Correct: "{answer}" is not one of this check\'s options.'
            )
            return None
        after = get("after") or previous_id or ""
        if not after:
            parsed.error(
                open_line, "A check cannot be the first block — put it after a card."
            )
            return None
        return {
            "type": "check",
            "id": block_id,
            "question": question,
            "options": options,
            "answer": answer,
            "explanation": get("why"),
            "afterCard": after,
        }

    if kind == "diagram":
        raw_svg = "\n".join(
            line for line in f["raw"] if not re.match(r"^([A-Za-z]+):", line.strip())
        ).strip()
        svg, svg_warnings = sanitize_svg(raw_svg)
        if not svg:
            parsed.error(open_line, "A diagram fence needs an inline <svg> element.")
            return None
        for message in svg_warnings:
            parsed.warn(open_line, message)
        block = {
            "type": "diagram",
            "id": block_id,
            "svg": svg,
            "hotspots": [_parse_hotspot(h, i) for i, h in enumerate(f["hotspots"])],
        }
        if get("title"):
            block["title"] = get("title")
        if get("caption"):
            block["caption"] = get("caption")
        return block
    return None


# ─── concept cards ───────────────────────────────────────────


def _emit_concept(
    section: dict, next_id, blocks: list[dict], parsed: ParsedLesson
) -> None:
    """One concept card, or several split at paragraph boundaries past the cap."""
    title = section.get("title")
    slug = slugify(title or "concept")
    whole = {"type": "concept", "id": next_id(slug), "text": section["text"]}
    if title:
        whole["title"] = title
    if section.get("reveal"):
        whole["reveal"] = section["reveal"]

    if block_word_count(whole) <= MAX_CARD_WORDS:
        blocks.append(whole)
        return

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section["text"]) if p.strip()]
    if len(paragraphs) < 2:  # nothing to split on — the lint rejects it, honestly
        blocks.append(whole)
        return

    cards: list[str] = []
    current: list[str] = []
    running = 0
    for paragraph in paragraphs:
        words = word_count(paragraph)
        if current and running + words > MAX_CARD_WORDS:
            cards.append("\n\n".join(current))
            current, running = [], 0
        current.append(paragraph)
        running += words
    if current:
        cards.append("\n\n".join(current))

    emitted: list[dict] = []
    for index, text in enumerate(cards):
        card = {
            "type": "concept",
            "id": whole["id"] if index == 0 else next_id(slug),
            "text": text,
        }
        if index == 0 and title:
            card["title"] = title
        if index == len(cards) - 1 and section.get("reveal"):
            card["reveal"] = section["reveal"]
        emitted.append(card)
    blocks.extend(emitted)

    still_over = any(block_word_count(b) > MAX_CARD_WORDS for b in emitted)
    parsed.warn(
        section["line"],
        f'"{title or "Untitled section"}" is longer than {MAX_CARD_WORDS} words and was split '
        f"into {len(cards)} cards"
        + (" — one is still over the limit, see the errors." if still_over else "."),
    )


# ─── the scanner ─────────────────────────────────────────────


def parse_lesson_markdown(source: str) -> ParsedLesson:
    parsed = ParsedLesson(title="")
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    body, offset = _parse_frontmatter(lines, parsed)

    next_id = make_id_factory()
    blocks = parsed.blocks
    section: dict | None = None
    buffer: list[str] = []
    in_reveal = False
    expect_info = False

    def previous_non_check() -> str | None:
        return next((b["id"] for b in reversed(blocks) if b["type"] != "check"), None)

    def flush() -> None:
        nonlocal section, buffer, in_reveal
        if section is None:
            buffer, in_reveal = [], False
            return
        text = "\n".join(buffer).strip()
        if in_reveal:
            section["reveal"] = text
        else:
            section["text"] = text
        buffer = []
        if section.get("text"):
            _emit_concept(section, next_id, blocks, parsed)
        section, in_reveal = None, False

    i = 0
    while i < len(body):
        line = body[i]
        line_no = offset + i + 1

        h1 = re.match(r"^#\s+(.*)$", line)
        if h1:
            flush()
            if not parsed.meta.get("title"):
                parsed.meta["title"] = strip_lesson_note_prefix(h1.group(1).strip())
            expect_info = True
            i += 1
            continue

        if expect_info:
            if not line.strip():
                i += 1
                continue
            info = parse_info_line(line)
            expect_info = False
            if info:
                parsed.doc_info.update(info)
                i += 1
                continue

        h2 = re.match(r"^##\s+(.*)$", line)
        if h2:
            flush()
            expect_info = False
            title = h2.group(1).strip()
            rest = body[i + 1 :]
            if is_quiz_heading(title):
                new, consumed = _parse_quiz(
                    rest, line_no + 1, title, next_id, previous_non_check(), parsed
                )
                blocks.extend(new)
                i += consumed + 1
                continue
            if is_worked_heading(title):
                new, consumed = _parse_worked(rest, line_no + 1, title, next_id, parsed)
                blocks.extend(new)
                i += consumed + 1
                continue
            section = {"title": title, "text": "", "line": line_no}
            i += 1
            continue

        h3 = re.match(r"^###\s+(.*)$", line)
        if h3:
            if section is not None and h3.group(1).strip().lower() == "reveal":
                section["text"] = "\n".join(buffer).strip()
                buffer, in_reveal = [], True
            else:
                buffer.append(line)
            i += 1
            continue

        fence = re.match(r"^:::\s*([a-z]+)\s*$", line.strip(), re.I)
        if fence:
            flush()
            kind = fence.group(1).lower()
            close = next(
                (j for j in range(i + 1, len(body)) if body[j].strip() == ":::"), None
            )
            if close is None:
                parsed.error(line_no, f'Fence ":::{kind}" was never closed.')
                break
            fence_body = body[i + 1 : close]
            i = close + 1
            if kind not in FENCE_TYPES:
                parsed.error(line_no, f'Unknown fence type ":::{kind}".')
                continue
            fields = _read_fence(fence_body, line_no, parsed)
            block = _build_fence_block(
                kind, fields, next_id(kind), line_no, previous_non_check(), parsed
            )
            if block:
                blocks.append(block)
            continue

        if section is None and line.strip():
            section = {
                "title": None,
                "text": "",
                "line": line_no,
            }  # prose before any heading
        if not is_horizontal_rule(line):
            buffer.append(line)
        i += 1
    flush()

    parsed.title = parsed.meta.get("title", "")
    if parsed.doc_info:
        parsed.meta["docInfo"] = dict(parsed.doc_info)
    if not blocks and not parsed.errors:
        parsed.error(None, "This file has no lesson content.")
    return parsed


def validate_lesson_markdown(source: str) -> ParsedLesson:
    """Parse, then run the authoring lint — what the import route calls."""
    parsed = parse_lesson_markdown(source)
    if parsed.blocks:
        parsed.errors.extend(lint_blocks(parsed.blocks))
    return parsed
