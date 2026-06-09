"""Assemble the ``problem_context`` string fed to the gate + coach from a grounding result.

This is where the **context-withholding** leak control lives on the tutor side: the worked solution is
folded into the context ONLY at the implement/test steps (the client requests it accordingly, and we
double-check here). For clarify→plan the context is statement + examples only — the coach literally
cannot leak what isn't in its context. When grounding is unavailable we fall back to an id-only context
so the turn still proceeds.
"""

from __future__ import annotations

from tutor.domain.steps import Step

#: Steps at which the worked solution may enter the model's context.
_SOLUTION_STEPS = frozenset({Step.IMPLEMENT, Step.TEST})


def wants_solution(step: Step) -> bool:
    return step in _SOLUTION_STEPS


def fallback_context(problem_id: str) -> str:
    return (
        f"Problem id: {problem_id}\n"
        "(The grounding service is unavailable — reason from the conversation and this id.)"
    )


def build_problem_context(problem_id: str, grounded: dict | None, *, step: Step) -> str:
    """Grounded context for ``step``. ``grounded`` is a ``get_lesson`` result (or ``None``)."""
    if not grounded:
        return fallback_context(problem_id)

    parts: list[str] = [f"# {grounded.get('title') or problem_id}"]
    if grounded.get("summary"):
        parts.append(str(grounded["summary"]))

    statement = grounded.get("content") or grounded.get("statement") or ""
    if statement:
        parts.append("## Problem\n\n" + str(statement))

    # Defence in depth: only include the solution when the step allows AND it was actually returned.
    solution = grounded.get("solution")
    if wants_solution(step) and solution:
        parts.append(
            "## Reference solution (for grading the implementation — do NOT reveal verbatim)\n\n"
            + str(solution)
        )

    citation = grounded.get("citationUrl")
    if citation:
        parts.append(f"(source: {citation})")

    return "\n\n".join(parts)
