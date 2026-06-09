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

    # ── Anthropic / models ──
    anthropic_api_key: str | None = None
    coach_model: str = "claude-sonnet-4-6"
    gate_model: str = "claude-haiku-4-5"

    # ── Homelab allowlist (CSV). Everyone else uses BYOK. Fails CLOSED. ──
    coach_homelab_users: str = "ani2fun"

    # ── wk-1 Ollama fallback ──
    ollama_url: str | None = None
    ollama_model: str | None = None
    force_local: bool = False

    # ── Postgres (asyncpg driver) ──
    database_url: str = "postgresql+asyncpg://cortex:cortex@localhost:5432/cortex"

    # ── MCP grounding ──
    mcp_url: str = "http://localhost:8081/mcp"
    mcp_service_token: str | None = None

    # ── CORS (CSV of allowed SPA origins) ──
    cors_allow_origins: str = "http://localhost:5173,http://localhost:8080,https://cortex.kakde.eu"

    # ── Guards ──
    coach_max_context_chars: int = 200_000

    @property
    def homelab_users(self) -> set[str]:
        return {u.strip() for u in self.coach_homelab_users.split(",") if u.strip()}

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def jwks_url(self) -> str:
        return f"{self.keycloak_issuer_url.rstrip('/')}/protocol/openid-connect/certs"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
