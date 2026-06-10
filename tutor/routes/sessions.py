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

from tutor.auth import CurrentPrincipal, wants_byok
from tutor.config import get_settings
from tutor.domain.steps import Step
from tutor.grounding import context as grounding_context
from tutor.grounding.mcp_client import GroundingClient
from tutor.models.base import CoachProvider, GateProvider
from tutor.models.factory import make_coach_provider, make_gate_provider
from tutor.orchestration import byok as byok_orch
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


def _grounding_client(request: Request) -> GroundingClient:
    client = getattr(request.app.state, "grounding_client", None)
    if client is None:
        s = get_settings()
        client = GroundingClient(s.mcp_url, s.mcp_service_token)
        request.app.state.grounding_client = client
    return client


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
        "byok": s.byok,
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
            # The tier is pinned at creation: homelab (server-side gate+coach on our key) or BYOK
            # (client-direct on the user's key; the server only records). `reset` re-derives it.
            s = await repo.create(
                db,
                user_sub=principal.sub,
                problem_id=problem_id,
                origin=origin,
                rubric_version=loader.rubric_version(),
                byok=wants_byok(principal, get_settings()),
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


@router.post("/{session_id}/reset")
async def reset_session(session_id: UUID, principal: CurrentPrincipal, request: Request) -> dict:
    """Abandon the addressed session and start fresh at ``clarify``. The row lock serialises
    concurrent resets against the one-active unique index; the tier is re-derived, so reset is
    also the migration path for sessions created before a tier change."""
    async with request.app.state.sessionmaker() as db:
        s = await repo.get_for_user_locked(db, session_id, principal.sub)
        if s is None:
            raise HTTPException(status_code=404, detail="session not found")
        if s.status == "active":
            s.status = "abandoned"
            s.updated_at = dt.datetime.now(dt.UTC)
        fresh = await repo.create(
            db,
            user_sub=principal.sub,
            problem_id=s.problem_id,
            origin=s.origin,
            rubric_version=loader.rubric_version(),
            byok=wants_byok(principal, get_settings()),
        )
        await db.commit()
        return _session_payload(fresh, [])


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
    # Workbench evidence (implement/test) — the editor's code + the latest run output. Size-guarded
    # here (413 per the contract); compose_answer applies its own prompt-side caps downstream.
    code, language, run_result = body.get("code"), body.get("language"), body.get("runResult")
    caps = (("code", code, 64_000), ("language", language, 100), ("runResult", run_result, 16_000))
    for name, value, cap in caps:
        if value is not None and not isinstance(value, str):
            raise HTTPException(status_code=422, detail=f"{name} must be a string")
        if value is not None and len(value) > cap:
            raise HTTPException(status_code=413, detail=f"{name} exceeds {cap} characters")

    # ── Gate + FSM + persist, synchronously: a stale/mismatched write must surface as HTTP 409
    #    (the contract's 409 carries the current session), which is only possible before the SSE
    #    stream opens. The coach reply — the long, streamed part — comes after.
    async with request.app.state.sessionmaker() as db:
        s = await repo.get_for_user(db, session_id, principal.sub)
        if s is None:
            raise HTTPException(status_code=404, detail="session not found")
        if s.byok:
            # The homelab-key spend path is allowlist-only; BYOK sessions turn via prompt-bundle +
            # byok-record (client-direct). The client branches on `session.byok` before calling here.
            raise HTTPException(
                status_code=403, detail="byok_required: this session uses the bring-your-own-key flow"
            )
        # Grounded context for THIS step: the worked solution is folded in only at implement/test
        # (the context-withholding leak control). Degrades to an id-only context if MCP is down.
        # (Fetching it concurrently with the gate's prompt assembly is a later optimisation.)
        grounded = await _grounding_client(request).get_lesson(
            s.problem_id, include_solution=grounding_context.wants_solution(step)
        )
        problem_ctx = grounding_context.build_problem_context(s.problem_id, grounded, step=step)
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
                code=code,
                language=language,
                run_result=run_result,
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

        # Audit the grounding used (best-effort; skip on replay). apply_turn already committed, so
        # this is a small follow-up commit on the same session.
        if grounded and grounded.get("citationUrl") and not replayed:
            await repo.add_grounding_ref(
                db,
                session_id=session_id_val,
                step=evaluated_step.value,
                tool="get_lesson",
                citation_url=grounded["citationUrl"],
            )
            await db.commit()

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


# ── BYOK (client-direct) — the server assembles prompts and records outcomes; the user's key
#    never reaches it. See orchestration/byok.py + plan §7. ─────────────────────────────────────


@router.get("/{session_id}/prompt-bundle")
async def get_prompt_bundle(
    session_id: UUID, step: str, principal: CurrentPrincipal, request: Request
) -> dict:
    try:
        step_e = Step(step)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid step: {step}") from None
    async with request.app.state.sessionmaker() as db:
        s = await repo.get_for_user(db, session_id, principal.sub)
        if s is None:
            raise HTTPException(status_code=404, detail="session not found")
        if not s.byok:
            raise HTTPException(status_code=403, detail="not a BYOK session")
        if s.status != "active":
            raise HTTPException(status_code=409, detail="session is already completed")
        # Clamp to the session's CURRENT step — the context-withholding control: a client must not
        # be able to request the implement-step bundle (which carries the solution) while at clarify.
        if step_e.value != s.current_step:
            raise HTTPException(
                status_code=409, detail=f"requested step '{step}' != current '{s.current_step}'"
            )
        history = await repo.load_recent_messages(db, s.id, limit=200)
        problem_id = s.problem_id

    grounded = await _grounding_client(request).get_lesson(
        problem_id, include_solution=grounding_context.wants_solution(step_e)
    )
    problem_ctx = grounding_context.build_problem_context(problem_id, grounded, step=step_e)
    return {
        "step": step_e.value,
        "system": byok_orch.build_byok_system(step_e, problem_ctx),
        "messages": [
            {"role": m.role, "step": m.step, "content": m.content, "createdAtEpochMs": _ms(m.created_at)}
            for m in history
            if m.role in ("user", "coach")
        ],
        "model": get_settings().coach_model,
    }


@router.post("/{session_id}/turns/byok-record")
async def record_byok_turn(
    session_id: UUID, principal: CurrentPrincipal, body: dict, request: Request
) -> Response:
    step_raw, answer = body.get("step"), body.get("text")
    coach_reply, verdict_raw = body.get("coachReply"), body.get("verdict")
    if not step_raw or answer is None or coach_reply is None or verdict_raw is None:
        raise HTTPException(status_code=422, detail="step, text, coachReply and verdict are required")
    try:
        step = Step(step_raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid step: {step_raw}") from None
    code, language, run_result = body.get("code"), body.get("language"), body.get("runResult")
    caps = (
        ("code", code, 64_000),
        ("language", language, 100),
        ("runResult", run_result, 16_000),
        ("coachReply", coach_reply, 64_000),
    )
    for name, value, cap in caps:
        if value is not None and not isinstance(value, str):
            raise HTTPException(status_code=422, detail=f"{name} must be a string")
        if value is not None and len(value) > cap:
            raise HTTPException(status_code=413, detail=f"{name} exceeds {cap} characters")
    try:
        verdict, verdict_outcome = byok_orch.validate_client_verdict(verdict_raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    turn_raw = body.get("turnId")
    turn_uuid = UUID(turn_raw) if turn_raw else None

    async with request.app.state.sessionmaker() as db:
        s = await repo.get_for_user_locked(db, session_id, principal.sub)
        if s is None:
            raise HTTPException(status_code=404, detail="session not found")
        if not s.byok:
            raise HTTPException(status_code=403, detail="not a BYOK session")
        try:
            outcome = await turn_orch.apply_byok_turn(
                db,
                session=s,
                step=step,
                answer=answer,
                coach_reply=coach_reply,
                verdict=verdict,
                verdict_outcome=verdict_outcome,
                raw_verdict=verdict_raw,
                turn_id=turn_uuid,
                code=code,
                language=language,
                run_result=run_result,
            )
        except StepMismatch as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except StaleTurn:
            await db.rollback()
            fresh = await repo.get_for_user(db, session_id, principal.sub)
            history = await repo.load_recent_messages(db, session_id, limit=200) if fresh else []
            payload = _session_payload(fresh, history) if fresh else {"error": "stale_turn"}
            return JSONResponse(status_code=409, content=payload)

        result_base = _result_base(outcome)
        reply_text = coach_reply
        if outcome.replayed:  # re-POST of a committed turn — return the recorded reply, change nothing
            history = await repo.load_recent_messages(db, outcome.session.id, limit=200)
            reply_text = _last_coach_reply(history, outcome.evaluated_step)

    reply = _coach_message(result_base["step"], reply_text, _now_ms())
    return JSONResponse(content={**result_base, "reply": reply, "usage": None})
