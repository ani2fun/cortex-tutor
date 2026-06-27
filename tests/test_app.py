"""P0 smoke for the FastAPI app: health, metrics, and Keycloak auth parity (dev bypass + 401)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient
from tutor.config import get_settings


def _client(auth_enabled: str) -> TestClient:
    get_settings.cache_clear()
    from tutor.app import create_app

    return TestClient(create_app())


@pytest.fixture
def dev_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    # The dev-bypass principal is homelab, whose only coach model is the local one — so pin a backend
    # URL (deterministic regardless of a dev .env) so the model list isn't empty. The no-backend
    # view has its own test below.
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test:11434")
    with _client("false") as c:
        yield c
    get_settings.cache_clear()


def test_healthz(dev_client: TestClient):
    r = dev_client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_metrics_exposes_prometheus(dev_client: TestClient):
    r = dev_client.get("/metrics")
    assert r.status_code == 200
    assert "# HELP" in r.text


def test_whoami_dev_bypass(dev_client: TestClient):
    r = dev_client.get("/v1/whoami")
    assert r.status_code == 200
    body = r.json()
    assert (body["sub"], body["preferredUsername"]) == ("dev", "dev")
    assert body["tier"] == "homelab"  # auth off (+ no FORCE_BYOK) → dev principal rides homelab
    # Dual-mode: the operator defaults to the local model but may ALSO pick any cloud model (funded
    # by their own key). With the dev backend configured, the list is local + every cloud model.
    assert body["defaultModel"] == "qwen-coach"
    keys = {m["key"] for m in body["availableModels"]}
    assert "qwen-coach" in keys
    assert {"or-claude-sonnet", "or-gpt-4.1", "or-gemini-flash", "or-deepseek",
            "or-llama-70b", "claude-sonnet", "claude-haiku"} <= keys
    assert {m["provider"] for m in body["availableModels"]} == {"ollama", "openrouter", "anthropic"}


def test_whoami_homelab_without_backend_still_has_cloud_models(monkeypatch: pytest.MonkeyPatch):
    # Dual-mode: with no OLLAMA_URL the local model drops out, but the operator can still pick any
    # cloud model (their own key) — so the list is the cloud set, not empty.
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OLLAMA_URL", "")
    with _client("false") as c:
        body = c.get("/v1/whoami").json()
    get_settings.cache_clear()
    assert body["tier"] == "homelab"
    keys = {m["key"] for m in body["availableModels"]}
    assert "qwen-coach" not in keys  # local hidden without a backend
    assert {"or-claude-sonnet", "claude-sonnet"} <= keys  # cloud remains


def test_whoami_byok_tier_lists_models(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("FORCE_BYOK", "true")
    with _client("false") as c:
        body = c.get("/v1/whoami").json()
    get_settings.cache_clear()
    assert body["tier"] == "byok"
    assert body["defaultModel"] == "or-glm-5.2"  # the OpenRouter BYOK default (GLM 5.2)
    assert {m["key"] for m in body["availableModels"]} == {
        "or-glm-5.2",
        "or-claude-sonnet",
        "or-gpt-4.1",
        "or-gemini-flash",
        "or-deepseek",
        "or-llama-70b",
        "claude-sonnet",
        "claude-haiku",
    }
    # Every BYOK model is a cloud provider needing the user's own key — never the local server model.
    assert {m["provider"] for m in body["availableModels"]} == {"openrouter", "anthropic"}


def test_whoami_requires_bearer_when_auth_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with _client("true") as c:
        assert c.get("/v1/whoami").status_code == 401  # no bearer, no JWKS call
    get_settings.cache_clear()


def test_whoami_jwks_unreachable_is_503_not_500(monkeypatch: pytest.MonkeyPatch):
    # Regression (prod incident): when Keycloak's JWKS can't be resolved — endpoint unreachable from
    # the pod, or the key id isn't published — PyJWT raises PyJWKClientError. It must surface as a
    # clean 503, NOT a bare 500: the SPA reads any non-401 whoami failure as "tutor unavailable" and
    # silently degrades the Coach to its static fallback for EVERY signed-in user.
    from jwt.exceptions import PyJWKClientError

    monkeypatch.setenv("AUTH_ENABLED", "true")

    class _BoomClient:
        def get_signing_key_from_jwt(self, token: str):
            raise PyJWKClientError("Unable to fetch/find a signing key")

    import tutor.auth as auth

    monkeypatch.setattr(auth, "_jwks_client", lambda url: _BoomClient())
    with _client("true") as c:
        r = c.get("/v1/whoami", headers={"Authorization": "Bearer dummy.jwt.token"})
    get_settings.cache_clear()
    assert r.status_code == 503


def test_jwks_url_override_bypasses_public_issuer(monkeypatch: pytest.MonkeyPatch):
    # KEYCLOAK_JWKS_URL points the *fetch* at the in-cluster Keycloak (bypassing the Cloudflare-fronted
    # public edge), while the iss check stays on the public issuer. Default (unset) derives from issuer.
    internal = "http://keycloak.identity.svc.cluster.local/realms/apps-prod/protocol/openid-connect/certs"
    get_settings.cache_clear()
    monkeypatch.setenv("KEYCLOAK_ISSUER_URL", "https://keycloak.kakde.eu/realms/apps-prod")

    monkeypatch.delenv("KEYCLOAK_JWKS_URL", raising=False)
    assert get_settings().jwks_url == "https://keycloak.kakde.eu/realms/apps-prod/protocol/openid-connect/certs"

    get_settings.cache_clear()
    monkeypatch.setenv("KEYCLOAK_JWKS_URL", internal)
    s = get_settings()
    assert s.jwks_url == internal  # fetch goes internal …
    assert s.keycloak_issuer_url == "https://keycloak.kakde.eu/realms/apps-prod"  # … iss check unchanged
    get_settings.cache_clear()


@pytest.mark.parametrize("bad_model", ["gpt-4", "claude-opus", "does-not-exist"])
def test_create_session_rejects_unknown_model(dev_client: TestClient, bad_model: str):
    # Never trust a client-supplied model id: unknown keys fail closed BEFORE any DB work (so this
    # runs without Postgres). Under dual-mode the homelab operator CAN pick cloud keys, so only
    # genuinely unknown ids are rejected here.
    r = dev_client.post("/v1/sessions", json={"problemId": "itest/x", "model": bad_model})
    assert r.status_code == 422


def test_create_session_rejects_homelab_only_model_for_byok(monkeypatch: pytest.MonkeyPatch):
    # The local model is homelab-exclusive — a BYOK user can't select it (fail closed, no DB).
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("FORCE_BYOK", "true")
    with _client("false") as c:
        r = c.post("/v1/sessions", json={"problemId": "itest/x", "model": "qwen-coach"})
    get_settings.cache_clear()
    assert r.status_code == 422


def test_create_session_rate_limited(dev_client: TestClient):
    # The "session" bucket is 15/min per principal. A bad body 422s but still counts against the
    # limiter (it runs first), so the 16th call from the same principal is 429 — no DB needed.
    for _ in range(15):
        assert dev_client.post("/v1/sessions", json={}).status_code == 422
    r = dev_client.post("/v1/sessions", json={})
    assert r.status_code == 429
    assert "Retry-After" in r.headers
