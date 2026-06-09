"""Gate-evaluation tests with a fake provider — exercise parse / repair / fail-safe deterministically.

No live API calls; the real Anthropic gate is smoke-tested separately once a key is available.
"""

from __future__ import annotations

from tutor.domain.steps import Step
from tutor.domain.verdict import Verdict
from tutor.models.base import ChatMessage
from tutor.orchestration import gate

PROBLEM = "Two Sum: return the indices of the two numbers in `nums` that add to `target`."


class FakeGateProvider:
    """A GateProvider stub: returns a canned tool-input dict, or raises a canned error."""

    def __init__(self, *, result: dict | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    async def gate(
        self, *, system: str, messages: list[ChatMessage], tool_schema: dict, tool_name: str
    ) -> dict:
        self.calls.append({"system": system, "messages": messages, "tool_name": tool_name})
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


async def _evaluate(provider: FakeGateProvider, step: Step = Step.CLARIFY):
    return await gate.evaluate(
        provider, step=step, problem_context=PROBLEM, transcript=[], answer="my answer"
    )


# ── happy path ───────────────────────────────────────────────────────────────


async def test_clean_pass_verdict_is_returned_verbatim():
    fake = FakeGateProvider(
        result={
            "verdict": "pass",
            "score": 80,
            "rubric_hits": ["named input/output"],
            "missing": [],
            "hint": "",
            "next_hint_level": 0,
        }
    )
    v = await _evaluate(fake)
    assert v.verdict is Verdict.PASS
    assert v.score == 80
    assert v.rubric_hits == ["named input/output"]
    assert len(fake.calls) == 1 and fake.calls[0]["tool_name"] == gate.TOOL_NAME


# ── one-shot repair ──────────────────────────────────────────────────────────


async def test_out_of_set_score_snaps_to_nearest_bucket():
    v = await _evaluate(FakeGateProvider(result={"verdict": "pass", "score": 83}))
    assert v.verdict is Verdict.PASS
    assert v.score == 80  # 83 → nearest allowed bucket


async def test_unknown_verdict_coerced_to_retry():
    v = await _evaluate(FakeGateProvider(result={"verdict": "maybe", "score": 50}), step=Step.PLAN)
    assert v.verdict is Verdict.RETRY


async def test_out_of_range_hint_level_clamped():
    v = await _evaluate(FakeGateProvider(result={"verdict": "retry", "next_hint_level": 9}))
    assert v.verdict is Verdict.RETRY
    assert v.next_hint_level == 3


# ── fail-safe: the gate can only withhold progress ───────────────────────────


async def test_provider_error_fails_safe_to_retry():
    v = await _evaluate(FakeGateProvider(error=TimeoutError("llm down")), step=Step.APPROACH)
    assert v.verdict is Verdict.RETRY
    assert v.score == 0


async def test_irreparable_output_fails_safe_to_retry():
    # rubric_hits is a string (should be a list); _coerce can't fix it → fail-safe RETRY.
    v = await _evaluate(
        FakeGateProvider(result={"verdict": "pass", "rubric_hits": "oops-not-a-list"}),
        step=Step.TEST,
    )
    assert v.verdict is Verdict.RETRY


# ── prompt assembly + tool schema ────────────────────────────────────────────


def test_gate_system_includes_rubric_step_guide_and_problem():
    sys = gate.build_gate_system(Step.EXAMPLES, PROBLEM)
    assert "Cortex Tutor" in sys  # the rubric system prompt
    assert "Step 2 — examples" in sys  # the current step's guide
    assert PROBLEM in sys  # the grounded problem context


def test_gate_tool_schema_is_strict():
    schema = gate.gate_tool_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["score"]["enum"] == [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
