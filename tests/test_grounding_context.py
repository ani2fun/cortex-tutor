"""Context assembly + the step-gated solution-withholding (the tutor-side leak control)."""

from __future__ import annotations

from tutor.domain.steps import Step
from tutor.grounding import context

_GROUNDED = {
    "title": "Two Sum",
    "summary": "Find two indices that sum to target.",
    "content": "## Problem Statement\n\nReturn the indices summing to the target.",
    "solution": "Use a hashmap of value→index. O(n) time.",
    "citationUrl": "https://cortex.kakde.eu/data-structures-and-algorithms/.../two-sum",
}


def test_fallback_when_grounding_unavailable():
    ctx = context.build_problem_context("book/slug", None, step=Step.CLARIFY)
    assert "book/slug" in ctx
    assert "unavailable" in ctx


def test_solution_withheld_before_implement():
    for step in (Step.CLARIFY, Step.EXAMPLES, Step.APPROACH, Step.PLAN):
        assert context.wants_solution(step) is False
        ctx = context.build_problem_context("p", _GROUNDED, step=step)
        assert "Two Sum" in ctx
        assert "Return the indices" in ctx
        assert "hashmap" not in ctx  # ← the solution must not enter the context


def test_solution_included_at_implement_and_test():
    for step in (Step.IMPLEMENT, Step.TEST):
        assert context.wants_solution(step) is True
        ctx = context.build_problem_context("p", _GROUNDED, step=step)
        assert "hashmap" in ctx  # reference solution available for grading


def test_no_solution_returned_stays_safe_at_implement():
    grounded = {**_GROUNDED, "solution": None}
    ctx = context.build_problem_context("p", grounded, step=Step.IMPLEMENT)
    assert "Two Sum" in ctx
    assert "hashmap" not in ctx  # nothing to include → nothing leaked


def test_citation_included():
    ctx = context.build_problem_context("p", _GROUNDED, step=Step.CLARIFY)
    assert "cortex.kakde.eu" in ctx
