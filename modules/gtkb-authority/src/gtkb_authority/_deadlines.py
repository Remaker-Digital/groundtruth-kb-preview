"""Internal wait handling for finite operation deadlines and manual ending."""

from __future__ import annotations

import math
import time

Deadline = float | None


def checked(value: Deadline) -> Deadline:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Use a finite monotonic deadline or no automatic cutoff")
    return float(value)


def remaining(value: Deadline) -> float | None:
    return None if value is None else max(0.0, value - time.monotonic())


def expired(value: Deadline) -> bool:
    return value is not None and time.monotonic() >= value


def earliest(*values: Deadline) -> Deadline:
    bounded = [value for value in values if value is not None]
    return min(bounded) if bounded else None


def within(value: Deadline, original: Deadline) -> bool:
    """An absent cutoff can narrow no finite original authorization."""
    return original is None or value is not None and value <= original
