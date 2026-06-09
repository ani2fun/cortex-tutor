"""The rubric loader must resolve every skill file the service + evals depend on."""

from __future__ import annotations

from tutor.domain.steps import STEP_ORDER
from tutor.skills import loader


def test_system_prompt_includes_persona_and_verdict_contract():
    sp = loader.system_prompt()
    assert "Cortex Tutor" in sp
    assert "Coach, don't solve" in sp
    assert "verdict" in sp.lower()  # the verdict contract is appended


def test_every_step_has_a_guide_with_a_gate_criterion():
    for step in STEP_ORDER:
        guide = loader.step_guide(step)
        assert "Gate criterion" in guide
        assert "Pass threshold" in guide


def test_rubric_version_is_stable_short_hash():
    v = loader.rubric_version()
    assert isinstance(v, str) and len(v) == 12
    assert v == loader.rubric_version()  # cached + deterministic
    int(v, 16)  # valid hex
