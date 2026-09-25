"""Seed subjects and the SS1–SS3 curriculum.

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


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.ASCII)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")


def load_seed() -> tuple[list[dict], dict[str, list[dict]]]:
    subjects = json.loads((HERE / "subject.json").read_text())
    curricula = json.loads((HERE / "curriculum.json").read_text())
    return subjects, curricula


async def seed_subjects(session, subjects: list[dict]) -> None:
    for row in subjects:
        existing = await subjects_repository.by_code(session, row["code"])
        if existing is not None:
            continue
        await subjects_repository.add(
            session,
            Subject(
                id=cuid(),
                name=row["name"],
                slug=slugify(row["name"]),
                code=row["code"],
                track_category=row["trackCategory"],
                is_waec=row["isWaec"],
                is_jamb=row["isJamb"],
                is_neco=row["isNeco"],
            ),
            flush=True,
        )
    print(f"  subjects: {len(subjects)}")


async def seed_curriculum(session, code: str, curriculum: list[dict]) -> None:
    subject = await subjects_repository.by_code(session, code)
    if subject is None:
        raise RuntimeError(f"Subject {code} not found")
    topic_count = 0
    order = 0
    for term_data in curriculum:
        level = await curriculum_levels_repository.for_subject_term(
            session,
            subject.id,
            term_data["classLevel"],
            term_data["term"],
        )
        if level is None:
            level = await curriculum_levels_repository.add(
                session,
                CurriculumLevel(
                    id=cuid(),
                    subject_id=subject.id,
                    class_level=term_data["classLevel"],
                    term=term_data["term"],
                ),
                flush=True,
            )
        for topic_def in term_data["topics"]:
            slug = slugify(topic_def["title"])
            existing = await topics_repository.by_slug(session, subject.id, slug)
            if existing is None:
                await topics_repository.add(
                    session,
                    Topic(
                        id=cuid(),
                        subject_id=subject.id,
                        curriculum_level_id=level.id,
                        title=topic_def["title"],
                        slug=slug,
                        order_index=order,
                        estimated_minutes=topic_def["estimatedMinutes"],
                        waec_weight=topic_def["waecWeight"],
                        jamb_weight=topic_def["jambWeight"],
                    ),
                    flush=True,
                )
            else:
                existing.curriculum_level_id = level.id
            order += 1
            topic_count += 1
    print(f"  {subject.name}: {topic_count} topics")


async def main() -> None:
    subjects, curricula = load_seed()
    print("Seeding subjects and curriculum")
    async with get_session() as session:
        await seed_subjects(session, subjects)
        for code, curriculum in curricula.items():
            await seed_curriculum(session, code, curriculum)
        await session.commit()
    print("Seed completed")


if __name__ == "__main__":
    asyncio.run(main())
