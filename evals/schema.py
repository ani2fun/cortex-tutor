"""The eval-case schema (see ``evals/README.md`` §2).

A case is one frozen gate invocation: the transcript exactly as the gate saw it, the answer under
judgment, the problem context frozen at extraction, and a human-owned expectation. ``Expected`` uses
``allowed_verdicts`` (not a single target) so one scoring rule covers both recorded cases (usually a
singleton) and cross-step probes (anything-but-``pass``): a **false pass** is "emitted ``pass`` when
``pass`` isn't allowed", a **false retry** is "withheld when only ``pass`` is allowed".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from tutor.domain.steps import Step
from tutor.domain.verdict import Verdict

#: Marks machine-drafted labels awaiting the human pass (κ calibration starts with a second judge).
PENDING_REVIEW = "fable-5 draft — PENDING HUMAN REVIEW"


class Expected(BaseModel):
    """The labelled expectation for a case. ``min_score`` only constrains ``pass`` verdicts."""

    allowed_verdicts: list[Verdict]
    min_score: int | None = None
    rationale: str

    def allows(self, verdict: Verdict) -> bool:
        return verdict in self.allowed_verdicts


class Case(BaseModel):
    case_id: str
    kind: Literal["recorded", "cross_step_probe", "synthetic"]
    problem_id: str
    step: Step
    transcript: list[dict] = Field(default_factory=list)  # [{"role": …, "content": …}] as the gate saw it
    answer: str
    problem_context: str  # frozen at extraction — reproducible regardless of corpus drift
    expected: Expected | None = None  # None until labelled; the runner refuses unlabelled cases
    labeller: str | None = None
    source: dict | None = None  # provenance (session_id, answer_seq, …)


def load_cases(path: Path) -> list[Case]:
    cases = [Case.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]
    seen: set[str] = set()
    for c in cases:
        if c.case_id in seen:
            raise ValueError(f"duplicate case_id: {c.case_id}")
        seen.add(c.case_id)
    return cases


def dump_cases(cases: list[Case], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(c.model_dump(mode="json"), ensure_ascii=False) for c in cases]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
