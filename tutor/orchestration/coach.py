"""Coach turn — stream the Socratic coach reply, given the gate's verdict + the transcript.

The gate (``orchestration.gate``) decides advancement; the **coach speaks** to the learner. It uses
the full coach rubric (persona + the current step's guide + the problem) plus a per-turn *directive*
derived from the verdict: acknowledge & advance, give a graduated hint, answer a question, or redirect.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from tutor.domain.steps import Step
from tutor.domain.verdict import GateVerdict, Verdict
from tutor.models.base import ChatMessage, CoachProvider
from tutor.skills import loader


def build_coach_system(step: Step, problem_context: str) -> str:
    """Full coach rubric + the current step's guide + the grounded problem."""
    return (
        f"{loader.system_prompt()}\n\n"
        f"---\n\n## Current step: {step.value}\n\n{loader.step_guide(step)}\n\n"
        f"---\n\n## Problem context\n\n{problem_context}"
    )


def _directive(verdict: GateVerdict, advanced: bool, completed: bool) -> str:
    if completed:
        return (
            "The learner just cleared the FINAL step. Give a short, warm wrap-up: one thing they did "
            "well, the single key takeaway, and the final Time/Space complexity."
        )
    if advanced:
        return (
            "The learner cleared this step. Acknowledge it briefly and genuinely, then pose the "
            "opening question for the NEXT step."
        )
    if verdict.verdict is Verdict.QUESTION:
        return (
            "The learner asked a question. Answer it concisely within this step's guardrails (no "
            "spoilers beyond what the step allows), then re-pose the step's question."
        )
    if verdict.verdict is Verdict.OFF_TOPIC:
        return "The learner drifted off-topic. Gently redirect them to the current step's question."
    missing = "; ".join(verdict.missing) if verdict.missing else "the criterion isn't fully met yet"
    return (
        f"The learner has NOT cleared this step yet ({missing}). Give ONE targeted Socratic nudge at "
        f"hint level {verdict.next_hint_level} — a leading question, never the answer. Stay "
        "spoiler-free for this step."
    )


def stream_coach(
    provider: CoachProvider,
    *,
    step: Step,
    problem_context: str,
    transcript: list[ChatMessage],
    verdict: GateVerdict,
    advanced: bool,
    completed: bool,
) -> AsyncIterator[str]:
    """Return the coach's streamed reply (text deltas) for this turn."""
    system = (
        build_coach_system(step, problem_context)
        + "\n\n---\n\n## Your task this turn\n"
        + _directive(verdict, advanced, completed)
    )
    return provider.coach_stream(system=system, messages=transcript)
