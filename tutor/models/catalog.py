"""The coach model catalog — stable keys → models, per-tier availability, and how a turn's coach
call is funded.

Product model: BYOK users bring their own key and pick any curated model — primarily via
**OpenRouter** (one key, any model, called browser-direct on the user's spend), with **Anthropic
direct** kept as a purist second option. The homelab user (ani2fun) DEFAULTS to the local wk-1 model
— personal, no server spend — but may also pick any cloud model, funded by their own key
(client-direct), exactly like an external user. So cloud entries are selectable by every tier and only
the local entry is **homelab-only**. The gate still runs on the server key for the local submit-turn path;
see ``models/anthropic_provider.py``. A BYOK turn is one combined client-side call — the server
never holds the key and makes no model call (see ``orchestration/byok.py``).

Stable **keys** (not raw model ids) are the public API currency, so bumping a model id never breaks
a stored session or a client.

Pure module — no FastAPI / DB / SDK imports — so the catalog and the resolver are unit-testable
without a running app. The runtime glue (Settings → booleans → provider) lives in the routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Tier(StrEnum):
    HOMELAB = "homelab"  # the operator (ani2fun): coach on the local wk-1 model; gate on the server key
    BYOK = "byok"  # everyone else: own key, any curated model (client-direct, OpenRouter-first)


class Provider(StrEnum):
    ANTHROPIC = "anthropic"  # Claude direct (BYOK purist path) and the homelab gate's server key
    OPENROUTER = (
        "openrouter"  # BYOK aggregator: one key, any model, browser-direct (client-direct, BYOK-only)
    )
    OLLAMA = "ollama"  # the wk-1 local model (homelab-only `qwen-coach`); offered only when OLLAMA_URL is set


class CredentialMode(StrEnum):
    """How a turn's coach call is funded — decided per request from tier + deployment."""

    SERVER_KEY = "server_key"  # homelab, Anthropic mode: the server's ANTHROPIC_API_KEY
    CLIENT_DIRECT = "client_direct"  # byok: the user's key, used client-side (server never holds it)
    LOCAL = "local"  # local-dev Ollama fallback (the factory-global provider)
    LOCKED = "locked"  # fail closed: a Claude model chosen with no resolvable key


@dataclass(frozen=True)
class CatalogEntry:
    key: str  # stable public id (what clients send / we store on the session)
    provider: Provider
    model_id: str  # the provider-specific model string (server-internal)
    allowed_tiers: frozenset[Tier]
    display: str


# Cloud models are selectable by EVERYONE. The operator (homelab) gets them too — but a cloud pick is
# funded by the operator's OWN key (client-direct), exactly like an external BYOK user, so it never
# spends the server key. Only the local wk-1 model is homelab-exclusive. The transport/funding for a
# turn follows the chosen model's PROVIDER (see the session `byok` flag), not the tier.
_CLOUD_TIERS = frozenset({Tier.BYOK, Tier.HOMELAB})
_HOMELAB_ONLY = frozenset({Tier.HOMELAB})

# BYOK is the primary path: the user brings ONE key and picks any curated model. The default and
# most of the list go through **OpenRouter** (provider=OPENROUTER) — an OpenAI-compatible aggregator
# the browser calls directly on the user's key; `model_id` is the OpenRouter model slug (verified
# live). Curated to function-calling-capable models only: a BYOK turn is one combined gate+coach
# call, and the gate half needs reliable structured output. **Anthropic direct** (provider=ANTHROPIC)
# stays as a purist second option (no middleman). All cloud entries are BYOK-only; the local wk-1
# model is homelab-only and only surfaces when OLLAMA_URL is set (the `has_local` gate below).
_CATALOG: dict[str, CatalogEntry] = {
    # ── OpenRouter (BYOK primary): one key, any model, browser-direct. ────────────────────────────
    "or-claude-sonnet": CatalogEntry(
        key="or-claude-sonnet",
        provider=Provider.OPENROUTER,
        model_id="anthropic/claude-sonnet-4.6",
        allowed_tiers=_CLOUD_TIERS,
        display="Claude Sonnet 4.6 (OpenRouter)",
    ),
    "or-gpt-4.1": CatalogEntry(
        key="or-gpt-4.1",
        provider=Provider.OPENROUTER,
        model_id="openai/gpt-4.1",
        allowed_tiers=_CLOUD_TIERS,
        display="GPT-4.1 (OpenRouter)",
    ),
    "or-gemini-flash": CatalogEntry(
        key="or-gemini-flash",
        provider=Provider.OPENROUTER,
        model_id="google/gemini-2.5-flash",
        allowed_tiers=_CLOUD_TIERS,
        display="Gemini 2.5 Flash (OpenRouter)",
    ),
    "or-deepseek": CatalogEntry(
        key="or-deepseek",
        provider=Provider.OPENROUTER,
        model_id="deepseek/deepseek-chat-v3.1",
        allowed_tiers=_CLOUD_TIERS,
        display="DeepSeek V3.1 (OpenRouter)",
    ),
    "or-llama-70b": CatalogEntry(
        key="or-llama-70b",
        provider=Provider.OPENROUTER,
        model_id="meta-llama/llama-3.3-70b-instruct",
        allowed_tiers=_CLOUD_TIERS,
        display="Llama 3.3 70B (OpenRouter)",
    ),
    # ── Anthropic direct (BYOK purist path): the user's own Anthropic key, no aggregator. ────────
    "claude-sonnet": CatalogEntry(
        key="claude-sonnet",
        provider=Provider.ANTHROPIC,
        model_id="claude-sonnet-4-6",
        allowed_tiers=_CLOUD_TIERS,
        display="Claude Sonnet 4.6 (direct)",
    ),
    "claude-haiku": CatalogEntry(
        key="claude-haiku",
        provider=Provider.ANTHROPIC,
        # Dated id — the dateless "claude-haiku-4-5" 404s (see config.py). The coach provider sends
        # no effort/thinking, so Haiku is a safe coach model with zero per-model branching.
        model_id="claude-haiku-4-5-20251001",
        allowed_tiers=_CLOUD_TIERS,
        display="Claude Haiku 4.5 (direct)",
    ),
    # ── Local (homelab only): the operator's wk-1 model; only when OLLAMA_URL is set. ────────────
    "qwen-coach": CatalogEntry(
        key="qwen-coach",
        provider=Provider.OLLAMA,
        model_id="socratic-coach",  # the deployed Ollama model (a Modelfile over qwen2.5-coder:7b)
        # The operator's personal model: the server streams it from wk-1 Ollama for the homelab
        # principal. Only surfaces when OLLAMA_URL is set (the `has_local` gate below).
        allowed_tiers=_HOMELAB_ONLY,
        display="Qwen Coach (local)",
    ),
}

# Per-tier default coach model: homelab → the local wk-1 model; byok → Claude Sonnet via OpenRouter
# (the primary BYOK path — one key, any model).
_DEFAULT_BY_TIER: dict[Tier, str] = {Tier.HOMELAB: "qwen-coach", Tier.BYOK: "or-claude-sonnet"}


class ModelNotAllowed(ValueError):
    """A client asked for a model not in the catalog, or not allowed for its tier."""


def by_key(key: str | None) -> CatalogEntry | None:
    return _CATALOG.get(key) if key else None


def available_models(tier: Tier, *, has_local: bool = False) -> list[CatalogEntry]:
    """The catalog entries a caller in ``tier`` may choose, in catalog (default-first) order.

    Local (Ollama) entries are hidden unless ``has_local`` — they're only usable when the wk-1
    backend is configured (OLLAMA_URL), so we never offer a model that would fail at turn time.
    """
    return [
        e
        for e in _CATALOG.values()
        if tier in e.allowed_tiers and (has_local or e.provider is not Provider.OLLAMA)
    ]


def default_model(tier: Tier) -> CatalogEntry:
    """The default coach model: homelab → local wk-1 model, byok → Claude Sonnet (OpenRouter)."""
    entry = _CATALOG[_DEFAULT_BY_TIER[tier]]
    assert tier in entry.allowed_tiers, f"default for tier {tier} not allowed for that tier"
    return entry


def validate_choice(requested: str | None, tier: Tier, *, has_local: bool = False) -> CatalogEntry:
    """Resolve a client-supplied key to an entry, FAILING CLOSED.

    ``None`` → the tier default. An unknown key, one not allowed for ``tier``, or a local (Ollama)
    model when the backend isn't configured (``has_local`` false), raises ``ModelNotAllowed`` —
    never trust a client model id; always check the tier allow-list (and the backend) here.
    """
    if requested is None:
        return default_model(tier)
    entry = _CATALOG.get(requested)
    if entry is None or tier not in entry.allowed_tiers:
        raise ModelNotAllowed(requested)
    if entry.provider is Provider.OLLAMA and not has_local:
        raise ModelNotAllowed(requested)
    return entry


@dataclass(frozen=True)
class CoachResolution:
    """The chosen coach entry plus how its call is funded for this turn."""

    entry: CatalogEntry | None  # None only for the local-dev LOCAL fallback; set otherwise
    mode: CredentialMode


def _entry_for_tier(stored_key: str | None, tier: Tier) -> CatalogEntry:
    """The stored key as a tier-ALLOWED entry, degrading to the tier default when the key is unknown
    or no longer allowed for this tier — so a homelab session pinned to a Claude model (e.g. created
    before the tier model changed) falls back to the local default rather than spending the server
    key. ``reset`` re-pins a valid key the same way."""
    entry = by_key(stored_key)
    if entry is None or tier not in entry.allowed_tiers:
        return default_model(tier)
    return entry


def resolve_coach(
    *,
    stored_key: str | None,
    tier: Tier,
    has_server_key: bool,
    prefers_local: bool,
    has_local: bool = False,
) -> CoachResolution:
    """Decide which coach model + credential mode a turn uses. Pure (booleans, not Settings)."""
    if tier is Tier.BYOK:
        # Client-direct: the server never holds the key. We still resolve the entry so the
        # prompt-bundle can hand the chosen model id to the client.
        return CoachResolution(_entry_for_tier(stored_key, tier), CredentialMode.CLIENT_DIRECT)
    # Homelab: the coach is the local entry, resolved BEFORE the dev `prefers_local` flag, so it
    # always uses the tuned socratic-coach (via its model id) rather than the factory-global dev
    # model. The server streams it from wk-1 Ollama — only when that backend is configured.
    entry = _entry_for_tier(stored_key, tier)
    if entry.provider is Provider.OLLAMA:
        return CoachResolution(entry, CredentialMode.LOCAL if has_local else CredentialMode.LOCKED)
    # Non-local homelab entry (not expected given the catalog): dev-local fallback, then server key.
    if prefers_local:
        return CoachResolution(None, CredentialMode.LOCAL)
    if entry.provider is Provider.ANTHROPIC and has_server_key:
        return CoachResolution(entry, CredentialMode.SERVER_KEY)
    return CoachResolution(entry, CredentialMode.LOCKED)
