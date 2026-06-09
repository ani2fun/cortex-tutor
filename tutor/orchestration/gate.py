"""Gate evaluation — the heart of "evaluate, then advance".

Assemble the gate prompt (rubric system + the current step's criterion + problem context + transcript
+ the learner's answer), call the provider with forced tool-use, validate the tool output into a
``GateVerdict``, attempt **one** repair on a near-miss, and **fail safe to ``RETRY``** on any
uncertainty. The gate can only ever withhold progress, never grant it.
"""

from __future__ import annotations

import structlog
from pydantic import ValidationError

from tutor.domain.steps import Step
from tutor.domain.verdict import SCORE_VALUES, GateVerdict, Verdict
from tutor.models.base import ChatMessage, GateProvider
from tutor.skills import loader

log = structlog.get_logger()

TOOL_NAME = "record_gate_verdict"

_VERDICTS = {v.value for v in Verdict}


def gate_tool_schema() -> dict:
    """The forced-tool ``input_schema`` (score as an enum, ``additionalProperties: false``)."""
    return GateVerdict.model_json_schema()


def build_gate_system(step: Step, problem_context: str) -> str:
    """The lean grader prompt + the current step's gate criterion + the grounded problem. Uses the
    gate-specific prompt (not the full coach rubric) to keep the prompt small for CPU inference."""
    return (
        f"{loader.gate_prompt()}\n\n"
        f"---\n\n## Current step: {step.value}\n\n{loader.step_guide(step)}\n\n"
        f"---\n\n## Problem context\n\n{problem_context}"
    )


async def evaluate(
    provider: GateProvider,
    *,
    step: Step,
    problem_context: str,
    transcript: list[ChatMessage],
    answer: str,
) -> GateVerdict:
    system = build_gate_system(step, problem_context)
    messages: list[ChatMessage] = [*transcript, {"role": "user", "content": answer}]

    try:
        raw = await provider.gate(
            system=system,
            messages=messages,
            tool_schema=gate_tool_schema(),
            tool_name=TOOL_NAME,
        )
    except Exception as exc:  # transport / timeout / refusal → withhold progress
        log.warning("gate.provider_error", step=step.value, error=str(exc))
        return GateVerdict.retry_failsafe("Let's take another pass at that.")

    try:
        return GateVerdict.model_validate(raw)
    except ValidationError as first:
        log.info("gate.repair", step=step.value, error=str(first))
        try:
            return GateVerdict.model_validate(_coerce(raw))
        except ValidationError as second:
            log.warning("gate.repair_failed", step=step.value, error=str(second))
            return GateVerdict.retry_failsafe()


def _coerce(raw: dict) -> dict:
    """One-shot best-effort repair of a near-miss verdict: snap an out-of-set score to the nearest
    allowed bucket, clamp the hint level, and default an unknown verdict to ``RETRY``. Anything this
    can't fix falls through to the fail-safe."""
    out = dict(raw)

    if out.get("verdict") not in _VERDICTS:
        out["verdict"] = Verdict.RETRY.value

    score = out.get("score")
    if isinstance(score, bool):  # bool is an int subclass — treat as invalid
        out["score"] = 0
    elif isinstance(score, (int, float)):
        out["score"] = min(SCORE_VALUES, key=lambda b: abs(b - score))
    elif score is not None:
        out["score"] = 0

    level = out.get("next_hint_level")
    if isinstance(level, (int, float)) and not isinstance(level, bool):
        out["next_hint_level"] = max(0, min(3, int(level)))

    return out
