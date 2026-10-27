"""Score the engine's predictions against what students actually did.

Pure: takes plain rows, touches no database.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass

from app.services.learning.evidence import CONFIDENCE_FLOOR

BUCKETS = 10
LOW_SAMPLE = 30
EPSILON = 1e-6


@dataclass(frozen=True)
class PredictionRow:
    predicted: float
    correct: bool
    confidence: float
    difficulty: str | None


def _clip(value: float) -> float:
    return min(1 - EPSILON, max(EPSILON, value))


def _log_loss(predicted: float, correct: bool) -> float:
    p = _clip(predicted)
    return -math.log(p if correct else 1 - p)


def _summary(rows: list[PredictionRow]) -> dict:
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "baseRate": 0,
            "meanPredicted": 0,
            "brier": 0,
            "logLoss": 0,
            "baselineBrier": 0,
            "baselineLogLoss": 0,
        }
    base = sum(1 for item in rows if item.correct) / n
    clipped = _clip(base)
    return {
        "n": n,
        "baseRate": base,
        "meanPredicted": sum(item.predicted for item in rows) / n,
        "brier": sum((item.predicted - item.correct) ** 2 for item in rows) / n,
        "logLoss": sum(_log_loss(item.predicted, item.correct) for item in rows) / n,
        "baselineBrier": base * (1 - base),
        "baselineLogLoss": -(
            clipped * math.log(clipped) + (1 - clipped) * math.log(1 - clipped)
        ),
    }


def _bucket_index(predicted: float) -> int:
    return min(BUCKETS - 1, max(0, int(predicted * BUCKETS)))


def _buckets(rows: list[PredictionRow]) -> list[dict]:
    grouped: list[list[PredictionRow]] = [[] for _ in range(BUCKETS)]
    for item in rows:
        grouped[_bucket_index(item.predicted)].append(item)
    result = []
    for index, found in enumerate(grouped):
        n = len(found)
        result.append(
            {
                "lower": index / BUCKETS,
                "upper": (index + 1) / BUCKETS,
                "n": n,
                "meanPredicted": sum(i.predicted for i in found) / n if n else None,
                "observed": sum(1 for i in found if i.correct) / n if n else None,
                "lowSample": n < LOW_SAMPLE,
            }
        )
    return result


def calibration_report(rows: Iterable[PredictionRow]) -> dict:
    found = list(rows)
    below = [item for item in found if item.confidence < CONFIDENCE_FLOOR]
    enough = [item for item in found if item.confidence >= CONFIDENCE_FLOOR]
    by_difficulty: dict[str, list[PredictionRow]] = {}
    for item in found:
        by_difficulty.setdefault(item.difficulty or "UNKNOWN", []).append(item)
    return {
        "overall": _summary(found),
        "buckets": _buckets(found),
        "byEvidence": {
            "belowFloor": _summary(below),
            "atOrAboveFloor": _summary(enough),
        },
        "byDifficulty": {key: _summary(items) for key, items in by_difficulty.items()},
    }
