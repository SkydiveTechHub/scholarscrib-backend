import hashlib
import re

DRAW_LIMIT = 50
MIN_NEW_PER_DRAW = 10
MAX_DRAWS = 12
LEASE_WINDOW_MS = 120_000
MAPPER_VERSION = 3
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

# Our Subject.slug -> SDash's subject key, where they differ. Anything absent
# is sent as-is (mathematics, physics, biology... share a name). Documented at
# https://sdashapi.com/docs under GET /v1/subjects.
SDASH_SUBJECT_SLUGS = {
    "english-language": "english",
    "literature-in-english": "englishlit",
    "literature": "englishlit",
    "christian-religious-studies": "crk",
    "christian-religious-knowledge": "crk",
    "crs": "crk",
    "islamic-studies": "irk",
    "islamic-religious-knowledge": "irk",
    "irs": "irk",
    "civic-education": "civiledu",
    "computer-studies": "computer",
    "agricultural-science": "agriculture",
    "financial-accounting": "accounting",
    "fine-art": "fineart",
    "home-economics": "homeeconomics",
    "general-science": "gscience",
    "current-affairs": "currentaffairs",
}

# Our exam keys -> SDash's ``type`` values (neco is the same on both sides).
SDASH_EXAM_TYPES = {"jamb": "utme", "waec": "wassce", "neco": "neco"}


def sdash_subject(slug: str) -> str:
    """The key SDash knows one of our subjects by."""
    slug = slug.strip().lower()
    return SDASH_SUBJECT_SLUGS.get(slug, slug)


def sdash_exam_type(exam: str) -> str:
    exam = exam.strip().lower()
    return SDASH_EXAM_TYPES.get(exam, exam)


def our_slugs_for_sdash(sdash_key: str) -> set[str]:
    """Every slug of ours that resolves to one SDash subject key."""
    key = sdash_key.strip().lower()
    return {key} | {
        ours for ours, theirs in SDASH_SUBJECT_SLUGS.items() if theirs == key
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
