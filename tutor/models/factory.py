"""Select the concrete gate provider from config.

Ollama-first for keyless local dev; Claude when a server key is set (the homelab/allowlist tier);
a clear error if neither is configured. BYOK is client-direct, so it has no server-side provider.
Keys are read from settings (env) — never hardcoded.
"""

from __future__ import annotations

from tutor.config import Settings
from tutor.models.anthropic_provider import AnthropicGateProvider
from tutor.models.base import GateProvider
from tutor.models.ollama_provider import OllamaGateProvider

_DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:7b"  # stronger JSON adherence than Gemma for the gate


def make_gate_provider(settings: Settings) -> GateProvider:
    prefer_ollama = settings.force_local or (not settings.anthropic_api_key and bool(settings.ollama_url))
    model = settings.ollama_model or _DEFAULT_OLLAMA_MODEL

    if prefer_ollama and settings.ollama_url:
        return OllamaGateProvider(settings.ollama_url, model, timeout=settings.ollama_timeout)
    if settings.anthropic_api_key:
        return AnthropicGateProvider(settings.anthropic_api_key, settings.gate_model)
    if settings.ollama_url:
        return OllamaGateProvider(settings.ollama_url, model, timeout=settings.ollama_timeout)
    raise RuntimeError("No gate provider configured: set OLLAMA_URL (local dev) or ANTHROPIC_API_KEY.")
