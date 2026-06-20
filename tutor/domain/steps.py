"""The Socratic coaching steps — ordered ladders keyed by track.

Pure domain: no IO, no dependencies beyond the stdlib. The order is the contract.

Two tracks share one ``Step`` enum:
  * ``PROBLEM`` — the original six-step *coding* interview (clarify → … → test);
  * ``CONCEPTUAL`` — a four-step *understanding* check for prose lessons
    (explain → apply → analyze → defend), where there is no code to run.

Every ``Step`` value belongs to exactly one track, so the per-step helpers
(``step_index``/``next_step``/``is_terminal``) resolve the track *from the step* — the FSM and the
turn pipeline never have to thread a track around. Only the step-less entry points (``first_step``,
``repo.create``) take a ``Track`` explicitly.
"""

from __future__ import annotations

from enum import StrEnum


class Track(StrEnum):
    """Which coaching ladder a session runs (pinned at session creation)."""

    PROBLEM = "problem"  # six-step coding interview
    CONCEPTUAL = "conceptual"  # four-step understanding check (prose lessons)


class Step(StrEnum):
    """One step (a "gate") in a coaching session, across both tracks."""

    # ── PROBLEM track ──
    CLARIFY = "clarify"
    EXAMPLES = "examples"
    APPROACH = "approach"
    PLAN = "plan"
    IMPLEMENT = "implement"
    TEST = "test"
    # ── CONCEPTUAL track ──
    EXPLAIN = "explain"
    APPLY = "apply"
    ANALYZE = "analyze"
    DEFEND = "defend"


#: Canonical order of each track's steps. The FSM walks these and never skips.
STEP_ORDER_BY_TRACK: dict[Track, tuple[Step, ...]] = {
    Track.PROBLEM: (
        Step.CLARIFY,
        Step.EXAMPLES,
        Step.APPROACH,
        Step.PLAN,
        Step.IMPLEMENT,
        Step.TEST,
    ),
    Track.CONCEPTUAL: (
        Step.EXPLAIN,
        Step.APPLY,
        Step.ANALYZE,
        Step.DEFEND,
    ),
}

#: Back-compat alias — the original six-step order (the PROBLEM track).
STEP_ORDER: tuple[Step, ...] = STEP_ORDER_BY_TRACK[Track.PROBLEM]

#: Reverse index: each step → its (unique) track. Built once from STEP_ORDER_BY_TRACK.
_TRACK_OF: dict[Step, Track] = {step: track for track, order in STEP_ORDER_BY_TRACK.items() for step in order}


def track_of(step: Step) -> Track:
    """The track ``step`` belongs to (every step belongs to exactly one)."""
    return _TRACK_OF[step]


def steps_for(track: Track) -> tuple[Step, ...]:
    """The ordered steps of ``track``."""
    return STEP_ORDER_BY_TRACK[track]


def first_step(track: Track) -> Step:
    """The step a fresh ``track`` session starts on."""
    return STEP_ORDER_BY_TRACK[track][0]


def step_index(step: Step) -> int:
    """0-based position of ``step`` within its own track's order."""
    return STEP_ORDER_BY_TRACK[track_of(step)].index(step)


def next_step(step: Step) -> Step | None:
    """The step after ``step`` in its track, or ``None`` if ``step`` is that track's terminal step."""
    order = STEP_ORDER_BY_TRACK[track_of(step)]
    i = order.index(step)
    return order[i + 1] if i + 1 < len(order) else None


def is_terminal(step: Step) -> bool:
    """True iff ``step`` is the last step of its track (passing it completes the session)."""
    return step is STEP_ORDER_BY_TRACK[track_of(step)][-1]
