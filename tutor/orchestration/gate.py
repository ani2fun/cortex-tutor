"""Gate evaluation — the heart of "evaluate, then advance".

Assemble the gate prompt (rubric system + the current step's criterion + problem context + transcript
+ the learner's answer), call the provider with forced tool-use, validate the tool output into a
``GateVerdict``, attempt **one** repair on a near-miss, and **fail safe to ``RETRY``** on any
uncertainty. The gate can only ever withhold progress, never grant it.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Literal

import structlog
from pydantic import ValidationError

from tutor.domain.steps import Step
from tutor.domain.verdict import SCORE_VALUES, GateVerdict, Verdict
from tutor.models.base import ChatMessage, GateProvider
from tutor.skills import loader

log = structlog.get_logger()

TOOL_NAME = "record_gate_verdict"

_VERDICTS = {v.value for v in Verdict}

#: Which path the validate → repair → fail-safe pipeline took for one invocation.
GateOutcome = Literal["valid", "coerced", "failsafe_schema", "failsafe_provider"]


@dataclass(frozen=True)
class GateEvaluation:
    """One gate invocation's full audit record — the validated verdict plus everything needed to
    quantify schema fragility and flakiness after the fact (``evals/README.md``). Persisted to the
    append-only ``tutor.gate_call`` table in production and consumed directly by the eval runner —
    one code path for both."""

    verdict: GateVerdict
    outcome: GateOutcome
    raw: dict | None  # the UNVALIDATED tool output; None when the provider itself errored
    latency_ms: int
    provider_kind: str
    model: str
    problem_context_hash: str


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
) -> GateEvaluation:
    system = build_gate_system(step, problem_context)
    messages: list[ChatMessage] = [*transcript, {"role": "user", "content": answer}]
    context_hash = hashlib.sha256(problem_context.encode("utf-8")).hexdigest()[:12]

    def _result(verdict: GateVerdict, outcome: GateOutcome, raw: dict | None) -> GateEvaluation:
        return GateEvaluation(
            verdict=verdict,
            outcome=outcome,
            raw=raw,
            latency_ms=int((time.monotonic() - started) * 1000),
            provider_kind=getattr(provider, "kind", type(provider).__name__),
            model=getattr(provider, "model_id", "unknown"),
            problem_context_hash=context_hash,
        )

    started = time.monotonic()
    try:
        raw = await provider.gate(
            system=system,
            messages=messages,
            tool_schema=gate_tool_schema(),
            tool_name=TOOL_NAME,
        )
    except Exception as exc:  # transport / timeout / refusal → withhold progress
        log.warning("gate.provider_error", step=step.value, error=str(exc))
        return _result(
            GateVerdict.retry_failsafe("Let's take another pass at that."), "failsafe_provider", None
        )

    try:
        return _result(GateVerdict.model_validate(raw), "valid", raw)
    except ValidationError as first:
        log.info("gate.repair", step=step.value, error=str(first))
        try:
            return _result(GateVerdict.model_validate(_coerce(raw)), "coerced", raw)
        except ValidationError as second:
            log.warning("gate.repair_failed", step=step.value, error=str(second))
            return _result(GateVerdict.retry_failsafe(), "failsafe_schema", raw)


def _coerce(raw: dict) -> dict:
    """One-shot best-effort repair of a near-miss verdict: snap an out-of-set score to the nearest
    allowed bucket (ties snap **down** — the gate never grants more than the model intended),
    normalise benign list-field shapes (null/empty string → ``[]``, bare string → singleton list),
    clamp the hint level, and default an unknown verdict to ``RETRY``. Anything this can't fix
    falls through to the fail-safe."""
    out = dict(raw)

    if out.get("verdict") not in _VERDICTS:
        out["verdict"] = Verdict.RETRY.value

    score = out.get("score")
    if isinstance(score, bool):  # bool is an int subclass — treat as invalid
        out["score"] = 0
    elif isinstance(score, (int, float)):
        out["score"] = min(SCORE_VALUES, key=lambda b: (abs(b - score), b))
    elif score is not None:
        out["score"] = 0

    level = out.get("next_hint_level")
    if isinstance(level, (int, float)) and not isinstance(level, bool):
        out["next_hint_level"] = max(0, min(3, int(level)))

    for field in ("rubric_hits", "missing"):
        if field in out:
            out[field] = _coerce_str_list(out[field])

    return out


def _coerce_str_list(value: object) -> object:
    """Normalise the known-benign near-miss shapes for ``list[str]`` fields: ``null`` and
    empty/whitespace-only strings mean "nothing to report" → ``[]``; a bare non-empty string is a
    singleton → ``[value]``. Anything else is returned untouched for validation to judge."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return value
