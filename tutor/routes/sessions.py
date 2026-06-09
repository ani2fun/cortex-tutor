"""Session + turn routes (non-streaming for P2; the SSE-streamed coach lands in P3)."""

from __future__ import annotations

import datetime as dt
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from tutor.auth import CurrentPrincipal
from tutor.config import get_settings
from tutor.domain.steps import Step
from tutor.models.factory import make_gate_provider
from tutor.orchestration import turn as turn_orch
from tutor.orchestration.turn import StaleTurn, StepMismatch, TurnOutcome
from tutor.persistence import models, repo
from tutor.skills import loader

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


def _ms(t: dt.datetime) -> int:
    return int(t.timestamp() * 1000)


def _problem_context(problem_id: str) -> str:
    return (
        f"Problem id: {problem_id}\n"
        "(The full problem statement is supplied by the MCP grounding server in a later phase; for "
        "now the gate reasons from the conversation and this id.)"
    )


def _session_payload(s: models.Session, history: list[models.Message]) -> dict:
    return {
        "sessionId": str(s.id),
        "problemId": s.problem_id,
        "origin": s.origin,
        "status": s.status,
        "currentStep": s.current_step,
        "stepIndex": s.step_index,
        "completed": s.status == "completed",
        "messages": [
            {"role": m.role, "step": m.step, "content": m.content, "createdAtEpochMs": _ms(m.created_at)}
            for m in history
            if m.role in ("user", "coach")
        ],
        "scores": [],  # filled from the gate table in a later phase
        "rubricVersion": s.rubric_version,
    }


def _turn_payload(o: TurnOutcome) -> dict:
    return {
        "sessionId": str(o.session.id),
        "step": o.evaluated_step.value,
        "stepIndex": o.session.step_index,
        "verdict": o.verdict.verdict.value,
        "score": o.verdict.score,
        "advanced": o.advanced,
        "completed": o.completed,
        "hint": o.verdict.hint or None,
        "reply": {
            "role": "coach",
            "step": o.evaluated_step.value,
            "content": o.reply_text,
            "createdAtEpochMs": _ms(dt.datetime.now(dt.UTC)),
        },
    }


@router.post("")
async def create_session(principal: CurrentPrincipal, body: dict, request: Request) -> dict:
    problem_id = body.get("problemId")
    if not problem_id:
        raise HTTPException(status_code=422, detail="problemId is required")
    origin = body.get("origin") or "your_turn"
    async with request.app.state.sessionmaker() as db:
        s = await repo.get_active(db, principal.sub, problem_id)
        if s is None:
            s = await repo.create(
                db,
                user_sub=principal.sub,
                problem_id=problem_id,
                origin=origin,
                rubric_version=loader.rubric_version(),
            )
            await db.commit()
        history = await repo.load_recent_messages(db, s.id)
        return _session_payload(s, history)


@router.get("/{session_id}")
async def get_session(session_id: UUID, principal: CurrentPrincipal, request: Request) -> dict:
    async with request.app.state.sessionmaker() as db:
        s = await repo.get_for_user(db, session_id, principal.sub)
        if s is None:
            raise HTTPException(status_code=404, detail="session not found")
        history = await repo.load_recent_messages(db, s.id, limit=200)
        return _session_payload(s, history)


@router.post("/{session_id}/turns")
async def submit_turn(session_id: UUID, principal: CurrentPrincipal, body: dict, request: Request) -> dict:
    step_raw = body.get("step")
    answer = body.get("text")
    if not step_raw or answer is None:
        raise HTTPException(status_code=422, detail="step and text are required")
    try:
        step = Step(step_raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid step: {step_raw}") from None
    turn_raw = body.get("turnId")
    turn_uuid = UUID(turn_raw) if turn_raw else None

    async with request.app.state.sessionmaker() as db:
        s = await repo.get_for_user(db, session_id, principal.sub)
        if s is None:
            raise HTTPException(status_code=404, detail="session not found")
        provider = make_gate_provider(get_settings())
        try:
            outcome = await turn_orch.run_turn(
                db,
                provider=provider,
                user_sub=principal.sub,
                problem_id=s.problem_id,
                origin=s.origin,
                step=step,
                answer=answer,
                problem_context=_problem_context(s.problem_id),
                turn_id=turn_uuid,
            )
        except StepMismatch as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except StaleTurn as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _turn_payload(outcome)
