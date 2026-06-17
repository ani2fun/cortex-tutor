"""The Cortex Tutor FastAPI application.

P0 surface: health/readiness, Prometheus metrics, CORS, and Keycloak JWT parity. The session/turn
routes (gate → coach → SSE) land in later phases; the auth + plumbing here is the foundation they
hang off.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from tutor.auth import CurrentPrincipal, tier_for
from tutor.config import get_settings
from tutor.models import catalog
from tutor.persistence.db import make_engine, make_sessionmaker
from tutor.persistence.purge import purge_expired
from tutor.ratelimit import make_limiters
from tutor.routes import sessions as sessions_routes

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    log.info(
        "tutor.startup",
        auth_enabled=settings.auth_enabled,
        homelab_users=sorted(settings.homelab_users),
        coach_model=settings.coach_model,
        gate_model=settings.gate_model,
    )

    # Ephemeral sessions: sweep expired (idle) sessions on startup, then hourly. Single-replica app,
    # so an in-process task suffices — no CronJob. Durable saves live in cortex and are untouched.
    async def _purge_loop() -> None:
        while True:
            try:
                removed = await purge_expired(app.state.sessionmaker)
                if removed:
                    log.info("tutor.session_purge", removed=removed)
            except Exception:
                log.exception("tutor.session_purge_failed")
            await asyncio.sleep(3600)

    purge_task = asyncio.create_task(_purge_loop())
    try:
        yield
    finally:
        purge_task.cancel()
        await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Cortex Tutor", version="0.1.0", lifespan=lifespan)
    # Per-principal rate limiters (in-memory; single-replica). See tutor/ratelimit.py.
    app.state.limiters = make_limiters()

    # Bearer (not cookies) → allow_credentials must be False to avoid the wildcard/credentials trap.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        max_age=600,
    )

    @app.middleware("http")
    async def _limit_body_size(request, call_next):
        # Reject an oversize body by its declared Content-Length BEFORE the route buffers it — a cheap
        # DoS guard. Chunked/no-length bodies are bounded upstream by the cortex edge request cap.
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                too_big = int(content_length) > settings.coach_max_request_bytes
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "invalid Content-Length"})
            if too_big:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"request body exceeds {settings.coach_max_request_bytes} bytes"},
                )
        return await call_next(request)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        # DB reachable? (JWKS + MCP readiness join later; Anthropic is a metric, never a gate.)
        try:
            async with app.state.sessionmaker() as db:
                await db.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"db: {exc}") from exc
        return {"status": "ready"}

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/whoami")
    async def whoami(principal: CurrentPrincipal, request: Request) -> dict:
        # Identity + the caller's coach tier and the models they may pick. The SPA renders the
        # picker from availableModels/defaultModel; create-session re-validates the choice server-side.
        request.app.state.limiters["read"].check(principal.sub)  # cheap, but bound a hammering script
        settings = get_settings()
        tier = tier_for(principal, settings)
        return {
            "sub": principal.sub,
            "preferredUsername": principal.preferred_username,
            "tier": tier.value,
            "defaultModel": catalog.default_model(tier).key,
            "availableModels": [
                {"key": e.key, "display": e.display, "provider": e.provider.value}
                for e in catalog.available_models(tier, has_local=bool(settings.ollama_url))
            ],
        }

    app.include_router(sessions_routes.router)
    return app


app = create_app()
