"""Seed subjects and the SS1–SS3 curriculum.

Topics come from `curriculum2.json` (Lagos State Unified Schemes of Work).
Each subject there is keyed by display name and lists topic titles only, so
where a title also exists in the legacy `curriculum.json`, we carry over its
exam metadata (estimated minutes and WAEC/JAMB weights); anything new falls
back to the model defaults.

The seed is idempotent: it upserts. Records are matched on their natural keys
(subject code, subject + class level + term, subject + topic slug); fields that
changed are updated, missing records are created, and untouched ones are left
alone.

Run from the repository root:

    python -m scripts.seed_catalogue
"""

import asyncio
import json
import re
from pathlib import Path

from app.core.ids import cuid
from app.database.db import get_session
from app.database.models import CurriculumLevel, Subject, Topic
from app.database.repositories.curriculum import (
    curriculum_levels_repository,
    subjects_repository,
    topics_repository,
)

HERE = Path(__file__).resolve().parent

# curriculum2.json term keys -> our Term enum values.
TERMS = {
    "firstTerm": "FIRST",
    "secondTerm": "SECOND",
    "thirdTerm": "THIRD",
}

# curriculum2.json is keyed by display name; these differ from subject.json.
SUBJECT_NAME_ALIASES = {
    "General Mathematics": "MTH",
    "Computer Studies (ICT)": "CMP",
    "Literature-in-English": "LIT",
    "Islamic Religious Studies": "IRS",
    "Food and Nutrition": "FNT",
    "Visual Art (Fine Art)": "FNA",
    "Physical and Health Education": "HED",
    "Basic Electricity (Electronics)": "BEL",
}

DEFAULT_ESTIMATED_MINUTES = 45
DEFAULT_WAEC_WEIGHT = 0.0
DEFAULT_JAMB_WEIGHT = 0.0


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.ASCII)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")


def load_seed() -> tuple[list[dict], dict[str, dict], dict[tuple[str, str], dict]]:
    subjects = json.loads((HERE / "subject.json").read_text())
    curriculum = json.loads((HERE / "curriculum2.json").read_text())["subjects"]
    metadata = load_topic_metadata()
    return subjects, curriculum, metadata


def load_topic_metadata() -> dict[tuple[str, str], dict]:
    """Legacy topic metadata keyed by (subject code, topic slug)."""
    legacy = json.loads((HERE / "curriculum.json").read_text())
    metadata: dict[tuple[str, str], dict] = {}
    for code, terms in legacy.items():
        for term_data in terms:
            for topic in term_data["topics"]:
                metadata[(code, slugify(topic["title"]))] = topic
    return metadata


def resolve_code(name: str, name_to_code: dict[str, str]) -> str:
    code = SUBJECT_NAME_ALIASES.get(name) or name_to_code.get(name)
    if code is None:
        raise RuntimeError(f"Unknown subject in curriculum2.json: {name}")
    return code


def apply_changes(obj, values: dict) -> bool:
    """Assign changed fields only; return True if anything differed."""
    changed = False
    for attr, value in values.items():
        if getattr(obj, attr) != value:
            setattr(obj, attr, value)
            changed = True
    return changed


def topic_values(
    level_id: str,
    title: str,
    order: int,
    meta: dict | None,
) -> dict:
    return {
        "curriculum_level_id": level_id,
        "title": title,
        "order_index": order,
        "estimated_minutes": (
            meta["estimatedMinutes"] if meta else DEFAULT_ESTIMATED_MINUTES
        ),
        "waec_weight": meta["waecWeight"] if meta else DEFAULT_WAEC_WEIGHT,
        "jamb_weight": meta["jambWeight"] if meta else DEFAULT_JAMB_WEIGHT,
    }


async def seed_subjects(session, subjects: list[dict]) -> None:
    created = updated = 0
    for row in subjects:
        values = {
            "name": row["name"],
            "slug": slugify(row["name"]),
            "track_category": row["trackCategory"],
            "is_waec": row["isWaec"],
            "is_jamb": row["isJamb"],
            "is_neco": row["isNeco"],
        }
        existing = await subjects_repository.by_code(session, row["code"])
        if existing is None:
            await subjects_repository.add(
                session,
                Subject(id=cuid(), code=row["code"], **values),
                flush=True,
            )
            created += 1
        elif apply_changes(existing, values):
            updated += 1
    print(f"  subjects: {created} created, {updated} updated")


async def seed_curriculum(
    session,
    code: str,
    curriculum: dict[str, dict[str, list[str]]],
    metadata: dict[tuple[str, str], dict],
) -> None:
    subject = await subjects_repository.by_code(session, code)
    if subject is None:
        raise RuntimeError(f"Subject {code} not found")
    created = updated = 0
    order = 0
    for class_level, terms in curriculum.items():
        for term_key, titles in terms.items():
            level = await curriculum_levels_repository.for_subject_term(
                session,
                subject.id,
                class_level,
                TERMS[term_key],
            )
            if level is None:
                level = await curriculum_levels_repository.add(
                    session,
                    CurriculumLevel(
                        id=cuid(),
                        subject_id=subject.id,
                        class_level=class_level,
                        term=TERMS[term_key],
                    ),
                    flush=True,
                )
            for title in titles:
                slug = slugify(title)
                values = topic_values(
                    level.id,
                    title,
                    order,
                    metadata.get((code, slug)),
                )
                existing = await topics_repository.by_slug(session, subject.id, slug)
                if existing is None:
                    await topics_repository.add(
                        session,
                        Topic(id=cuid(), subject_id=subject.id, slug=slug, **values),
                        flush=True,
                    )
                    created += 1
                elif apply_changes(existing, values):
                    updated += 1
                order += 1
    print(f"  {subject.name}: {created} created, {updated} updated")


async def main() -> None:
    subjects, curriculum, metadata = load_seed()
    name_to_code = {row["name"]: row["code"] for row in subjects}
    print("Seeding subjects and curriculum")
    async with get_session() as session:
        await seed_subjects(session, subjects)
        for name, terms in curriculum.items():
            code = resolve_code(name, name_to_code)
            await seed_curriculum(session, code, terms, metadata)
        await session.commit()
    print("Seed completed")


if __name__ == "__main__":
    asyncio.run(main())
