"""Per-turn orchestration — the gate → FSM → persist cycle.

Lock-or-create the active session, replay if the turn was already recorded, run the gate, apply the
pure FSM transition, persist the gate + state (optimistic version → 409 on a concurrent advance), and
commit. The coach reply is a **minimal placeholder** here; the streamed Sonnet coach lands in P3.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    reply_text: str
    replayed: bool = False


def _transcript(history: list[models.Message]) -> list[ChatMessage]:
    return [
        {"role": "assistant" if m.role == "coach" else "user", "content": m.content}
        for m in history
    ]


def _placeholder_reply(verdict: GateVerdict, advanced: bool, completed: bool) -> str:
    if completed:
        return "That's the final gate — nicely done. (The full coaching summary lands with P3.)"
    if advanced:
        return "Good — that clears this step. Let's move on to the next one."
    return verdict.hint or "Not quite yet — let's refine that and try again."


async def run_turn(
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

    # Idempotency — this exact answer-turn already committed? Replay the current state (no re-advance).
    if turn_id is not None and await repo.find_by_turn(db, session.id, turn_id) is not None:
        current = Step(session.current_step)
        return TurnOutcome(
            session=session,
            evaluated_step=current,
            verdict=GateVerdict.retry_failsafe("(already recorded)"),
            advanced=False,
            completed=session.status == SessionStatus.COMPLETED.value,
            reply_text="(already recorded)",
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

    transcript = _transcript(await repo.load_recent_messages(db, session.id))
    await repo.append_message(
        db, session_id=session.id, role="user", step=state.step.value, content=answer, turn_id=turn_id
    )

    verdict = await gate.evaluate(
        provider, step=state.step, problem_context=problem_context, transcript=transcript, answer=answer
    )
    result = transition(state, verdict)

    await repo.upsert_gate(
        db,
        session_id=session.id,
        step=state.step.value,
        verdict=verdict.verdict.value,
        score=verdict.score,
        attempts=result.state.attempts,
    )

    reply = _placeholder_reply(verdict, result.advanced, result.completed)
    await repo.append_message(
        db, session_id=session.id, role="coach", step=state.step.value, content=reply
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

    # Reflect the committed state on the returned row (the ORM object is now detached-ish but handy).
    session.status = result.state.status.value
    session.current_step = result.state.step.value
    session.step_index = step_index(result.state.step)
    session.attempts = result.state.attempts
    session.hint_level = result.state.hint_level
    session.version = session.version + 1

    log.info(
        "turn.committed",
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
        reply_text=reply,
    )
