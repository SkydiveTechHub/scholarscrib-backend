"""The level curve. Cosmetic only: a level is not an academic qualification."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Level:
    level: int
    threshold: int  # cumulative XP needed to reach this level
    title: str


LEVELS: tuple[Level, ...] = (
    Level(1, 0, "Starter Scholar"),
    Level(2, 100, "Starter Scholar"),
    Level(3, 250, "Rising Scholar"),
    Level(4, 450, "Rising Scholar"),
    Level(5, 700, "Focused Learner"),
    Level(6, 1000, "Focused Learner"),
    Level(7, 1400, "Subject Specialist"),
    Level(8, 1900, "Subject Specialist"),
    Level(9, 2500, "Exam Strategist"),
    Level(10, 3200, "ScholarsCrib Elite"),
)


@dataclass(frozen=True)
class LevelProgress:
    level: int
    title: str
    xp_into_level: int
    xp_to_next: int  # 0 at the top level
    level_span: int  # XP between this level and the next; 0 at the top
    is_max: bool


def level_progress(total_xp: int) -> LevelProgress:
    xp = max(total_xp, 0)
    current = LEVELS[0]
    for candidate in LEVELS:
        if xp >= candidate.threshold:
            current = candidate
    index = current.level  # next level is LEVELS[index]
    if index >= len(LEVELS):
        return LevelProgress(
            current.level, current.title, xp - current.threshold, 0, 0, True
        )
    nxt = LEVELS[index]
    span = nxt.threshold - current.threshold
    return LevelProgress(
        current.level,
        current.title,
        xp - current.threshold,
        nxt.threshold - xp,
        span,
        False,
    )
