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
    assert r.json() == {"sub": "dev", "preferredUsername": "dev"}


def test_whoami_requires_bearer_when_auth_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with _client("true") as c:
        assert c.get("/v1/whoami").status_code == 401  # no bearer, no JWKS call
    get_settings.cache_clear()
