"""Session + turn routes.

``submit_turn`` is the heart of P3: the gate runs (and the FSM advances + persists) **synchronously**
so a stale/mismatched write can still return an HTTP 409, then the committed state and the streamed
coach reply go out over **SSE** — a ``state`` event (before the first token), zero or more ``token``
events, then a ``done`` event carrying the ``TurnResult``. The coach reply is persisted after the
stream completes (``record_coach_reply``), in its own transaction, so the committed gate verdict never
depends on the coach succeeding.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import AsyncIterator
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from tutor.auth import CurrentPrincipal
from tutor.config import get_settings
from tutor.domain.steps import Step
from tutor.models.base import CoachProvider, GateProvider
from tutor.models.factory import make_coach_provider, make_gate_provider
from tutor.orchestration import coach as coach_orch
from tutor.orchestration import turn as turn_orch
from tutor.orchestration.turn import StaleTurn, StepMismatch, TurnOutcome
from tutor.persistence import models, repo
from tutor.skills import loader

log = structlog.get_logger()

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


def _ms(t: dt.datetime) -> int:
    return int(t.timestamp() * 1000)


def _now_ms() -> int:
    return _ms(dt.datetime.now(dt.UTC))


def _problem_context(problem_id: str) -> str:
    return (
        f"Problem id: {problem_id}\n"
        "(The full problem statement is supplied by the MCP grounding server in a later phase; for "
        "now the gate reasons from the conversation and this id.)"
    )


# Providers are memoised on app.state so the underlying HTTP/SDK clients are pooled across requests
# (the env-derived provider choice is static per process). BYOK — per-user keys — is client-direct,
# so it never instantiates a server-side provider here.
def _gate_provider(request: Request) -> GateProvider:
    provider = getattr(request.app.state, "gate_provider", None)
    if provider is None:
        provider = make_gate_provider(get_settings())
        request.app.state.gate_provider = provider
    return provider


def _coach_provider(request: Request) -> CoachProvider:
    provider = getattr(request.app.state, "coach_provider", None)
    if provider is None:
        provider = make_coach_provider(get_settings())
        request.app.state.coach_provider = provider
    return provider


def _coach_message(step_value: str, content: str, created_ms: int) -> dict:
    return {"role": "coach", "step": step_value, "content": content, "createdAtEpochMs": created_ms}


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


def _result_base(o: TurnOutcome) -> dict:
    """The ``TurnResult`` minus its ``reply``/``usage`` — built while the DB session is still open, so
    the SSE generator (which runs after the request scope closes) carries only plain values."""
    return {
        "sessionId": str(o.session.id),
        "step": o.evaluated_step.value,
        "stepIndex": o.session.step_index,
        "verdict": o.verdict.verdict.value,
        "score": o.verdict.score,
        "advanced": o.advanced,
        "completed": o.completed,
        "hint": o.verdict.hint or None,
    }


def _last_coach_reply(history: list[models.Message], step: Step) -> str:
    for m in reversed(history):
        if m.role == "coach" and m.step == step.value:
            return m.content
    return ""


def _sse(event_type: str, **fields: object) -> dict:
    """An sse-starlette frame: the discriminator lives both in the SSE ``event:`` name and inside the
    ``data`` JSON (``type``), matching the ``TurnEvent`` union in the contract."""
    return {"event": event_type, "data": json.dumps({"type": event_type, **fields})}


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
async def submit_turn(
    session_id: UUID, principal: CurrentPrincipal, body: dict, request: Request
) -> Response:
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

    # ── Gate + FSM + persist, synchronously: a stale/mismatched write must surface as HTTP 409
    #    (the contract's 409 carries the current session), which is only possible before the SSE
    #    stream opens. The coach reply — the long, streamed part — comes after.
    async with request.app.state.sessionmaker() as db:
        s = await repo.get_for_user(db, session_id, principal.sub)
        if s is None:
            raise HTTPException(status_code=404, detail="session not found")
        problem_ctx = _problem_context(s.problem_id)
        try:
            outcome = await turn_orch.apply_turn(
                db,
                provider=_gate_provider(request),
                user_sub=principal.sub,
                problem_id=s.problem_id,
                origin=s.origin,
                step=step,
                answer=answer,
                problem_context=problem_ctx,
                turn_id=turn_uuid,
            )
        except StepMismatch as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except StaleTurn:
            # Another tab advanced first; our turn rolled back. Return the now-current session (409).
            await db.rollback()
            fresh = await repo.get_for_user(db, session_id, principal.sub)
            history = await repo.load_recent_messages(db, session_id, limit=200) if fresh else []
            payload = _session_payload(fresh, history) if fresh else {"error": "stale_turn"}
            return JSONResponse(status_code=409, content=payload)

        # Snapshot everything the generator needs as plain values (the DB session closes below).
        history = await repo.load_recent_messages(db, outcome.session.id, limit=200)
        state_payload = _session_payload(outcome.session, history)
        result_base = _result_base(outcome)
        session_id_val = outcome.session.id
        evaluated_step = outcome.evaluated_step
        replayed = outcome.replayed
        replay_reply = _last_coach_reply(history, evaluated_step) if replayed else ""
        coach_transcript = outcome.coach_messages
        verdict = outcome.verdict
        advanced = outcome.advanced
        completed = outcome.completed

    coach_provider = _coach_provider(request)

    async def event_gen() -> AsyncIterator[dict]:
        # 1) Committed state, before the first coach token — lets the UI advance the tracker now.
        yield _sse("state", session=state_payload)

        # 2) Replay (idempotent re-POST): the coach reply already persisted — emit it, don't re-stream.
        if replayed:
            reply = _coach_message(result_base["step"], replay_reply, _now_ms())
            yield _sse("done", result={**result_base, "reply": reply, "usage": None})
            return

        # 3) Stream the coach. A coach failure must not strand the turn (already committed) — fall back
        #    to the gate's hint so the client always reaches a `done`.
        parts: list[str] = []
        try:
            async for delta in coach_orch.stream_coach(
                coach_provider,
                step=evaluated_step,
                problem_context=problem_ctx,
                transcript=coach_transcript,
                verdict=verdict,
                advanced=advanced,
                completed=completed,
            ):
                parts.append(delta)
                yield _sse("token", text=delta)
        except Exception as exc:
            log.warning("coach.stream_failed", session_id=str(session_id_val), error=str(exc))
            if not parts:
                fallback = verdict.hint or "Let's keep going — what's your next thought?"
                parts.append(fallback)
                yield _sse("token", text=fallback)

        reply_text = "".join(parts)
        try:
            async with request.app.state.sessionmaker() as db2:
                await turn_orch.record_coach_reply(
                    db2, session_id=session_id_val, step=evaluated_step, content=reply_text
                )
        except Exception as exc:  # the coach reply is non-critical; the turn already committed
            log.warning("coach.persist_failed", session_id=str(session_id_val), error=str(exc))

        # 4) Done — the committed TurnResult with the full coach reply.
        reply = _coach_message(result_base["step"], reply_text, _now_ms())
        yield _sse("done", result={**result_base, "reply": reply, "usage": None})

    return EventSourceResponse(event_gen(), headers={"X-Accel-Buffering": "no"})
