"""Select the concrete gate/coach providers from config.

Ollama-first for keyless local dev; Claude when a server key is set (the homelab/allowlist tier);
a clear error if neither is configured. BYOK is client-direct, so it has no server-side provider.
Keys are read from settings (env) — never hardcoded.
"""

from __future__ import annotations

from tutor.config import Settings
from tutor.models.anthropic_provider import AnthropicCoachProvider, AnthropicGateProvider
from tutor.models.base import CoachProvider, GateProvider
from tutor.models.ollama_provider import OllamaCoachProvider, OllamaGateProvider

_DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:7b"  # 7b grades more reliably than a 3b; 3b is faster on CPU


def prefers_local(settings: Settings) -> bool:
    """True when gate/coach should use the wk-1 Ollama backend instead of Claude — local-dev mode:
    ``FORCE_LOCAL``, or no Anthropic key with an Ollama URL set. The session routes reuse this so
    the per-session Claude coach is skipped (and the Ollama fallback kept) in local dev."""
    return settings.force_local or (not settings.anthropic_api_key and bool(settings.ollama_url))


def _ollama_model(settings: Settings) -> str:
    return settings.ollama_model or _DEFAULT_OLLAMA_MODEL


def make_gate_provider(settings: Settings) -> GateProvider:
    if prefers_local(settings) and settings.ollama_url:
        return OllamaGateProvider(
            settings.ollama_url, _ollama_model(settings), timeout=settings.ollama_timeout
        )
    if settings.anthropic_api_key:
        return AnthropicGateProvider(settings.anthropic_api_key, settings.gate_model)
    if settings.ollama_url:
        return OllamaGateProvider(
            settings.ollama_url, _ollama_model(settings), timeout=settings.ollama_timeout
        )
    raise RuntimeError("No gate provider configured: set OLLAMA_URL (local dev) or ANTHROPIC_API_KEY.")


def make_coach_provider(settings: Settings) -> CoachProvider:
    if prefers_local(settings) and settings.ollama_url:
        return OllamaCoachProvider(
            settings.ollama_url, _ollama_model(settings), timeout=settings.ollama_timeout
        )
    if settings.anthropic_api_key:
        return AnthropicCoachProvider(settings.anthropic_api_key, settings.coach_model)
    if settings.ollama_url:
        return OllamaCoachProvider(
            settings.ollama_url, _ollama_model(settings), timeout=settings.ollama_timeout
        )
    raise RuntimeError("No coach provider configured: set OLLAMA_URL (local dev) or ANTHROPIC_API_KEY.")
