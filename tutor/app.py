"""The Cortex Tutor FastAPI application.

P0 surface: health/readiness, Prometheus metrics, CORS, and Keycloak JWT parity. The session/turn
routes (gate → coach → SSE) land in later phases; the auth + plumbing here is the foundation they
hang off.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from tutor.auth import CurrentPrincipal
from tutor.config import get_settings
from tutor.persistence.db import make_engine, make_sessionmaker

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
    try:
        yield
    finally:
        await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Cortex Tutor", version="0.1.0", lifespan=lifespan)

    # Bearer (not cookies) → allow_credentials must be False to avoid the wildcard/credentials trap.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        max_age=600,
    )

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
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"db: {exc}"
            ) from exc
        return {"status": "ready"}

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/whoami")
    async def whoami(principal: CurrentPrincipal) -> dict[str, str]:
        # P0 auth-parity smoke; superseded by the session routes in later phases.
        return {"sub": principal.sub, "preferredUsername": principal.preferred_username}

    return app


app = create_app()
