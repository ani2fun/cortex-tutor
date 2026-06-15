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

from tutor.auth import CurrentPrincipal, tier_for
from tutor.config import get_settings
from tutor.domain.steps import Step
from tutor.grounding import context as grounding_context
from tutor.grounding.mcp_client import GroundingClient
from tutor.models import catalog
from tutor.models.anthropic_provider import AnthropicCoachProvider
from tutor.models.base import CoachProvider, GateProvider
from tutor.models.factory import make_coach_provider, make_gate_provider, prefers_local
from tutor.models.ollama_provider import OllamaCoachProvider
from tutor.orchestration import byok as byok_orch
from tutor.orchestration import coach as coach_orch
from tutor.orchestration import turn as turn_orch
from tutor.orchestration.turn import StaleTurn, StepMismatch, TurnOutcome
from tutor.persistence import models, repo
from tutor.quotas import check_message_quota, check_session_quota
from tutor.skills import loader

log = structlog.get_logger()

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


def _ms(t: dt.datetime) -> int:
    return int(t.timestamp() * 1000)


def _now_ms() -> int:
    return _ms(dt.datetime.now(dt.UTC))


def _ratelimit(request: Request, bucket: str, sub: str) -> None:
    """Per-principal rate limit for an endpoint class (raises 429 on abuse). See tutor/ratelimit.py."""
    request.app.state.limiters[bucket].check(sub)


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
    # The factory-global coach provider — used only for the LOCAL (local-dev Ollama) path now that
    # the homelab path selects a per-session Claude model via _coach_provider_for.
    provider = getattr(request.app.state, "coach_provider", None)
    if provider is None:
        provider = make_coach_provider(get_settings())
        request.app.state.coach_provider = provider
    return provider


def _coach_provider_for(request: Request, model_id: str) -> CoachProvider:
    """A per-model Anthropic coach provider, pooled on app.state by model id (the server key is
    process-global). Backs the homelab SERVER_KEY path; tests inject fakes by pre-seeding the
    ``coach_providers`` dict."""
    cache = getattr(request.app.state, "coach_providers", None)
    if cache is None:
        cache = {}
        request.app.state.coach_providers = cache
    provider = cache.get(model_id)
    if provider is None:
        provider = AnthropicCoachProvider(get_settings().anthropic_api_key, model_id)
        cache[model_id] = provider
    return provider


def _local_coach_provider_for(request: Request, model_id: str) -> CoachProvider:
    """A per-model Ollama coach provider for a SELECTED local model (e.g. qwen-coach), pooled on
    app.state by model id. Only reached when OLLAMA_URL is set — resolve_coach gates the
    LOCAL-with-entry resolution on ``has_local``; tests inject fakes via ``local_coach_providers``."""
    cache = getattr(request.app.state, "local_coach_providers", None)
    if cache is None:
        cache = {}
        request.app.state.local_coach_providers = cache
    provider = cache.get(model_id)
    if provider is None:
        s = get_settings()
        provider = OllamaCoachProvider(s.ollama_url, model_id, timeout=s.ollama_timeout)
        cache[model_id] = provider
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
        "model": s.coach_model,  # the chosen catalog key (None on pre-selection sessions → default)
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
    _ratelimit(request, "session", principal.sub)
    problem_id = body.get("problemId")
    if not problem_id:
        raise HTTPException(status_code=422, detail="problemId is required")
    if not isinstance(problem_id, str) or len(problem_id) > 256:
        raise HTTPException(status_code=413, detail="problemId exceeds 256 characters")
    origin = body.get("origin") or "your_turn"
    settings = get_settings()
    tier = tier_for(principal, settings)
    # Validate the client's model choice against the tier allow-list — fail closed on anything
    # unknown/disallowed. Never trust a client-supplied model id.
    try:
        entry = catalog.validate_choice(body.get("model"), tier, has_local=bool(settings.ollama_url))
    except catalog.ModelNotAllowed:
        raise HTTPException(
            status_code=422, detail=f"model not available for your tier: {body.get('model')}"
        ) from None
    async with request.app.state.sessionmaker() as db:
        s = await repo.get_active(db, principal.sub, problem_id)
        if s is None:
            # New session for a new problem — enforce the per-user storage budget (BYOK only).
            await check_session_quota(db, principal, settings)
            # The tier AND the coach model are pinned at creation: homelab (server-side gate+coach
            # on our key) or BYOK (client-direct on the user's key; the server only records).
            # `reset` re-derives both. A `model` on a resume is ignored — reset to change it.
            s = await repo.create(
                db,
                user_sub=principal.sub,
                problem_id=problem_id,
                origin=origin,
                rubric_version=loader.rubric_version(),
                # Transport/funding follows the model's PROVIDER, not the tier: a cloud pick is
                # client-direct on the user's key (byok), the local model is server-streamed.
                byok=entry.provider is not catalog.Provider.OLLAMA,
                coach_model=entry.key,
            )
            await db.commit()
        history = await repo.load_recent_messages(db, s.id)
        return _session_payload(s, history)


@router.get("/active")
async def get_active_session(principal: CurrentPrincipal, request: Request, problemId: str) -> Response:
    """The caller's in-progress session for a problem, if any — so a refresh restores the transcript,
    step and model instead of an empty coach. A pure READ: it never creates a session (an un-started
    problem returns 204), so the client lazily creates on the first submit, exactly as before. Declared
    before ``/{session_id}`` so the literal path isn't captured as a session id."""
    _ratelimit(request, "read", principal.sub)
    async with request.app.state.sessionmaker() as db:
        s = await repo.get_active(db, principal.sub, problemId)
        if s is None:
            return Response(status_code=204)
        history = await repo.load_recent_messages(db, s.id, limit=200)
        return JSONResponse(_session_payload(s, history))


@router.get("/{session_id}")
async def get_session(session_id: UUID, principal: CurrentPrincipal, request: Request) -> dict:
    _ratelimit(request, "read", principal.sub)
    async with request.app.state.sessionmaker() as db:
        s = await repo.get_for_user(db, session_id, principal.sub)
        if s is None:
            raise HTTPException(status_code=404, detail="session not found")
        history = await repo.load_recent_messages(db, s.id, limit=200)
        return _session_payload(s, history)


@router.post("/{session_id}/reset")
async def reset_session(session_id: UUID, principal: CurrentPrincipal, request: Request) -> dict:
    """Hard-delete this problem's coach history for the caller and start fresh at ``clarify`` (the
    permanent "Start over"). The row lock serialises concurrent resets against the one-active unique
    index; the model is re-validated + carried forward (its transport re-derived)."""
    _ratelimit(request, "session", principal.sub)
    settings = get_settings()
    tier = tier_for(principal, settings)
    async with request.app.state.sessionmaker() as db:
        s = await repo.get_for_user_locked(db, session_id, principal.sub)
        if s is None:
            raise HTTPException(status_code=404, detail="session not found")
        problem_id, origin, prior_model = s.problem_id, s.origin, s.coach_model
        # Carry the chosen model forward; re-validate so reset also migrates a model that became
        # disallowed for the (re-derived) tier back to the default.
        try:
            model_entry = catalog.validate_choice(prior_model, tier, has_local=bool(settings.ollama_url))
        except catalog.ModelNotAllowed:
            model_entry = catalog.default_model(tier)
        # Hard-delete this problem's sessions (messages cascade) BEFORE re-creating, so the fresh
        # active session can't collide with the one-active partial unique index.
        await repo.delete_for_problem(db, principal.sub, problem_id)
        fresh = await repo.create(
            db,
            user_sub=principal.sub,
            problem_id=problem_id,
            origin=origin,
            rubric_version=loader.rubric_version(),
            byok=model_entry.provider is not catalog.Provider.OLLAMA,
            coach_model=model_entry.key,
        )
        await db.commit()
        return _session_payload(fresh, [])


@router.post("/clear-all")
async def clear_all_sessions(principal: CurrentPrincipal, request: Request) -> dict:
    """Permanently delete EVERY coach session (and cascaded messages) the caller owns. Authenticated
    and strictly scoped to the token's sub — never a body-supplied id."""
    _ratelimit(request, "session", principal.sub)
    async with request.app.state.sessionmaker() as db:
        deleted = await repo.delete_all_for_user(db, principal.sub)
        await db.commit()
        return {"deleted": deleted}


@router.post("/{session_id}/model")
async def change_session_model(
    session_id: UUID, principal: CurrentPrincipal, body: dict, request: Request
) -> dict:
    """Re-point an ACTIVE session's coach model (dual-mode: switch local↔cloud mid-conversation). The
    transcript and FSM state stay; the transport ``byok`` is re-derived from the new model's provider —
    a cloud pick goes client-direct on the user's key, a local pick stays server-streamed."""
    _ratelimit(request, "session", principal.sub)
    requested = body.get("model")
    settings = get_settings()
    tier = tier_for(principal, settings)
    try:
        entry = catalog.validate_choice(requested, tier, has_local=bool(settings.ollama_url))
    except catalog.ModelNotAllowed:
        raise HTTPException(
            status_code=422, detail=f"model not available for your tier: {requested}"
        ) from None
    async with request.app.state.sessionmaker() as db:
        s = await repo.get_for_user_locked(db, session_id, principal.sub)
        if s is None:
            raise HTTPException(status_code=404, detail="session not found")
        if s.status != "active":
            raise HTTPException(status_code=409, detail="session is not active")
        ok = await repo.set_coach_model(
            db,
            session_id=s.id,
            expected_version=s.version,
            coach_model=entry.key,
            byok=entry.provider is not catalog.Provider.OLLAMA,
        )
        if not ok:
            await db.rollback()
            raise HTTPException(status_code=409, detail="session advanced concurrently — reload and retry")
        await db.commit()
        fresh = await repo.get_for_user(db, session_id, principal.sub)
        history = await repo.load_recent_messages(db, fresh.id, limit=200)
        return _session_payload(fresh, history)


@router.post("/{session_id}/turns")
async def submit_turn(
    session_id: UUID, principal: CurrentPrincipal, body: dict, request: Request
) -> Response:
    _ratelimit(request, "turn", principal.sub)
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
    settings = get_settings()
    code, language, run_result = body.get("code"), body.get("language"), body.get("runResult")
    caps = (
        ("text", answer, settings.coach_max_message_chars),
        ("code", code, 64_000),
        ("language", language, 100),
        ("runResult", run_result, 16_000),
    )
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
        await check_message_quota(db, principal, settings, s.id)
        # Grounded context for THIS step: the worked solution is folded in only at implement/test
        # (the context-withholding leak control). Degrades to an id-only context if MCP is down.
        # (Fetching it concurrently with the gate's prompt assembly is a later optimisation.)
        grounded = await _grounding_client(request).get_lesson(
            s.problem_id, include_solution=grounding_context.wants_solution(step)
        )
        problem_ctx = grounding_context.build_problem_context(s.problem_id, grounded, step=step)
        # The gate runs server-side on the Anthropic key (Haiku) for the homelab turn path — fast and
        # consistent. The COACH is the local wk-1 model (resolved below); only the cheap, synchronous
        # gate stays on Claude. (A local 7B gate was tried and reverted: too slow + schema-fragile.)
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
        coach_model_key = outcome.session.coach_model

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

    # Resolve the coach model + how its call is funded, BEFORE the SSE stream opens — a misconfig
    # must surface as a real HTTP error, not a half-written stream. The gate already ran (server
    # key, always Haiku); only the coach model is user-selectable.
    settings = get_settings()
    resolution = catalog.resolve_coach(
        stored_key=coach_model_key,
        tier=tier_for(principal, settings),
        has_server_key=bool(settings.anthropic_api_key),
        prefers_local=prefers_local(settings),
        has_local=bool(settings.ollama_url),
    )
    if resolution.mode is catalog.CredentialMode.SERVER_KEY:
        coach_provider = _coach_provider_for(request, resolution.entry.model_id)
    elif resolution.mode is catalog.CredentialMode.LOCAL:
        # A selected local model (e.g. qwen-coach) streams from wk-1 Ollama by its catalog model id;
        # entry is None only in local-dev mode, which uses the factory-global Ollama provider.
        coach_provider = (
            _local_coach_provider_for(request, resolution.entry.model_id)
            if resolution.entry is not None
            else _coach_provider(request)
        )
    else:  # LOCKED — selected model unavailable (no server key, or local backend not configured)
        raise HTTPException(status_code=503, detail="coach_model_unavailable")

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
                    db2,
                    session_id=session_id_val,
                    step=evaluated_step,
                    content=reply_text,
                    model=coach_model_key,
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
    _ratelimit(request, "bundle", principal.sub)
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
        coach_model_key = s.coach_model

    grounded = await _grounding_client(request).get_lesson(
        problem_id, include_solution=grounding_context.wants_solution(step_e)
    )
    problem_ctx = grounding_context.build_problem_context(problem_id, grounded, step=step_e)
    # The BYOK client calls Claude with its own key — hand it the session's chosen model id (the
    # key was validated against this tier at create time; fall back to the default if unset/stale).
    coach_entry = catalog.by_key(coach_model_key) or catalog.default_model(catalog.Tier.BYOK)
    return {
        "step": step_e.value,
        "system": byok_orch.build_byok_system(step_e, problem_ctx),
        "messages": [
            {"role": m.role, "step": m.step, "content": m.content, "createdAtEpochMs": _ms(m.created_at)}
            for m in history
            if m.role in ("user", "coach")
        ],
        "model": coach_entry.model_id,
    }


@router.post("/{session_id}/turns/byok-record")
async def record_byok_turn(
    session_id: UUID, principal: CurrentPrincipal, body: dict, request: Request
) -> Response:
    _ratelimit(request, "turn", principal.sub)
    step_raw, answer = body.get("step"), body.get("text")
    coach_reply, verdict_raw = body.get("coachReply"), body.get("verdict")
    if not step_raw or answer is None or coach_reply is None or verdict_raw is None:
        raise HTTPException(status_code=422, detail="step, text, coachReply and verdict are required")
    try:
        step = Step(step_raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid step: {step_raw}") from None
    settings = get_settings()
    code, language, run_result = body.get("code"), body.get("language"), body.get("runResult")
    caps = (
        ("text", answer, settings.coach_max_message_chars),
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
        await check_message_quota(db, principal, settings, s.id)
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
