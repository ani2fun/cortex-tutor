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


async def _evaluate_full(provider: FakeGateProvider, step: Step = Step.CLARIFY) -> gate.GateEvaluation:
    return await gate.evaluate(
        provider, step=step, problem_context=PROBLEM, transcript=[], answer="my answer"
    )


async def _evaluate(provider: FakeGateProvider, step: Step = Step.CLARIFY):
    return (await _evaluate_full(provider, step)).verdict


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


async def test_tie_score_snaps_down():
    # 95 is equidistant from 90 and 100; ties snap DOWN — the gate never grants more than the
    # model intended.
    v = await _evaluate(FakeGateProvider(result={"verdict": "pass", "score": 95}))
    assert v.verdict is Verdict.PASS
    assert v.score == 90


async def test_empty_string_missing_coerces_to_empty_list():
    # Live repro (2026-06-09, Haiku gate, step=plan): score=95 + missing="" fell through repair to
    # the fail-safe RETRY/0 — a plan the gate scored 95 was graded as a failure.
    v = await _evaluate(
        FakeGateProvider(result={"verdict": "pass", "score": 95, "missing": ""}), step=Step.PLAN
    )
    assert v.verdict is Verdict.PASS
    assert v.score == 90
    assert v.missing == []


async def test_null_rubric_hits_coerces_to_empty_list():
    v = await _evaluate(FakeGateProvider(result={"verdict": "pass", "score": 80, "rubric_hits": None}))
    assert v.verdict is Verdict.PASS
    assert v.rubric_hits == []


async def test_bare_string_list_field_coerces_to_singleton():
    v = await _evaluate(FakeGateProvider(result={"verdict": "retry", "missing": "no contrasting approach"}))
    assert v.verdict is Verdict.RETRY
    assert v.missing == ["no contrasting approach"]


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
    # rubric_hits is a number — not one of the benign near-miss shapes _coerce repairs → fail-safe
    # RETRY. (A bare *string* is repairable: it coerces to a singleton list.)
    v = await _evaluate(
        FakeGateProvider(result={"verdict": "pass", "rubric_hits": 123}),
        step=Step.TEST,
    )
    assert v.verdict is Verdict.RETRY


# ── the GateEvaluation audit record (the recording seam behind evals/) ──────


async def test_clean_verdict_audits_as_valid_with_raw_and_identity():
    raw = {"verdict": "pass", "score": 80}
    fake = FakeGateProvider(result=raw)
    fake.kind = "fake"
    fake.model_id = "fake-1"
    ev = await _evaluate_full(fake)
    assert ev.outcome == "valid"
    assert ev.raw == raw  # the unvalidated output is preserved verbatim
    assert (ev.provider_kind, ev.model) == ("fake", "fake-1")
    assert ev.latency_ms >= 0
    assert len(ev.problem_context_hash) == 12


async def test_repaired_verdict_audits_as_coerced_with_original_raw():
    raw = {"verdict": "pass", "score": 95, "missing": ""}  # the live 2026-06-09 near-miss shape
    ev = await _evaluate_full(FakeGateProvider(result=raw), step=Step.PLAN)
    assert ev.outcome == "coerced"
    assert ev.raw == raw  # pre-coercion, so the failure SHAPE stays measurable
    assert ev.verdict.score == 90


async def test_irreparable_output_audits_as_failsafe_schema():
    ev = await _evaluate_full(FakeGateProvider(result={"verdict": "pass", "rubric_hits": 123}))
    assert ev.outcome == "failsafe_schema"
    assert ev.raw == {"verdict": "pass", "rubric_hits": 123}
    assert ev.verdict.verdict is Verdict.RETRY


async def test_provider_error_audits_as_failsafe_provider_with_no_raw():
    ev = await _evaluate_full(FakeGateProvider(error=TimeoutError("llm down")))
    assert ev.outcome == "failsafe_provider"
    assert ev.raw is None
    assert ev.verdict.verdict is Verdict.RETRY


async def test_provider_without_identity_attrs_gets_class_name_fallback():
    ev = await _evaluate_full(FakeGateProvider(result={"verdict": "pass", "score": 80}))
    assert ev.provider_kind == "FakeGateProvider"
    assert ev.model == "unknown"


# ── prompt assembly + tool schema ────────────────────────────────────────────


def test_gate_system_includes_rubric_step_guide_and_problem():
    sys = gate.build_gate_system(Step.EXAMPLES, PROBLEM)
    assert "grading" in sys.lower()  # the lean gate grader prompt
    assert "Step 2 — examples" in sys  # the current step's guide
    assert PROBLEM in sys  # the grounded problem context


def test_gate_tool_schema_is_strict():
    schema = gate.gate_tool_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["score"]["enum"] == [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
