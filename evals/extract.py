"""pg → JSONL case extraction (the production→dataset loop, ``evals/README.md`` §2).

Faithful by construction: transcripts replay ``turn._transcript``'s exact role mapping (including any
flawed coach turns the gate really saw), and the problem context is rebuilt through the same grounding
corpus + ``build_problem_context`` the route uses, then **frozen** into the case. Labels are added by
hand afterwards — extraction emits ``expected: null``.

    uv run python -m evals.extract --session 2afb4b05 --out evals/datasets/two_sum_pilot.jsonl
    uv run python -m evals.extract --probes evals/datasets/two_sum_pilot.jsonl \
        --out evals/datasets/two_sum_probes.jsonl

``--probes`` synthesizes cross-step probes from a *labelled* dataset: each pass-labelled answer is
re-presented at every OTHER step that has a pass-labelled case (that case donates its transcript +
context, so the context-withholding rule stays correct for the probed step). Expected: never ``pass``.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from grounding_mcp.tools import Grounding
from sqlalchemy import select
from tutor.config import get_settings
from tutor.domain.steps import Step
from tutor.domain.verdict import Verdict
from tutor.grounding.context import build_problem_context, wants_solution
from tutor.persistence import models
from tutor.persistence.db import make_engine, make_sessionmaker

from evals.schema import Case, Expected, dump_cases, load_cases


def _role(db_role: str) -> str:
    """The same mapping ``turn._transcript`` applies when assembling the gate's messages."""
    return "assistant" if db_role == "coach" else "user"


def _frozen_context(grounding: Grounding, problem_id: str, step: Step) -> str:
    grounded = grounding.get_lesson(problem_id, include_solution=wants_solution(step))
    if grounded.get("error"):
        raise SystemExit(f"grounding could not resolve {problem_id!r}: {grounded}")
    return build_problem_context(problem_id, grounded, step=step)


async def extract_session(session_prefix: str, out: Path) -> None:
    engine = make_engine(get_settings().database_url)
    try:
        async with make_sessionmaker(engine)() as db:
            rows = (await db.execute(select(models.Session))).scalars().all()
            matches = [s for s in rows if str(s.id).startswith(session_prefix)]
            if len(matches) != 1:
                raise SystemExit(f"session prefix {session_prefix!r} matched {len(matches)} sessions")
            session = matches[0]
            stmt = (
                select(models.Message)
                .where(models.Message.session_id == session.id, models.Message.role != "system")
                .order_by(models.Message.seq)
            )
            messages = (await db.execute(stmt)).scalars().all()
    finally:
        await engine.dispose()

    grounding = Grounding()
    stem = session.problem_id.rsplit("/", 1)[-1]
    sid8 = str(session.id)[:8]
    cases: list[Case] = []
    for i, msg in enumerate(messages):
        if msg.role != "user":
            continue
        step = Step(msg.step)
        cases.append(
            Case(
                case_id=f"{stem}-{sid8}-s{msg.seq}",
                kind="recorded",
                problem_id=session.problem_id,
                step=step,
                transcript=[{"role": _role(m.role), "content": m.content} for m in messages[:i]],
                answer=msg.content,
                problem_context=_frozen_context(grounding, session.problem_id, step),
                source={"session_id": str(session.id), "answer_seq": msg.seq},
            )
        )
    dump_cases(cases, out)
    print(f"wrote {len(cases)} cases → {out}")


def make_probes(labelled: Path, out: Path) -> None:
    cases = load_cases(labelled)
    passes = [c for c in cases if c.expected and c.expected.allowed_verdicts == [Verdict.PASS]]
    if not passes:
        raise SystemExit(f"{labelled} has no pass-labelled cases to build probes from")
    by_step: dict[Step, Case] = {}
    for c in passes:  # one donor per step — the FIRST pass case anchors that step's transcript
        by_step.setdefault(c.step, c)

    probes: list[Case] = []
    seen: set[tuple[str, Step]] = set()  # identical resubmitted answers would duplicate probes
    for answer_case in passes:
        for step, donor in sorted(by_step.items(), key=lambda kv: kv[0].value):
            if step is answer_case.step or (answer_case.answer, step) in seen:
                continue
            seen.add((answer_case.answer, step))
            probes.append(
                Case(
                    case_id=f"probe-{answer_case.case_id}-at-{step.value}",
                    kind="cross_step_probe",
                    problem_id=donor.problem_id,
                    step=step,
                    transcript=donor.transcript,
                    answer=answer_case.answer,
                    problem_context=donor.problem_context,
                    expected=Expected(
                        allowed_verdicts=[Verdict.RETRY, Verdict.OFF_TOPIC, Verdict.QUESTION],
                        rationale=(
                            f"answer addresses '{answer_case.step.value}', presented at "
                            f"'{step.value}' — a pass here is a counted step-hallucination"
                        ),
                    ),
                    labeller="synthesized by evals.extract --probes",
                    source={"answer_case": answer_case.case_id, "transcript_case": donor.case_id},
                )
            )
    dump_cases(probes, out)
    print(f"wrote {len(probes)} probes → {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", help="session uuid (or unique prefix) to extract")
    parser.add_argument("--probes", type=Path, help="labelled dataset to synthesize cross-step probes from")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if bool(args.session) == bool(args.probes):
        parser.error("exactly one of --session / --probes is required")
    if args.session:
        asyncio.run(extract_session(args.session, args.out))
    else:
        make_probes(args.probes, args.out)


if __name__ == "__main__":
    main()
