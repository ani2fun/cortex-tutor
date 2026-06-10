"""Unit tests for the eval runner's pure parts — no network, no DB.

Fabricated ``GateEvaluation``s exercise the failure-shape classifier, the three metric axes
(schema / decision / stability) and the baseline regression check deterministically.
"""

from __future__ import annotations

from evals.gate_runner import Replay, compare_baseline, compute_metrics, failure_shapes
from evals.schema import Case, Expected
from tutor.domain.steps import Step
from tutor.domain.verdict import GateVerdict, Verdict
from tutor.orchestration.gate import GateEvaluation

# ── failure-shape classifier ─────────────────────────────────────────────────


def test_live_incident_shape_is_classified():
    # The 2026-06-09 near-miss: score 95 (not in the enum) + missing as "".
    shapes = failure_shapes({"verdict": "pass", "score": 95, "missing": ""})
    assert shapes == ["score_out_of_enum", "missing_as_string"]


def test_provider_error_and_misc_shapes():
    assert failure_shapes(None) == ["provider_error"]
    assert failure_shapes({"verdict": "maybe", "score": 80}) == ["unknown_verdict"]
    assert failure_shapes({"score": 80}) == ["missing_verdict"]
    assert "rubric_hits_as_null" in failure_shapes({"verdict": "pass", "rubric_hits": None})
    assert "extra_keys" in failure_shapes({"verdict": "pass", "safe_to_advance": True})
    assert "hint_level_out_of_range" in failure_shapes({"verdict": "retry", "next_hint_level": 9})
    assert failure_shapes({"verdict": "pass", "score": 80}) == []


# ── metrics over fabricated replays ──────────────────────────────────────────


def _case(case_id: str, kind: str, allowed: list[Verdict], min_score: int | None = None) -> Case:
    return Case(
        case_id=case_id,
        kind=kind,  # type: ignore[arg-type]
        problem_id="b/p",
        step=Step.PLAN,
        transcript=[],
        answer="a",
        problem_context="ctx",
        expected=Expected(allowed_verdicts=allowed, min_score=min_score, rationale="t"),
    )


def _ev(verdict: str, score: int, outcome: str = "valid", raw: dict | None = None) -> GateEvaluation:
    return GateEvaluation(
        verdict=GateVerdict(verdict=Verdict(verdict), score=score),  # type: ignore[arg-type]
        outcome=outcome,  # type: ignore[arg-type]
        raw=raw if raw is not None else {"verdict": verdict, "score": score},
        latency_ms=100,
        provider_kind="fake",
        model="fake-1",
        problem_context_hash="abc123def456",
    )


def test_metrics_axes_are_independent():
    cases = [
        _case("flaky-pass", "recorded", [Verdict.PASS], min_score=70),
        _case("probe-x", "cross_step_probe", [Verdict.RETRY, Verdict.OFF_TOPIC, Verdict.QUESTION]),
    ]
    replays = [
        # flaky-pass: one deserved pass, one failsafe RETRY (the incident pattern) → flip + denial.
        Replay("flaky-pass", _ev("pass", 90)),
        Replay(
            "flaky-pass",
            _ev("retry", 0, outcome="failsafe_schema", raw={"verdict": "pass", "score": 95, "missing": ""}),
        ),
        # probe: one correct retry, one step-hallucinated pass → false pass.
        Replay("probe-x", _ev("retry", 30)),
        Replay("probe-x", _ev("pass", 80)),
    ]
    m = compute_metrics(cases, replays, n=2)

    assert m["schema"]["valid_rate"] == 0.75
    assert m["schema"]["failsafe_schema_rate"] == 0.25
    assert m["schema"]["failure_shapes"] == {"score_out_of_enum": 1, "missing_as_string": 1}

    assert m["decision"]["false_pass_rate"] == 0.25  # the probe pass
    assert m["decision"]["false_retry_rate"] == 0.25  # the failsafe on a deserved pass
    assert m["decision"]["failsafe_denied_pass"] == 1
    assert m["decision"]["step_hallucination_rate"] == 0.5
    assert m["decision"]["confusion"]["pass→retry"] == 1
    assert m["decision"]["confusion"]["not_pass→pass"] == 1

    assert m["stability"]["flip_rate"] == 1.0  # both cases mixed pass/non-pass
    assert m["per_case"]["flaky-pass"]["flipped"] is True
    assert m["stability"]["max_score_spread"] == 90


def test_underscored_pass_is_tracked_separately():
    cases = [_case("low-pass", "recorded", [Verdict.PASS], min_score=70)]
    m = compute_metrics(cases, [Replay("low-pass", _ev("pass", 40))], n=1)
    assert m["decision"]["accuracy"] == 1.0  # verdict allowed…
    assert m["decision"]["underscored_pass"] == 1  # …but flagged below the band


# ── baseline regression check ────────────────────────────────────────────────


def test_compare_baseline_flags_each_axis():
    m = compute_metrics(
        [_case("c", "recorded", [Verdict.RETRY])],
        [Replay("c", _ev("pass", 80, outcome="coerced", raw={"verdict": "pass", "score": 75}))],
        n=1,
    )
    baseline = {"thresholds": {"false_pass_rate": 0.0, "flip_rate": 0.0, "schema_valid_rate": 1.0}}
    failures = compare_baseline(m, baseline)
    assert len(failures) == 2  # false_pass up + valid_rate down (no flip with one replay)
    ok = {"thresholds": {"false_pass_rate": 1.0, "flip_rate": 1.0, "schema_valid_rate": 0.0}}
    assert compare_baseline(m, ok) == []
