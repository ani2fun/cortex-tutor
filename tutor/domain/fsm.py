"""The six-step coaching state machine — a pure, total transition function.

This is the heart of the "stateful agent, not a quiz" promise and the load-bearing CCA artifact:
**the loop lives in code, the model is a stateless function called inside each step.** The DB
row is the source of truth and projects onto ``SessionState``; ``transition`` is a pure function
over it (no IO), so it is testable and the gate transition is deterministic and auditable.

Invariants enforced here (never by the model):
  * advancement is recomputed as ``verdict == PASS`` — the model cannot fabricate an advance;
  * a step is never skipped (we only ever move to ``next_step``);
  * a step is never advanced without a recorded ``PASS`` score;
  * ``RETRY`` climbs a graduated hint ladder (capped); ``OFF_TOPIC``/``QUESTION`` keep the step.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from tutor.domain.steps import Step, next_step
from tutor.domain.verdict import GateVerdict, Verdict

#: Hint ladder tops out here (0 = none … 3 = worked hint, only ever inside IMPLEMENT).
MAX_HINT_LEVEL = 3
#: Attempts at a single step after which the hint ladder is maxed.
MAX_ATTEMPTS = 3


class SessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"


@dataclass(frozen=True)
class SessionState:
    """Immutable snapshot of a session's progress (the DB row projects onto this)."""

    step: Step = Step.CLARIFY
    attempts: int = 0  # attempts at the CURRENT step
    hint_level: int = 0  # 0..MAX_HINT_LEVEL graduated-hint ladder for the current step
    status: SessionStatus = SessionStatus.ACTIVE
    scores: dict[Step, int] = field(default_factory=dict)  # cleared-gate score per passed step


@dataclass(frozen=True)
class Transition:
    """Outcome of applying a verdict: the new state and what happened."""

    state: SessionState
    advanced: bool
    completed: bool


def transition(state: SessionState, verdict: GateVerdict) -> Transition:
    """Apply a gate ``verdict`` to ``state`` and return the next ``Transition``. Pure and total."""
    # Terminal: a completed session never transitions again.
    if state.status is SessionStatus.COMPLETED:
        return Transition(state=state, advanced=False, completed=True)

    # The ONLY way to advance — recomputed here, never trusted from the model.
    if verdict.verdict is Verdict.PASS:
        scores = {**state.scores, state.step: int(verdict.score)}
        nxt = next_step(state.step)
        if nxt is None:  # passed the terminal step → done
            done = replace(
                state,
                status=SessionStatus.COMPLETED,
                attempts=0,
                hint_level=0,
                scores=scores,
            )
            return Transition(state=done, advanced=True, completed=True)
        moved = replace(state, step=nxt, attempts=0, hint_level=0, scores=scores)
        return Transition(state=moved, advanced=True, completed=False)

    # RETRY: stay on the step, consume an attempt, climb the hint ladder.
    if verdict.verdict is Verdict.RETRY:
        attempts = state.attempts + 1
        hint_level = min(
            MAX_HINT_LEVEL,
            max(state.hint_level, verdict.next_hint_level, attempts),
        )
        retried = replace(state, attempts=attempts, hint_level=hint_level)
        return Transition(state=retried, advanced=False, completed=False)

    # OFF_TOPIC | QUESTION: we answered/redirected in-guardrail — no attempt consumed, no move.
    return Transition(state=state, advanced=False, completed=False)
