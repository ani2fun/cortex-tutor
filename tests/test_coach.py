"""Unit tests for the coach orchestration — no network, a fake streaming provider.

Covers the per-turn ``_directive`` branches (advance / complete / question / off-topic / retry), that
``build_coach_system`` folds in the rubric + step guide + problem context, and that ``stream_coach``
relays the provider's deltas while passing the directive-augmented system + transcript through.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator

import pytest
from pydantic import ValidationError
from tutor.domain.steps import Step
from tutor.domain.verdict import GateVerdict, Verdict
from tutor.orchestration import coach as coach_orch


class FakeCoach:
    """A CoachProvider stub that records its call and yields fixed deltas."""

    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas
        self.system: str | None = None
        self.messages: list[dict] | None = None

    async def coach_stream(self, *, system: str, messages: list[dict]) -> AsyncIterator[str]:
        self.system = system
        self.messages = messages
        for d in self._deltas:
            yield d


def test_build_coach_system_includes_rubric_step_and_problem():
    system = coach_orch.build_coach_system(Step.CLARIFY, "PROBLEM-CONTEXT-MARKER")
    assert "PROBLEM-CONTEXT-MARKER" in system
    assert "clarify" in system.lower()
    assert len(system) > 200  # the full rubric is folded in, not just the directive


def test_directive_completed_is_a_wrap_up():
    v = GateVerdict(verdict=Verdict.PASS, score=100)
    d = coach_orch._directive(v, advanced=True, completed=True).lower()
    assert "final" in d
    assert "complexity" in d


def test_directive_advanced_opens_next_step():
    v = GateVerdict(verdict=Verdict.PASS, score=90)
    d = coach_orch._directive(v, advanced=True, completed=False).lower()
    assert "next" in d
    assert "final" not in d  # not the completion wrap-up


def test_directive_question_answers_in_guardrail():
    v = GateVerdict(verdict=Verdict.QUESTION)
    d = coach_orch._directive(v, advanced=False, completed=False).lower()
    assert "question" in d
    assert "spoiler" in d  # stays within the step's guardrails


def test_directive_off_topic_redirects():
    v = GateVerdict(verdict=Verdict.OFF_TOPIC)
    d = coach_orch._directive(v, advanced=False, completed=False).lower()
    assert "redirect" in d


def test_directive_retry_nudges_at_hint_level():
    v = GateVerdict(verdict=Verdict.RETRY, missing=["no restatement"], next_hint_level=2)
    d = coach_orch._directive(v, advanced=False, completed=False)
    assert "no restatement" in d  # the gate's missing reasons steer the nudge
    assert "hint level 2" in d
    assert "never the answer" in d.lower()


async def test_stream_coach_relays_deltas_and_appends_directive():
    fake = FakeCoach(["Nice", " work", "."])
    transcript = [{"role": "user", "content": "given nums, return two indices"}]
    out = [
        chunk
        async for chunk in coach_orch.stream_coach(
            fake,
            step=Step.CLARIFY,
            problem_context="ctx",
            transcript=transcript,
            verdict=GateVerdict(verdict=Verdict.PASS, score=90),
            advanced=True,
            completed=False,
        )
    ]
    assert "".join(out) == "Nice work."
    assert fake.messages == transcript  # the transcript is passed through verbatim
    assert "## Your task this turn" in (fake.system or "")
    assert "next" in (fake.system or "").lower()  # the advance directive is appended


# --- Regression guard: the coach speaks PROSE, never the gate's verdict JSON ----------------------
# cortex P5 #28: the coach streamed a fenced GateVerdict block because its system prompt still bundled
# `verdict-contract.md`. The GATE emits the verdict via forced tool-use; the COACH must never emit it.

_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def looks_like_gate_verdict_json(text: str) -> bool:
    """True iff ``text`` is — or contains a fenced — JSON object that validates as a ``GateVerdict``,
    i.e. the coach leaked the gate's structured output instead of coaching in prose."""
    candidates = list(_FENCED_JSON.findall(text))
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    for block in candidates:
        try:
            GateVerdict.model_validate_json(block)
            return True
        except (ValidationError, ValueError):
            continue
    return False


# The exact shape observed in the P5 #28 repro — the detector MUST flag it, or the guard is toothless.
_LEAKED_VERDICT = "```json\n" + json.dumps(
    {
        "verdict": "retry",
        "score": 40,
        "rubric_hits": ["optimal hash map idea"],
        "missing": ["a contrasting approach"],
        "hint": "Now offer a slower approach.",
        "next_hint_level": 2,
    }
) + "\n```"


def test_verdict_json_detector_has_teeth():
    assert looks_like_gate_verdict_json(_LEAKED_VERDICT) is True  # flags the real leak (fenced)
    assert looks_like_gate_verdict_json('{"verdict":"pass","score":100,"next_hint_level":0}') is True
    # ...and does not false-positive on genuine coaching prose.
    assert looks_like_gate_verdict_json("Nice — you've got the hash map. What's a slower approach?") is False


def test_coach_system_prompt_does_not_instruct_verdict_json():
    """Root cause: the coach's assembled system prompt must not carry the verdict contract, or the
    coach model copies that JSON template instead of speaking. Locks cortex P5 #28."""
    system = coach_orch.build_coach_system(Step.APPROACH, "PROBLEM-CONTEXT")
    assert "rubric_hits" not in system
    assert "next_hint_level" not in system
    assert "you cannot return prose here" not in system.lower()

    # The full per-turn system (with the retry directive folded in) likewise carries no verdict
    # template — the directive says "hint level 2" (a value), never the `next_hint_level` field.
    full = (
        coach_orch.build_coach_system(Step.APPROACH, "ctx")
        + "\n\n---\n\n## Your task this turn\n"
        + coach_orch._directive(
            GateVerdict(verdict=Verdict.RETRY, missing=["a contrasting approach"], next_hint_level=2),
            advanced=False,
            completed=False,
        )
    )
    assert "rubric_hits" not in full
    assert "next_hint_level" not in full


@pytest.mark.skipif(
    not (os.environ.get("RUN_LIVE_COACH") and os.environ.get("ANTHROPIC_API_KEY")),
    reason="live coach check needs RUN_LIVE_COACH=1 + ANTHROPIC_API_KEY (the P5 #28 FORCE_LOCAL=false path)",
)
async def test_live_coach_reply_is_prose_not_verdict_json():
    """End-to-end against the real Sonnet coach: the streamed reply must be prose, never a GateVerdict.
    A clean no-op in bare CI; the guard that actually exercised the reported failure mode."""
    from tutor.config import get_settings
    from tutor.models.anthropic_provider import AnthropicCoachProvider

    settings = get_settings()
    provider = AnthropicCoachProvider(settings.anthropic_api_key, settings.coach_model, max_tokens=256)
    reply = "".join(
        [
            delta
            async for delta in coach_orch.stream_coach(
                provider,
                step=Step.APPROACH,
                problem_context="Problem: Two Sum — return the indices of the two numbers adding to target.",
                transcript=[{"role": "user", "content": "Hash map of complements, O(n) time / O(n) space."}],
                verdict=GateVerdict(
                    verdict=Verdict.RETRY,
                    score=40,
                    missing=["a second, contrasting approach"],
                    next_hint_level=1,
                ),
                advanced=False,
                completed=False,
            )
        ]
    )
    assert reply.strip(), "coach produced no text"
    assert not looks_like_gate_verdict_json(reply), f"coach leaked verdict JSON:\n{reply}"
