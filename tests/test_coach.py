"""Unit tests for the coach orchestration — no network, a fake streaming provider.

Covers the per-turn ``_directive`` branches (advance / complete / question / off-topic / retry), that
``build_coach_system`` folds in the rubric + step guide + problem context, and that ``stream_coach``
relays the provider's deltas while passing the directive-augmented system + transcript through.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

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
