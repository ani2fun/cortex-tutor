"""BYOK (bring-your-own-key) orchestration — the client-direct turn (plan §7, P7).

The browser fetches a **prompt bundle** (the combined gate+coach system + the bounded transcript),
calls its own provider with the user's key — which never reaches this server — and posts the
outcome to ``byok-record``. This module owns the two server-side halves of that loop: assembling
the combined system prompt, and validating the client-supplied verdict through the SAME one-shot
coerce pipeline the server gate uses. A verdict that can't be repaired is a 422 contract violation
(the client can fix and resend), not a fail-safe retry — there is no model call here to fail.
"""

from __future__ import annotations

from typing import Literal

from pydantic import ValidationError

from tutor.domain.steps import Step
from tutor.domain.verdict import GateVerdict
from tutor.orchestration import coach
from tutor.orchestration.gate import _coerce

#: The forced-tool name the browser uses for the combined call (mirrored in the cortex client).
TOOL_NAME = "record_byok_turn"

_DUTY = """\
You are doing BOTH jobs in this one call. First, silently JUDGE the learner's latest message
against the current step's gate criterion above — strict and fair: emit `pass` only if it genuinely
meets the step's pass threshold; otherwise `retry` and name what's missing. Use `question` /
`off_topic` when the message isn't an attempt at this step. **When in doubt, `retry` — never `pass`
by accident.**

Then write the coach reply (`coachReply`) for the verdict you just chose:
- **pass** (steps remain): acknowledge briefly and genuinely, then pose the opening question for
  the NEXT step.
- **pass** (final step, `test`): a short, warm wrap-up — one thing done well, the single key
  takeaway, the final Time/Space complexity.
- **retry**: ONE targeted Socratic nudge at the next hint level — a leading question, never the
  answer. Stay spoiler-free for this step.
- **question**: answer it concisely within this step's guardrails (no spoilers beyond what the
  step allows), then re-pose the step's question.
- **off_topic**: gently redirect the learner to the current step's question.

Call the `record_byok_turn` tool exactly once with the verdict fields and the coach reply — no
other output."""


def build_byok_system(step: Step, problem_context: str) -> str:
    """The combined gate+coach system for ONE client-direct call: the coach persona + step guide +
    grounded problem (identical to the server coach path), plus the verdict duty. The per-verdict
    directive matrix mirrors ``coach._directive`` — written out statically because the model picks
    the branch itself from its own verdict."""
    return (
        coach.build_coach_system(step, problem_context)
        + "\n\n---\n\n## Your task this turn (combined gate + coach call)\n\n"
        + _DUTY
    )


def validate_client_verdict(raw: dict) -> tuple[GateVerdict, Literal["valid", "coerced"]]:
    """Validate the wire-shaped (camelCase) verdict posted to ``byok-record`` into a domain
    ``GateVerdict``, applying the gate's one-shot ``_coerce`` repair on a near-miss. Raises
    ``ValueError`` when irreparable — the route turns that into a 422."""
    if not isinstance(raw, dict):
        raise ValueError("verdict must be an object")
    snake = {
        "verdict": raw.get("verdict"),
        "score": raw.get("score", 0),
        "rubric_hits": raw.get("rubricHits", raw.get("rubric_hits", [])),
        "missing": raw.get("missing", []),
        "hint": raw.get("hint", ""),
        "next_hint_level": raw.get("nextHintLevel", raw.get("next_hint_level", 0)),
    }
    try:
        return GateVerdict.model_validate(snake), "valid"
    except ValidationError:
        try:
            return GateVerdict.model_validate(_coerce(snake)), "coerced"
        except ValidationError as exc:
            raise ValueError(f"unrepairable verdict payload: {exc}") from exc
