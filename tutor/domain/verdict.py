"""The gate verdict — the structured output the GATE model is forced to emit.

The model *classifies*; the server *decides*. There is deliberately **no** ``safe_to_advance``
field: advancement is recomputed in the FSM as ``verdict == PASS`` and is never trusted from the
model (see ``tutor.domain.fsm``). The gate can only ever withhold progress, never grant it.

Schema note (Anthropic structured outputs / strict tool-use): JSON Schema ``minimum``/``maximum``
are **silently dropped**, so ``score`` is an integer **enum**, not an int with a 0..100 range, and
``extra='forbid'`` renders ``additionalProperties: false`` (required by the structured-output API).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Verdict(StrEnum):
    """The gate model's classification of a learner's answer at the current step."""

    PASS = "pass"  # gate cleared → the FSM advances
    RETRY = "retry"  # insufficient → stay, give targeted feedback + a bigger hint
    OFF_TOPIC = "off_topic"  # not about the current step → stay, redirect in-guardrail
    QUESTION = "question"  # learner asked a question → answer it, stay


#: Allowed score values (an enum, not a range — see module docstring).
SCORE_VALUES: tuple[int, ...] = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
Score = Literal[0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
HintLevel = Literal[0, 1, 2, 3]


class GateVerdict(BaseModel):
    """Validated gate output. Produced by the gate model (strict tool-use), or synthesised as a
    fail-safe ``RETRY`` whenever the model output is uncertain (malformed / refusal / max_tokens /
    timeout) — the gate must never *grant* progress by accident."""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    score: Score = 0
    rubric_hits: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    hint: str = ""
    next_hint_level: HintLevel = 0

    @classmethod
    def retry_failsafe(cls, hint: str = "Let's try that again.") -> GateVerdict:
        """The safe default when the gate result can't be trusted: withhold progress."""
        return cls(verdict=Verdict.RETRY, score=0, hint=hint)
