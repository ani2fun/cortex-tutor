"""The six-step Socratic framework — the ordered set of coaching steps.

Pure domain: no IO, no dependencies beyond the stdlib. The order is the contract.
"""

from __future__ import annotations

from enum import StrEnum


class Step(StrEnum):
    """One step (a "gate") in the coaching session."""

    CLARIFY = "clarify"
    EXAMPLES = "examples"
    APPROACH = "approach"
    PLAN = "plan"
    IMPLEMENT = "implement"
    TEST = "test"


#: Canonical order of the six steps. The FSM walks this and never skips.
STEP_ORDER: tuple[Step, ...] = (
    Step.CLARIFY,
    Step.EXAMPLES,
    Step.APPROACH,
    Step.PLAN,
    Step.IMPLEMENT,
    Step.TEST,
)


def step_index(step: Step) -> int:
    """0-based position of ``step`` in the canonical order."""
    return STEP_ORDER.index(step)


def next_step(step: Step) -> Step | None:
    """The step after ``step``, or ``None`` if ``step`` is the terminal step."""
    i = step_index(step)
    return STEP_ORDER[i + 1] if i + 1 < len(STEP_ORDER) else None


def is_terminal(step: Step) -> bool:
    """True iff ``step`` is the last step (passing it completes the session)."""
    return step is STEP_ORDER[-1]
