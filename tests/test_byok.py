"""BYOK orchestration tests — verdict validation (wire → domain) and the combined system prompt.

The FSM application half (``apply_byok_turn``) is integration-tested against live Postgres in
``test_turn_integration.py``.
"""

from __future__ import annotations

import pytest
from tutor.auth import Principal, tier_for, wants_byok
from tutor.config import Settings
from tutor.domain.steps import Step
from tutor.domain.verdict import Verdict
from tutor.models.catalog import Tier
from tutor.orchestration import byok

PROBLEM = "Two Sum: return the indices of the two numbers in `nums` that add to `target`."


# ── validate_client_verdict: wire-shaped (camelCase) → domain, with the gate's repair ─────────


def test_clean_camelcase_verdict_validates():
    v, outcome = byok.validate_client_verdict(
        {
            "verdict": "pass",
            "score": 80,
            "rubricHits": ["complete code"],
            "missing": [],
            "hint": "",
            "nextHintLevel": 0,
        }
    )
    assert outcome == "valid"
    assert v.verdict is Verdict.PASS
    assert v.score == 80
    assert v.rubric_hits == ["complete code"]


def test_snake_case_fields_accepted_too():
    v, outcome = byok.validate_client_verdict(
        {"verdict": "retry", "score": 30, "rubric_hits": [], "next_hint_level": 2}
    )
    assert outcome == "valid"
    assert v.verdict is Verdict.RETRY
    assert v.next_hint_level == 2


def test_near_miss_score_coerces():
    # The same Haiku-style near-miss the server gate repairs: off-enum score, string `missing`.
    v, outcome = byok.validate_client_verdict({"verdict": "pass", "score": 95, "missing": ""})
    assert outcome == "coerced"
    assert v.verdict is Verdict.PASS
    assert v.score == 90  # ties snap down
    assert v.missing == []


def test_unrepairable_verdict_raises_value_error():
    with pytest.raises(ValueError):
        byok.validate_client_verdict({"verdict": "pass", "rubricHits": 123})


def test_non_dict_verdict_raises_value_error():
    with pytest.raises(ValueError):
        byok.validate_client_verdict("pass")  # type: ignore[arg-type]


def test_missing_optional_fields_default():
    v, outcome = byok.validate_client_verdict({"verdict": "retry"})
    assert outcome == "valid"
    assert (v.score, v.rubric_hits, v.missing, v.hint, v.next_hint_level) == (0, [], [], "", 0)


# ── build_byok_system: coach persona + step guide + problem + the combined-call duty ──────────


def test_byok_system_carries_persona_step_problem_and_duty():
    sys = byok.build_byok_system(Step.IMPLEMENT, PROBLEM)
    assert "Step 5 — implement" in sys  # the step guide
    assert PROBLEM in sys  # the grounded problem context
    assert "combined gate + coach call" in sys  # the duty block
    assert byok.TOOL_NAME in sys  # the forced-tool name the browser must use
    assert "When in doubt, `retry`" in sys  # the fail-closed gate stance survives


# ── wants_byok: the tier decision ─────────────────────────────────────────────────────────────


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_allowlisted_user_is_homelab_tier():
    s = _settings(auth_enabled=True, coach_homelab_users="ani2fun")
    assert wants_byok(Principal(sub="x", preferred_username="ani2fun"), s) is False


def test_unlisted_user_is_byok_tier():
    s = _settings(auth_enabled=True, coach_homelab_users="ani2fun")
    assert wants_byok(Principal(sub="y", preferred_username="someone"), s) is True


def test_dev_principal_rides_homelab_with_auth_off():
    s = _settings(auth_enabled=False, coach_homelab_users="ani2fun")
    assert wants_byok(Principal(sub="dev", preferred_username="dev"), s) is False


def test_force_byok_overrides_everything():
    s = _settings(auth_enabled=False, force_byok=True)
    assert wants_byok(Principal(sub="dev", preferred_username="dev"), s) is True
    s2 = _settings(auth_enabled=True, force_byok=True, coach_homelab_users="ani2fun")
    assert wants_byok(Principal(sub="x", preferred_username="ani2fun"), s2) is True


# ── tier_for: the coach tier (homelab vs byok), mirroring wants_byok ──────────────────────────


def test_tier_for_allowlisted_user_is_homelab():
    s = _settings(auth_enabled=True, coach_homelab_users="ani2fun")
    assert tier_for(Principal(sub="x", preferred_username="ani2fun"), s) is Tier.HOMELAB


def test_tier_for_unlisted_user_is_byok():
    s = _settings(auth_enabled=True, coach_homelab_users="ani2fun")
    assert tier_for(Principal(sub="y", preferred_username="someone"), s) is Tier.BYOK


def test_tier_for_force_byok_is_byok():
    s = _settings(auth_enabled=True, force_byok=True, coach_homelab_users="ani2fun")
    assert tier_for(Principal(sub="x", preferred_username="ani2fun"), s) is Tier.BYOK
