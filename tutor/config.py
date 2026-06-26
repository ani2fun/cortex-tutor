"""Application configuration — env / ``.env``, validated at startup (fail-fast)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Auth (Keycloak, shared realm with the cortex Scala server) ──
    auth_enabled: bool = True
    keycloak_issuer_url: str = "https://keycloak.kakde.eu/realms/apps-prod"
    keycloak_client_id: str = "cortex-web"
    # Override for the JWKS *fetch* URL only. Default (None) derives it from the issuer — the PUBLIC
    # Keycloak, which sits behind Cloudflare. Point this at the IN-CLUSTER Keycloak Service (e.g.
    # http://keycloak.identity.svc.cluster.local/realms/apps-prod/protocol/openid-connect/certs) so
    # server-side token validation fetches keys directly and never hairpins out through the public edge,
    # whose bot protection (Browser Integrity Check / Bot Fight Mode) 403s a non-browser client and breaks
    # auth. The `iss` claim is still validated against keycloak_issuer_url (the public URL tokens carry).
    keycloak_jwks_url: str | None = None

    # ── Anthropic / models ──
    anthropic_api_key: str | None = None
    coach_model: str = "claude-sonnet-4-6"
    gate_model: str = "claude-haiku-4-5-20251001"  # verified live; dateless "claude-haiku-4-5" 404s

    # ── Homelab allowlist (CSV). Everyone else uses BYOK. Fails CLOSED. ──
    coach_homelab_users: str = "ani2fun"
    # Dev escape: with auth off everyone is the synthetic homelab principal — set FORCE_BYOK=true
    # to mint BYOK-tier sessions locally and exercise the client-direct path end-to-end.
    force_byok: bool = False

    # ── wk-1 Ollama fallback ──
    ollama_url: str | None = None
    ollama_model: str | None = None
    force_local: bool = False
    ollama_timeout: int = 180  # CPU inference is slow; generous read timeout for the gate call

    # ── Postgres (asyncpg driver) ──
    database_url: str = "postgresql+asyncpg://cortex:cortex@localhost:5432/cortex"

    # ── MCP grounding ──
    mcp_url: str = "http://localhost:8081/mcp"
    mcp_service_token: str | None = None

    # ── CORS (CSV of allowed SPA origins) ──
    cors_allow_origins: str = "http://localhost:5173,http://localhost:8080,https://cortex.kakde.eu"

    # ── Guards ──
    coach_max_context_chars: int = 200_000
    # Largest single persisted message (learner answer or coach reply); also caps the request `text`
    # field. Enforced for BYOK/external users only — homelab users are unrestricted.
    coach_max_message_chars: int = 16_000
    # Hard ceiling on a request body (by Content-Length) — 413 before buffering. Comfortably covers
    # the largest legit turn (code 64k + coachReply 64k + runResult 16k + text 16k + JSON overhead).
    coach_max_request_bytes: int = 512_000

    # ── Per-user storage quotas (BYOK/external tier only; homelab users unlimited) ──
    # A realistic homelab budget so one external account cannot fill Postgres: a bounded number of
    # saved problems, each a bounded conversation. Clearing chats (account menu) frees the budget.
    coach_max_sessions_per_user: int = 25
    coach_max_messages_per_session: int = 120  # ~60 turns (a user + coach row per turn)

    # ── Ephemeral sessions: the tutor is a working store, not the archive. A session is purged after
    # this many hours of INACTIVITY (the window slides on each turn / model switch). Durable "keep this"
    # lives in cortex (POST /api/coach/saved, allow-listed). Generous so a multi-day interview survives.
    coach_session_ttl_hours: int = 48

    @property
    def homelab_users(self) -> set[str]:
        return {u.strip() for u in self.coach_homelab_users.split(",") if u.strip()}

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def jwks_url(self) -> str:
        # Explicit override (the in-cluster Keycloak) wins; else derive from the public issuer.
        if self.keycloak_jwks_url:
            return self.keycloak_jwks_url
        return f"{self.keycloak_issuer_url.rstrip('/')}/protocol/openid-connect/certs"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
