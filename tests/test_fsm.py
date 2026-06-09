"""Unit tests for the pure six-step FSM. No IO, no async — just the transition algebra."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tutor.domain.fsm import (
    MAX_HINT_LEVEL,
    SessionState,
    SessionStatus,
    transition,
)
from tutor.domain.steps import STEP_ORDER, Step, is_terminal, next_step, step_index
from tutor.domain.verdict import GateVerdict, Verdict

# ── steps ordering ───────────────────────────────────────────────────────────


def test_step_order_is_the_six_step_framework():
    assert STEP_ORDER == (
        Step.CLARIFY,
        Step.EXAMPLES,
        Step.APPROACH,
        Step.PLAN,
        Step.IMPLEMENT,
        Step.TEST,
    )
    assert [step_index(s) for s in STEP_ORDER] == [0, 1, 2, 3, 4, 5]


def test_next_step_walks_then_stops():
    assert next_step(Step.CLARIFY) is Step.EXAMPLES
    assert next_step(Step.IMPLEMENT) is Step.TEST
    assert next_step(Step.TEST) is None
    assert is_terminal(Step.TEST) and not is_terminal(Step.CLARIFY)


# ── helpers ──────────────────────────────────────────────────────────────────


def pass_(score: int = 80) -> GateVerdict:
    return GateVerdict(verdict=Verdict.PASS, score=score)


def retry_(level: int = 0) -> GateVerdict:
    return GateVerdict(verdict=Verdict.RETRY, next_hint_level=level)


# ── PASS advances ────────────────────────────────────────────────────────────


def test_pass_advances_one_step_and_records_score_and_resets_attempts():
    start = SessionState(step=Step.CLARIFY, attempts=2, hint_level=2)
    t = transition(start, pass_(score=90))
    assert t.advanced and not t.completed
    assert t.state.step is Step.EXAMPLES
    assert t.state.attempts == 0 and t.state.hint_level == 0
    assert t.state.scores == {Step.CLARIFY: 90}


def test_pass_through_all_six_completes_after_test():
    state = SessionState()
    seen = []
    for _ in range(len(STEP_ORDER)):
        seen.append(state.step)
        state = transition(state, pass_()).state
    assert seen == list(STEP_ORDER)
    assert state.status is SessionStatus.COMPLETED
    assert set(state.scores) == set(STEP_ORDER)  # a score recorded for every gate


def test_completed_session_is_terminal_noop():
    done = SessionState(step=Step.TEST, status=SessionStatus.COMPLETED)
    t = transition(done, pass_())
    assert t.completed and not t.advanced
    assert t.state is done  # unchanged


# ── RETRY stays, climbs the hint ladder ──────────────────────────────────────


def test_retry_stays_and_consumes_attempt_and_climbs_hint():
    s0 = SessionState(step=Step.APPROACH)
    s1 = transition(s0, retry_()).state
    assert s1.step is Step.APPROACH and s1.attempts == 1 and s1.hint_level == 1
    s2 = transition(s1, retry_()).state
    assert s2.attempts == 2 and s2.hint_level == 2


def test_hint_level_is_monotonic_and_capped():
    s = SessionState(step=Step.PLAN, attempts=0, hint_level=0)
    for _ in range(10):
        s = transition(s, retry_(level=0)).state
    assert s.hint_level == MAX_HINT_LEVEL  # capped, never exceeds 3
    assert s.attempts == 10  # attempts keep counting


def test_model_suggested_hint_level_is_honoured_but_bounded():
    s = transition(SessionState(step=Step.PLAN), retry_(level=3)).state
    assert s.hint_level == MAX_HINT_LEVEL


# ── OFF_TOPIC / QUESTION never move and never burn an attempt ─────────────────


@pytest.mark.parametrize("v", [Verdict.OFF_TOPIC, Verdict.QUESTION])
def test_off_topic_and_question_are_no_progress(v):
    s0 = SessionState(step=Step.EXAMPLES, attempts=1, hint_level=1)
    t = transition(s0, GateVerdict(verdict=v))
    assert not t.advanced and not t.completed
    assert t.state.step is Step.EXAMPLES
    assert t.state.attempts == 1 and t.state.hint_level == 1  # untouched


# ── core invariant: ONLY pass advances; the model can't fabricate it ─────────


@pytest.mark.parametrize(
    "v,should_advance",
    [
        (Verdict.PASS, True),
        (Verdict.RETRY, False),
        (Verdict.OFF_TOPIC, False),
        (Verdict.QUESTION, False),
    ],
)
def test_only_pass_advances(v, should_advance):
    t = transition(SessionState(step=Step.CLARIFY), GateVerdict(verdict=v))
    assert t.advanced is should_advance


def test_never_skips_a_step():
    # Even a perfect score only moves exactly one step.
    t = transition(SessionState(step=Step.CLARIFY), pass_(score=100))
    assert t.state.step is next_step(Step.CLARIFY)


def test_no_advance_means_no_score_recorded():
    t = transition(SessionState(step=Step.CLARIFY), retry_())
    assert t.state.scores == {}


def test_score_enum_rejects_out_of_set_values():
    with pytest.raises(ValidationError):  # pydantic ValidationError (Literal enum)
        GateVerdict(verdict=Verdict.PASS, score=83)  # 83 not in the score enum


def test_extra_fields_forbidden():
    # additionalProperties:false — a model trying to inject 'safe_to_advance' is rejected.
    with pytest.raises(ValidationError):
        GateVerdict(verdict=Verdict.PASS, safe_to_advance=True)  # type: ignore[call-arg]
