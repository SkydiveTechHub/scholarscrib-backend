"""Map one ALOC question item onto our bank: exam, year, difficulty and topic.

Pure, so the rules that decide what a stored question claims about itself are
testable without a database. The raw item is always kept on ProviderQuestion,
so anything this module declines to map today can be re-mapped later from the
stored payload once the rules (or the topic table) improve.
"""

import re
from dataclasses import dataclass, field

from app.services.provider.base import NormalizedQuestion
from app.services.provider.rules import objective_ok

# The exam boards we record. Post-UTME is deliberately absent: our ExamType
# has no value for it, and storing it as CUSTOM would corrupt past-paper
# grouping.
ALOC_EXAMS = {
    "jamb": "JAMB",
    "utme": "JAMB",
    "waec": "WAEC",
    "wassce": "WAEC",
    "neco": "NECO",
}

# ALOC subject key -> our Subject.slug, where they differ and the pairing is
# known (mirrors SUBJECT_ALIASES in rules.py). Anything else resolves through
# the key itself, then ALOC's own display name and aliases for the subject.
PROVIDER_SUBJECT_SLUGS = {
    "english": "english-language",
}

# ALOC classifies every question with a topic, a subtopic and a confidence.
# Below this confidence, or when ALOC flags the classification for review,
# the question gets no topic: crediting mastery to the wrong topic is worse
# than crediting none.
TOPIC_CONFIDENCE_FLOOR = 0.7

# Reviewed ALOC topic -> our Topic.slug, per our Subject.slug. Keys are either
# "aloc-topic/aloc-subtopic" (most specific) or "aloc-topic" (covers every
# subtopic without its own entry or an identical slug of ours). Our curriculum
# (SS1-SS3 classroom topics) and ALOC's taxonomy are different vocabularies,
# so entries here are deliberate decisions, never guesses.
#
# Drafted on 2026-10-06 from ALOC's live GET /subjects/{subject}/topics.
# Deliberately left unmapped:
# - broad buckets no single topic of ours covers (physics "mechanics",
#   economics "microeconomics", CRS "new-testament"/"old-testament",
#   geography "physical-geography"/"human-geography", English "vocabulary");
# - splits that straddle two of our topics (maths "mensuration" area vs
#   volume, "inequalities" SS1 vs SS3, accounting "not-for-profit");
# - pre-SS content we have no topic for (maths "number-theory" fractions,
#   HCF/LCM, percentages);
# - off-subject classifications (e.g. "chemistry" questions under physics);
# - Insurance: ALOC files all of it under one "insurance" subtopic.
# Identical slugs (e.g. maths "sets", chemistry "electrolysis") need no entry.
TOPIC_MAP: dict[str, dict[str, str]] = {
    "mathematics": {
        "trigonometry/sine-cosine-tangent": "trigonometric-ratios",
        "statistics/mean-median-mode": "statistics-mean-median-mode",
        "statistics/median": "statistics-mean-median-mode",
        "statistics/mode": "statistics-mean-median-mode",
        "statistics/range": "statistics-mean-median-mode",
        "statistics/pie-charts": "statistics-mean-median-mode",
        "statistics/frequency-distribution": (
            "statistics-grouped-data-and-standard-deviation"
        ),
        "statistics/histograms": "statistics-grouped-data-and-standard-deviation",
        "statistics/percentiles": "statistics-grouped-data-and-standard-deviation",
        "statistics/variance": "statistics-grouped-data-and-standard-deviation",
        "statistics/standard-deviation": (
            "statistics-grouped-data-and-standard-deviation"
        ),
        "statistics/variance-standard-deviation": (
            "statistics-grouped-data-and-standard-deviation"
        ),
        "geometry/circles": "circle-theorems",
        "geometry/cyclic-quadrilaterals": "circle-theorems",
        "geometry/bearings-elevations": "bearings-and-distances",
        "geometry/polygons": "plane-geometry-angles-and-polygons",
        "geometry/angles": "plane-geometry-angles-and-polygons",
        "geometry/triangles": "plane-geometry-angles-and-polygons",
        "geometry/lines": "plane-geometry-angles-and-polygons",
        "geometry/coordinate-geometry": "coordinate-geometry-straight-lines",
        "geometry/loci": "construction-and-loci",
        "calculus/limits": "introduction-to-calculus-differentiation",
        "calculus/differentiation": "introduction-to-calculus-differentiation",
        "calculus/maxima-minima": "application-of-differentiation",
        "calculus/rates-of-change": "application-of-differentiation",
        "algebra/variations": "variation-direct-inverse-joint-partial",
        "algebra/inverse-proportions": "variation-direct-inverse-joint-partial",
        "algebra/simultaneous-equations": "simultaneous-linear-and-quadratic-equations",
        "algebra/indices-logarithms": "indices-and-logarithms",
        "algebra/equations": "simple-equations-and-inequalities",
        "algebra/linear-equations": "simple-equations-and-inequalities",
        "algebra/matrices": "matrices-and-determinants",
        "algebra/polynomials": "polynomial-functions",
        "algebra/simplifying-expressions": "algebraic-expressions-factorization",
        "indices-logarithms": "indices-and-logarithms",
        "matrices": "matrices-and-determinants",
        "polynomials": "polynomial-functions",
        "sequences-series": "arithmetic-and-geometric-progressions",
    },
    "physics": {
        "electricity/current-voltage-resistance": "current-electricity-ohms-law",
        "electricity/circuits": "electrical-circuits-series-and-parallel",
        "electricity/electrostatics": "electrostatics-electric-charges-and-fields",
        "electricity/electric-field": "electrostatics-electric-charges-and-fields",
        "electricity/capacitors": "capacitors-and-capacitance",
        "electricity/transformers": "electromagnetic-induction",
        "modern-physics/nuclear-physics": "nuclear-reactions-fission-and-fusion",
        "modern-physics/radioactivity": "atomic-structure-and-radioactivity",
        "waves/sound": "sound-waves",
        "waves/wave-properties": "properties-of-waves",
        "waves/light-reflection": "light-reflection-at-plane-and-curved-surfaces",
        "measurement": "measurement-and-units",
        "heat/thermal-expansion": "linear-and-volume-expansivity",
        "heat/heat-transfer": "heat-energy-and-heat-transfer",
        "heat/temperature": "temperature-and-thermometers",
        "heat/heat-capacity": "specific-heat-capacity-and-latent-heat",
        "heat/calorimetry": "specific-heat-capacity-and-latent-heat",
        "heat/latent-heat": "specific-heat-capacity-and-latent-heat",
        "heat/gas-laws-thermal": "gas-laws",
        "gas-laws-thermal": "gas-laws",
        "magnetism": "magnets-and-magnetic-fields",
        "work-energy-power": "work-energy-and-power",
        "atomic-structure": "atomic-structure-and-radioactivity",
    },
    "chemistry": {
        "states-of-matter": "particulate-nature-of-matter",
        "stoichiometry": "chemical-reactions-and-equations",
        "inorganic-chemistry/oxidation-reduction": "oxidation-and-reduction",
        "inorganic-chemistry/acids-bases-salts": "acids-bases-and-salts",
        "atomic-structure/electron-configuration": "atomic-structure",
        "separation-and-purification": "separation-techniques",
    },
    "biology": {
        "genetics": "genetics-and-heredity",
        "microbiology": "microorganisms-and-disease",
        "disease-transmission": "microorganisms-and-disease",
        "cell-biology": "cell-structure-and-organization",
        "classification-of-plants": "classification-of-living-things",
        "human-biology/circulatory-system": "transport-system-in-animals",
    },
    "english-language": {
        "oral-english/stress-intonation": "speech-work-and-stress-patterns",
        "oral-english/vowel-sounds": "oral-english-vowel-sounds",
        "oral-english/consonant-sounds": "oral-english-consonant-sounds",
        "grammar/sentence-structure": "clauses-and-sentence-structure",
        "grammar/tenses-aspect": "tenses-and-aspects",
        "grammar": "grammar-and-usage",
        "comprehension/reading-comprehension": "comprehension",
        "vocabulary/idioms-phrasal-verbs": "idioms-and-figures-of-speech",
    },
    "economics": {
        "financial-economics/banking": "money-and-banking",
        "microeconomics/market-structures": "market-structures",
        "microeconomics/price-determination": "demand-and-supply",
        "development-economics/industrialisation": "agriculture-and-industry",
        "development-economics": "economic-development",
    },
    "government": {
        "african-politics/ecowas": "economic-communities",
        "african-politics/regional-integration": "economic-communities",
        "international-relations": "international-relations",
        "nigerian-government/nigerian-constitution": (
            "constitution-and-constitutionalism"
        ),
        "nigerian-government/federal-system": "federalism",
        "nigerian-government": "nigerian-government-and-politics",
        "political-concepts": "basic-concepts-of-government",
    },
    "financial-accounting": {},
    "christian-religious-studies": {},
    "civic-education": {
        "democracy": "democracy-and-national-development",
        "government/nigerian-government": "government-and-governance",
        "international-relations/un": "international-organizations",
    },
    "commerce": {
        "financial-economics/banking": "banking-and-finance",
        "african-politics/ecowas": "economic-integration",
        "international-relations/regional-integration": "economic-integration",
        "international-relations/ecowas": "economic-integration",
        "international-trade/regional-integration": "economic-integration",
        "accounting": "accounting-basics",
        "financial-accounting": "accounting-basics",
    },
    "geography": {
        "map-work": "map-reading",
        "nigeria-geography": "nigeria-physical-and-human-geography",
    },
    "history": {
        "african-politics/decolonisation": "independence-movements",
    },
    "literature-in-english": {
        "prose": "the-prose",
    },
}


@dataclass
class MappedItem:
    exam_type: str
    exam_year: int
    difficulty: str
    time_estimate_seconds: int
    question_number: int | None
    topic_slug: str | None
    rejection_reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejection_reasons


def exam_type_for(key: str | None) -> str | None:
    return ALOC_EXAMS.get(str(key or "").strip().lower())


def slugify(text: str) -> str:
    """Same rule our Subject.slug was seeded with (scripts/seed_catalogue.py)."""
    text = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.ASCII)
    return re.sub(r"[\s_]+", "-", text).strip("-")


def subject_slug_candidates(key: str, discovered: dict | None = None) -> list[str]:
    """Our slugs an ALOC subject may correspond to, most likely first.

    `discovered` is ALOC's own record for the subject (its display name and
    aliases), which is what makes keys like "english" resolve without a
    hand-kept table.
    """
    raw = key.strip().lower()
    names: list[str] = [PROVIDER_SUBJECT_SLUGS.get(raw, ""), raw]
    if isinstance(discovered, dict):
        names.append(str(discovered.get("displayName") or ""))
        aliases = discovered.get("aliases")
        if isinstance(aliases, list):
            names.extend(str(alias) for alias in aliases)
    seen: list[str] = []
    for name in names:
        slug = slugify(name) if name else ""
        if slug and slug not in seen:
            seen.append(slug)
    return seen


def difficulty_from_score(score: object) -> str:
    """ALOC's difficultyScore onto our three bands; unknown is the middle.

    ALOC scores 1-3 (the only values seen on live papers, 2026-10-06), which
    lines up one-to-one with BASIC / INTERMEDIATE / ADVANCED. Anything above 3
    is treated as hardest rather than guessed at.
    """
    if isinstance(score, bool) or not isinstance(score, int | float):
        return "INTERMEDIATE"
    if score <= 1:
        return "BASIC"
    if score >= 3:
        return "ADVANCED"
    return "INTERMEDIATE"


def map_topic(
    subject_slug: str, metadata: dict | None, topic_slugs: set[str]
) -> str | None:
    """Our topic slug for this question, or None when it cannot be trusted."""
    if not isinstance(metadata, dict) or metadata.get("needsReview"):
        return None
    confidence = metadata.get("classificationConfidence")
    if not isinstance(confidence, int | float) or confidence < TOPIC_CONFIDENCE_FLOOR:
        return None
    topic = str(metadata.get("topic") or "").strip().lower()
    subtopic = str(metadata.get("subtopic") or "").strip().lower()
    if not topic:
        return None
    table = TOPIC_MAP.get(subject_slug, {})
    # Most specific first: a subtopic (reviewed, or with a slug identical to
    # one of ours) beats a topic-level entry, so a broad topic's mapping never
    # swallows a precise subtopic match inside it.
    candidates = [
        table.get(f"{topic}/{subtopic}") if subtopic else None,
        subtopic if subtopic in topic_slugs else None,
        table.get(topic),
        topic if topic in topic_slugs else None,
    ]
    for slug in candidates:
        if slug and slug in topic_slugs:
            return slug
    return None


def provider_topic_filters(
    subject_slug: str, topic_slug: str
) -> list[tuple[str, str | None]]:
    """The ALOC (topic, subtopic) pairs that map onto one of our topics.

    The reverse of TOPIC_MAP. Empty when ALOC has no reviewed counterpart, so
    the caller never asks ALOC for an unrelated topic.
    """
    filters: list[tuple[str, str | None]] = []
    for key, slug in TOPIC_MAP.get(subject_slug, {}).items():
        if slug != topic_slug:
            continue
        topic, _, subtopic = key.partition("/")
        filters.append((topic, subtopic or None))
    return filters


def map_item(
    item: dict,
    normalized: NormalizedQuestion,
    *,
    subject_slug: str,
    expected_exam: str,
    expected_year: int,
    topic_slugs: set[str],
) -> MappedItem:
    """Everything we store about the question beyond its text and options.

    A row whose own exam or year disagrees with the paper it was drawn for is
    rejected rather than rewritten: storing it under the requested year would
    falsify its provenance, and under its own year would silently short the
    paper the student asked for.
    """
    reasons: list[str] = []
    if not normalized.text:
        reasons.append("empty question text")
    if not objective_ok(normalized.options, normalized.answer):
        reasons.append("options or answer incomplete")
    exam_type = exam_type_for(item.get("examType")) or expected_exam
    if exam_type != expected_exam:
        reasons.append(f"drawn for {expected_exam} but the item says {exam_type}")
    year = item.get("year")
    exam_year = year if isinstance(year, int) and not isinstance(year, bool) else None
    if exam_year is None:
        exam_year = expected_year
    elif exam_year != expected_year:
        reasons.append(f"drawn for {expected_year} but the item says {exam_year}")
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    estimate = metadata.get("estimatedTime")
    number = item.get("questionNumber")
    return MappedItem(
        exam_type=exam_type,
        exam_year=exam_year,
        difficulty=difficulty_from_score(metadata.get("difficultyScore")),
        time_estimate_seconds=int(estimate)
        if isinstance(estimate, int | float) and 0 < estimate <= 3600
        else 60,
        question_number=number
        if isinstance(number, int) and not isinstance(number, bool)
        else None,
        topic_slug=map_topic(subject_slug, metadata, topic_slugs),
        rejection_reasons=reasons,
    )
