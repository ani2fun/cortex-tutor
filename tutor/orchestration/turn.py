"""Per-turn orchestration — the gate → FSM → persist cycle (`apply_turn`).

Lock-or-create the active session, replay if the turn was already recorded, run the gate, apply the
pure FSM transition, persist the user message + gate + state (optimistic version → 409 on a concurrent
advance), and **commit** — all *before* the coach speaks. The streamed coach reply is generated
separately (``orchestration.coach``) and persisted via ``record_coach_reply`` after streaming; that
ordering is what lets the route emit the committed state before the first coach token.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tutor.domain.fsm import SessionState, SessionStatus, transition
from tutor.domain.steps import Step, step_index
from tutor.domain.verdict import GateVerdict
from tutor.models.base import ChatMessage, GateProvider
from tutor.orchestration import gate
from tutor.persistence import models, repo
from tutor.skills import loader

log = structlog.get_logger()


class StaleTurn(Exception):
    """Another writer advanced the session first (optimistic-version mismatch) → HTTP 409."""


class StepMismatch(Exception):
    """The submitted step doesn't match the session's current step (or it's completed) → HTTP 409."""


@dataclass(frozen=True)
class TurnOutcome:
    session: models.Session  # the row with the post-turn state applied in memory
    evaluated_step: Step
    verdict: GateVerdict
    advanced: bool
    completed: bool
    # The transcript INCLUDING the learner's latest answer — what the coach responds to.
    coach_messages: list[ChatMessage] = field(default_factory=list)
    replayed: bool = False


def _transcript(history: list[models.Message]) -> list[ChatMessage]:
    return [{"role": "assistant" if m.role == "coach" else "user", "content": m.content} for m in history]


async def apply_turn(
    db: AsyncSession,
    *,
    provider: GateProvider,
    user_sub: str,
    problem_id: str,
    origin: str,
    step: Step,
    answer: str,
    problem_context: str,
    turn_id: UUID | None = None,
) -> TurnOutcome:
    session = await repo.get_active_locked(db, user_sub, problem_id)
    if session is None:
        session = await repo.create(
            db,
            user_sub=user_sub,
            problem_id=problem_id,
            origin=origin,
            rubric_version=loader.rubric_version(),
        )

    # Idempotency — this exact answer-turn already committed? Replay (no re-advance, no re-coach).
    if turn_id is not None and await repo.find_by_turn(db, session.id, turn_id) is not None:
        return TurnOutcome(
            session=session,
            evaluated_step=Step(session.current_step),
            verdict=GateVerdict.retry_failsafe("(already recorded)"),
            advanced=False,
            completed=session.status == SessionStatus.COMPLETED.value,
            replayed=True,
        )

    state = SessionState(
        step=Step(session.current_step),
        attempts=session.attempts,
        hint_level=session.hint_level,
        status=SessionStatus(session.status),
    )
    if state.status is SessionStatus.COMPLETED:
        raise StepMismatch("session is already completed")
    if step is not state.step:
        raise StepMismatch(f"submitted step '{step.value}' != current '{state.step.value}'")

    prior = _transcript(await repo.load_recent_messages(db, session.id))
    answer_msg = await repo.append_message(
        db, session_id=session.id, role="user", step=state.step.value, content=answer, turn_id=turn_id
    )

    evaluation = await gate.evaluate(
        provider, step=state.step, problem_context=problem_context, transcript=prior, answer=answer
    )
    verdict = evaluation.verdict
    result = transition(state, verdict)

    await repo.upsert_gate(
        db,
        session_id=session.id,
        step=state.step.value,
        verdict=verdict.verdict.value,
        score=verdict.score,
        attempts=result.state.attempts,
        missing_json=verdict.missing,
    )
    # The append-only audit row behind the eval dataset (evals/README.md) — committed with the turn.
    await repo.add_gate_call(
        db,
        session_id=session.id,
        turn_id=turn_id,
        step=state.step.value,
        answer_seq=answer_msg.seq,
        rubric_version=session.rubric_version,
        provider=evaluation.provider_kind,
        model=evaluation.model,
        outcome=evaluation.outcome,
        raw_json=evaluation.raw,
        verdict=verdict.verdict.value,
        score=verdict.score,
        missing=verdict.missing,
        hint=verdict.hint,
        problem_context_hash=evaluation.problem_context_hash,
        latency_ms=evaluation.latency_ms,
    )
    advanced_ok = await repo.save_state(
        db,
        session_id=session.id,
        expected_version=session.version,
        status=result.state.status.value,
        current_step=result.state.step.value,
        step_index=step_index(result.state.step),
        attempts=result.state.attempts,
        hint_level=result.state.hint_level,
        last_turn_id=turn_id,
    )
    if not advanced_ok:
        raise StaleTurn("session advanced concurrently")
    await db.commit()

    # Reflect the committed state on the returned row.
    session.status = result.state.status.value
    session.current_step = result.state.step.value
    session.step_index = step_index(result.state.step)
    session.attempts = result.state.attempts
    session.hint_level = result.state.hint_level
    session.version = session.version + 1

    log.info(
        "turn.applied",
        session_id=str(session.id),
        step=state.step.value,
        verdict=verdict.verdict.value,
        advanced=result.advanced,
        completed=result.completed,
    )
    return TurnOutcome(
        session=session,
        evaluated_step=state.step,
        verdict=verdict,
        advanced=result.advanced,
        completed=result.completed,
        coach_messages=[*prior, {"role": "user", "content": answer}],
    )


async def record_coach_reply(db: AsyncSession, *, session_id: UUID, step: Step, content: str) -> None:
    """Persist the streamed coach reply after the stream completes."""
    await repo.append_message(db, session_id=session_id, role="coach", step=step.value, content=content)
    await db.commit()
