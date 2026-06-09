"""Configuration for the cortex-grounding-mcp server (env / ``.env``, validated at import).

A separate service from the tutor, so it owns its own settings — but ``MCP_SERVICE_TOKEN`` is the
*shared* secret (the tutor presents it; this server verifies it), so that key is unprefixed on both
sides. In dev, point ``CORTEX_CONTENT_DIR`` at the cortex repo's content dir; in prod the corpus is
baked into the image.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class GroundingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Corpus location. Dev: the cortex repo's content dir; prod: baked into the image.
    cortex_content_dir: str = "content/cortex"
    # Public base for chapter citation URLs (matches the cortex frontend route).
    cortex_public_base: str = "https://cortex.kakde.eu"

    # Service-identity bearer the tutor presents (confused-deputy fix). None → dev/open (no auth).
    mcp_service_token: str | None = None

    # Bind.
    grounding_host: str = "0.0.0.0"  # ClusterIP service, fronted by the mesh
    grounding_port: int = 8081

    # Server-side payload caps (~4 chars/token; keeps every tool result well under ~25k tokens).
    max_search_results: int = 8
    max_snippet_chars: int = 480
    max_body_chars: int = 40_000
    max_outline_chars: int = 20_000
    max_related: int = 6

    @property
    def content_path(self) -> Path:
        return Path(self.cortex_content_dir).expanduser()

    def citation_url(self, problem_id: str) -> str:
        return f"{self.cortex_public_base.rstrip('/')}/{problem_id}"


@lru_cache(maxsize=1)
def get_settings() -> GroundingSettings:
    return GroundingSettings()
