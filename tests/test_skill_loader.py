"""The rubric loader must resolve every skill file the service + evals depend on."""

from __future__ import annotations

from tutor.domain.steps import STEP_ORDER, STEP_ORDER_BY_TRACK, Track
from tutor.skills import loader


def test_coach_prompt_has_persona_without_verdict_contract():
    cp = loader.coach_prompt()
    assert "Cortex Tutor" in cp
    assert "Coach, don't solve" in cp
    # The coach speaks prose — it must NOT carry the gate's structured-output contract, or the model
    # copies that JSON template instead of coaching (regression: cortex P5 #28).
    assert "rubric_hits" not in cp
    assert "next_hint_level" not in cp


def test_gate_prompt_is_the_lean_grader_not_the_coach_persona():
    gp = loader.gate_prompt()
    assert "gate" in gp.lower()
    assert "Coach, don't solve" not in gp  # the gate gets the lean grader, not the coach persona


def test_every_step_has_a_guide_with_a_gate_criterion():
    for step in STEP_ORDER:
        guide = loader.step_guide(step)
        assert "Gate criterion" in guide
        assert "Pass threshold" in guide


# ── conceptual track: its own rubric tree (.claude/skills/conceptual-tutor/) ───


def test_conceptual_coach_prompt_has_persona_without_verdict_contract():
    cp = loader.coach_prompt(Track.CONCEPTUAL)
    assert "Cortex Tutor" in cp
    assert "Coach, don't lecture" in cp  # the conceptual prime directive
    # Same regression guard as the coding coach: the coach must not carry the gate's output contract.
    assert "rubric_hits" not in cp
    assert "next_hint_level" not in cp


def test_conceptual_gate_prompt_is_the_lean_grader():
    gp = loader.gate_prompt(Track.CONCEPTUAL)
    assert "gate" in gp.lower()
    assert "Coach, don't lecture" not in gp


def test_every_conceptual_step_has_a_guide_with_a_gate_criterion():
    for step in STEP_ORDER_BY_TRACK[Track.CONCEPTUAL]:
        guide = loader.step_guide(step)
        assert "Gate criterion" in guide
        assert "Pass threshold" in guide


def test_problem_and_conceptual_coach_prompts_differ():
    assert loader.coach_prompt(Track.PROBLEM) != loader.coach_prompt(Track.CONCEPTUAL)


def test_rubric_version_is_stable_short_hash():
    v = loader.rubric_version()
    assert isinstance(v, str) and len(v) == 12
    assert v == loader.rubric_version()  # cached + deterministic
    int(v, 16)  # valid hex
