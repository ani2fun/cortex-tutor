"""Coach model catalog — tier availability, fail-closed validation, and credential resolution.

Product model: cloud models (OpenRouter + Anthropic-direct) are selectable by EVERY tier — the
operator funds a cloud pick with their own key (client-direct), like an external user — while the
local wk-1 `qwen-coach` is homelab-only and gated on the Ollama backend (has_local). Default coach:
homelab → the local model, byok → Claude Sonnet via OpenRouter (the primary BYOK path).
"""

from __future__ import annotations

import pytest
from tutor.models import catalog
from tutor.models.catalog import CredentialMode, ModelNotAllowed, Provider, Tier

# ── catalog shape ───────────────────────────────────────────────────────────────────────────


def test_byok_catalog_is_openrouter_first_then_anthropic_direct():
    # BYOK users pick any curated model: OpenRouter entries (primary) then Anthropic-direct (purist).
    # Under dual-mode the operator (homelab) can pick these too — funded by their own key.
    assert [e.key for e in catalog.available_models(Tier.BYOK)] == [
        "or-claude-sonnet",
        "or-gpt-4.1",
        "or-gemini-flash",
        "or-deepseek",
        "or-llama-70b",
        "claude-sonnet",
        "claude-haiku",
    ]
    # Anthropic-direct entries (the purist path) are unchanged.
    sonnet, haiku = catalog.by_key("claude-sonnet"), catalog.by_key("claude-haiku")
    assert (sonnet.provider, sonnet.model_id) == (Provider.ANTHROPIC, "claude-sonnet-4-6")
    assert (haiku.provider, haiku.model_id) == (Provider.ANTHROPIC, "claude-haiku-4-5-20251001")
    assert sonnet.allowed_tiers == frozenset({Tier.BYOK, Tier.HOMELAB})


def test_openrouter_entries_selectable_all_tiers_with_live_slugs():
    # The curated OpenRouter set: provider=OPENROUTER, selectable by every tier, model_id = live slug.
    expected = {
        "or-claude-sonnet": "anthropic/claude-sonnet-4.6",
        "or-gpt-4.1": "openai/gpt-4.1",
        "or-gemini-flash": "google/gemini-2.5-flash",
        "or-deepseek": "deepseek/deepseek-chat-v3.1",
        "or-llama-70b": "meta-llama/llama-3.3-70b-instruct",
    }
    for key, model_id in expected.items():
        e = catalog.by_key(key)
        assert e is not None, key
        assert (e.provider, e.model_id) == (Provider.OPENROUTER, model_id)
        assert e.allowed_tiers == frozenset({Tier.BYOK, Tier.HOMELAB})


def test_qwen_is_local_homelab_only():
    q = catalog.by_key("qwen-coach")
    assert (q.provider, q.model_id) == (Provider.OLLAMA, "socratic-coach")
    assert q.allowed_tiers == frozenset({Tier.HOMELAB})


def test_homelab_sees_local_plus_cloud_under_dual_mode():
    # Dual-mode: the operator gets the local model (when the backend is up) PLUS every cloud model
    # (funded by their own key). With no backend, only the local entry drops out.
    with_backend = {e.key for e in catalog.available_models(Tier.HOMELAB, has_local=True)}
    assert "qwen-coach" in with_backend
    assert {"or-claude-sonnet", "claude-sonnet"} <= with_backend
    no_backend = {e.key for e in catalog.available_models(Tier.HOMELAB, has_local=False)}
    assert "qwen-coach" not in no_backend  # local gated on OLLAMA_URL
    assert {"or-claude-sonnet", "claude-sonnet"} <= no_backend  # cloud remains
    # BYOK never gets the local model, even with a backend; its cloud list is backend-independent.
    byok = [e.key for e in catalog.available_models(Tier.BYOK, has_local=True)]
    assert "qwen-coach" not in byok
    assert byok == [e.key for e in catalog.available_models(Tier.BYOK)]


def test_default_is_local_for_homelab_and_openrouter_gemini_for_byok():
    assert catalog.default_model(Tier.HOMELAB).key == "qwen-coach"
    assert catalog.default_model(Tier.BYOK).key == "or-gemini-flash"


# ── validate_choice: fail closed ─────────────────────────────────────────────────────────────


def test_validate_choice_none_returns_tier_default():
    assert catalog.validate_choice(None, Tier.BYOK).key == "or-gemini-flash"
    assert catalog.validate_choice(None, Tier.HOMELAB, has_local=True).key == "qwen-coach"


def test_validate_choice_accepts_in_tier_known_key():
    assert catalog.validate_choice("claude-haiku", Tier.BYOK).key == "claude-haiku"
    # OpenRouter entries are NOT backend-gated (unlike OLLAMA) — no has_local needed.
    assert catalog.validate_choice("or-gpt-4.1", Tier.BYOK).key == "or-gpt-4.1"
    assert catalog.validate_choice("qwen-coach", Tier.HOMELAB, has_local=True).key == "qwen-coach"


@pytest.mark.parametrize("bad", ["gpt-4", "claude-opus", "", "claude-sonnet-4-6"])
def test_validate_choice_rejects_unknown_or_raw_id_for_homelab(bad):
    # Unknown keys / raw model ids fail closed for everyone. (Cloud catalog KEYS are now valid for
    # homelab too — see the dual-mode test below — so only genuinely unknown ids are rejected here.)
    with pytest.raises(ModelNotAllowed):
        catalog.validate_choice(bad, Tier.HOMELAB, has_local=True)


def test_validate_choice_accepts_cloud_for_homelab_under_dual_mode():
    # The operator may select any cloud model; the route derives byok=true (client-direct, own key).
    assert catalog.validate_choice("or-gpt-4.1", Tier.HOMELAB, has_local=True).key == "or-gpt-4.1"
    assert catalog.validate_choice("claude-sonnet", Tier.HOMELAB).key == "claude-sonnet"


def test_validate_choice_local_requires_backend_and_homelab():
    assert catalog.validate_choice("qwen-coach", Tier.HOMELAB, has_local=True).key == "qwen-coach"
    with pytest.raises(ModelNotAllowed):  # backend not configured → fail closed
        catalog.validate_choice("qwen-coach", Tier.HOMELAB, has_local=False)
    with pytest.raises(ModelNotAllowed):  # wrong tier (BYOK can't use the internal local model)
        catalog.validate_choice("qwen-coach", Tier.BYOK, has_local=True)


# ── resolve_coach: credential mode per tier + deployment ──────────────────────────────────────


def _resolve(tier, *, key=None, server_key=True, local=False, has_local=False):
    return catalog.resolve_coach(
        stored_key=key, tier=tier, has_server_key=server_key, prefers_local=local, has_local=has_local
    )


def test_resolve_byok_is_client_direct():
    res = _resolve(Tier.BYOK, key="claude-haiku", server_key=False)
    assert res.mode is CredentialMode.CLIENT_DIRECT
    assert res.entry.model_id == "claude-haiku-4-5-20251001"


def test_resolve_byok_openrouter_is_client_direct_with_slug():
    # An OpenRouter pick is client-direct too; the resolved model_id is the OpenRouter slug the
    # prompt-bundle hands the browser. The server holds no key and makes no call.
    res = _resolve(Tier.BYOK, key="or-gemini-flash", server_key=False)
    assert res.mode is CredentialMode.CLIENT_DIRECT
    assert (res.entry.key, res.entry.model_id) == ("or-gemini-flash", "google/gemini-2.5-flash")


def test_resolve_byok_none_key_uses_openrouter_gemini_default():
    res = _resolve(Tier.BYOK, key=None, server_key=False)
    assert (res.entry.key, res.mode) == ("or-gemini-flash", CredentialMode.CLIENT_DIRECT)
    assert res.entry.model_id == "google/gemini-2.5-flash"  # the OpenRouter slug handed to the client


def test_resolve_homelab_selected_local_streams_when_configured():
    res = _resolve(Tier.HOMELAB, key="qwen-coach", has_local=True)
    assert res.mode is CredentialMode.LOCAL
    assert (res.entry.key, res.entry.model_id) == ("qwen-coach", "socratic-coach")


def test_resolve_homelab_locks_without_backend():
    res = _resolve(Tier.HOMELAB, key="qwen-coach", has_local=False)
    assert res.mode is CredentialMode.LOCKED
    assert res.entry.key == "qwen-coach"  # entry preserved for the 503 / UX


def test_resolve_homelab_uses_local_entry_even_under_prefers_local():
    # The dev prefers_local flag no longer overrides the coach model — homelab always resolves to the
    # tuned local entry (socratic-coach), not the factory-global dev model.
    res = _resolve(Tier.HOMELAB, key=None, server_key=False, local=True, has_local=True)
    assert res.mode is CredentialMode.LOCAL
    assert res.entry.key == "qwen-coach"


def test_resolve_homelab_degrades_vanished_key_to_local():
    # resolve_coach runs only on the server (local) turn path — reached only by byok=false sessions,
    # whose model is always the local one. An unknown/retired key still degrades to the local default
    # (never a server-key spend). A homelab CLOUD pick is byok=true → client-direct, so it never
    # reaches resolve_coach at all.
    for stale in ("retired-model", "also-gone"):
        res = _resolve(Tier.HOMELAB, key=stale, server_key=True, has_local=True)
        assert (res.entry.key, res.mode) == ("qwen-coach", CredentialMode.LOCAL)
