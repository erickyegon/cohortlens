"""Small-cell suppression (NFR-4, FR-10, FR-11): counts of 1 to 10 are never shown, they become "<11".

Pure logic with no database access. The engine's `StepCount` carries exact numbers and stays internal; everything
that leaves the API goes through `suppress_steps`, which returns `PublicStepCount` (key, label, persons) and nothing
else, so there is no person-level field to leak.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.cohort_engine.attrition import StepCount

SMALL_CELL_THRESHOLD = 11
SMALL_CELL_LABEL = "<11"


@dataclass(frozen=True)
class PublicStepCount:
    key: str
    label: str
    persons: int | str  # an exact number of 0 or 11+, or the string "<11"


def suppress_count(count: int) -> int | str:
    """0 stays 0, 1..10 become "<11", 11 and above stay exact. Booleans, floats and negatives are rejected."""
    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError("a count must be an integer")
    if count < 0:
        raise ValueError("a count cannot be negative")
    return SMALL_CELL_LABEL if 0 < count < SMALL_CELL_THRESHOLD else count


def suppress_steps(steps: list[StepCount]) -> list[PublicStepCount]:
    return [PublicStepCount(step.key, step.label, suppress_count(step.persons)) for step in steps]
