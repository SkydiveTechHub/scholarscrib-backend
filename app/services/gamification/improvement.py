"""Did the student genuinely improve? Pure.

Compares a student's latest attempt with their recent comparable ones. The
caller supplies only comparable attempts (same subject and kind) oldest-first.
Small attempts and short histories say "not enough to say" rather than
celebrate noise.
"""

from dataclasses import dataclass

MIN_QUESTIONS = 10  # an attempt smaller than this is too noisy to compare
MIN_BASELINE = 2  # earlier attempts needed before anything can be called
BASELINE_WINDOW = 3  # how many earlier attempts form the baseline
MIN_GAIN = 8  # percentage points that count as a real move


@dataclass(frozen=True)
class Attempt:
    percentage: float
    questions: int


def compare(attempts: list[Attempt]) -> dict:
    usable = [a for a in attempts if a.questions >= MIN_QUESTIONS]
    if len(usable) < MIN_BASELINE + 1:
        return {"status": "insufficient"}
    latest = usable[-1]
    baseline = usable[-1 - BASELINE_WINDOW : -1]
    average = sum(a.percentage for a in baseline) / len(baseline)
    gain = latest.percentage - average
    if gain >= MIN_GAIN:
        status = "improved"
    elif gain <= -MIN_GAIN:
        status = "lower"
    else:
        status = "steady"
    return {"status": status, "from": round(average), "to": round(latest.percentage)}


def ever_improved(attempts: list[Attempt]) -> bool:
    """True if any point in the history was a genuine improvement.

    A later dip must not undo a gain the student really made.
    """
    return any(
        compare(attempts[: i + 1])["status"] == "improved" for i in range(len(attempts))
    )


def message_for(result: dict) -> str | None:
    """Only an improvement gets a message. Dips and ties say nothing."""
    if result.get("status") != "improved":
        return None
    return (
        f"You improved from {result['from']}% to {result['to']}% "
        "across comparable practice."
    )
