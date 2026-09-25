import hashlib
import re

DRAW_LIMIT = 50
MIN_NEW_PER_DRAW = 10
MAX_DRAWS = 12
LEASE_WINDOW_MS = 120_000
MAPPER_VERSION = 2
COOLDOWN_MINUTES = 15

EXAM_ALIASES = {
    "utme": "JAMB",
    "jamb": "JAMB",
    "wassce": "WAEC",
    "waec": "WAEC",
    "neco": "NECO",
}
SUBJECT_ALIASES = {
    "english-language": "english",
    "english": "english",
}


def cache_key(subject_slug: str, exam_type: str, exam_year: int) -> str:
    slug = subject_slug.strip().lower().replace("|", "%7C")
    return f"{slug}|{exam_type}|{exam_year}"


def should_saturate(draw_count: int, returned_count: int, new_in_draw: int) -> bool:
    if returned_count < DRAW_LIMIT:
        return True
    if new_in_draw < MIN_NEW_PER_DRAW:
        return True
    return draw_count >= MAX_DRAWS


def next_circuit(status_code: int, body: str, current: str) -> str:
    if status_code == 404:
        return current
    if status_code == 402:
        return "EXHAUSTED"
    if status_code == 401:
        return "BLOCKED"
    if status_code == 403:
        if "credit" in body.lower():
            return "EXHAUSTED"
        return "BLOCKED"
    return current


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def fingerprint(question_text: str, options: dict[str, str]) -> str:
    choices = "|".join(sorted(_normalise(value) for value in options.values()))
    payload = f"{_normalise(question_text)}|{choices}"
    return hashlib.sha256(payload.encode()).hexdigest()


def objective_ok(options: dict | None, correct_answer: str | None) -> bool:
    if not options or not correct_answer:
        return False
    if len(options) < 4:
        return False
    return correct_answer.strip().upper() in {str(key).upper() for key in options}
