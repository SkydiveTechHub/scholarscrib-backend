import os
import random
import threading
import time

_LOCK = threading.Lock()
_COUNTER = random.randint(0, 1_679_615)
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(value: int) -> str:
    if value < 0:
        value = -value
    if value == 0:
        return "0"
    digits: list[str] = []
    while value:
        value, remainder = divmod(value, 36)
        digits.append(_ALPHABET[remainder])
    return "".join(reversed(digits))


def cuid() -> str:
    """Prisma-shaped cuid. Existing rows keep whatever the database stored."""
    global _COUNTER
    with _LOCK:
        _COUNTER = (_COUNTER + 1) % (36**4)
        count = _COUNTER
    timestamp = _base36(int(time.time() * 1000))
    counter = _base36(count).rjust(4, "0")
    fingerprint = (_base36(os.getpid()) + _base36(random.randint(0, 1295))).rjust(
        4, "0"
    )[:4]
    rand = _base36(random.randint(0, 36**8)).rjust(8, "0")[:8]
    return f"c{timestamp}{counter}{fingerprint}{rand}"
