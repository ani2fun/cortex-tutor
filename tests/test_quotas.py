"""Per-user storage quota logic — DB-free: the repo counts are monkeypatched, so these assert the
identity-gating (operator exempt / capped user limited) and the 429 surface, not Postgres."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from tutor import quotas
from tutor.auth import Principal
from tutor.config import Settings


def _settings(**over: object) -> Settings:
    # Explicit kwargs override any ambient .env so the test is hermetic.
    base: dict[str, object] = dict(
        auth_enabled=True,
        force_byok=False,
        coach_homelab_users="ani2fun",
        coach_max_sessions_per_user=2,
        coach_max_messages_per_session=120,
    )
    base.update(over)
    return Settings(**base)


_OPERATOR = Principal(sub="op", preferred_username="ani2fun")  # homelab tier → exempt
_EXTERNAL = Principal(sub="ext", preferred_username="someone-else")  # byok tier → capped


async def test_session_quota_exempts_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def _count(*_a: object, **_k: object) -> int:
        nonlocal called
        called = True
        return 999

    monkeypatch.setattr(quotas.repo, "count_sessions_for_user", _count)
    await quotas.check_session_quota(None, _OPERATOR, _settings())  # no raise
    assert not called  # short-circuited before any DB count


async def test_session_quota_caps_external_user(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _count(*_a: object, **_k: object) -> int:
        return 2  # at the cap

    monkeypatch.setattr(quotas.repo, "count_sessions_for_user", _count)
    with pytest.raises(HTTPException) as ei:
        await quotas.check_session_quota(None, _EXTERNAL, _settings(coach_max_sessions_per_user=2))
    assert ei.value.status_code == 429
    assert "quota_exceeded" in ei.value.detail


async def test_message_quota_exempts_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    # The operator stays uncapped even on a cloud (byok) session — exemption is by identity.
    called = False

    async def _next(*_a: object, **_k: object) -> int:
        nonlocal called
        called = True
        return 999

    monkeypatch.setattr(quotas.repo, "next_seq", _next)
    await quotas.check_message_quota(None, _OPERATOR, _settings(), "sid")  # no raise
    assert not called


async def test_message_quota_caps_external_user(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _next(*_a: object, **_k: object) -> int:
        return 121  # one past a 120 cap

    monkeypatch.setattr(quotas.repo, "next_seq", _next)
    settings = _settings(coach_max_messages_per_session=120)
    with pytest.raises(HTTPException) as ei:
        await quotas.check_message_quota(None, _EXTERNAL, settings, "sid")
    assert ei.value.status_code == 429
    assert "quota_exceeded" in ei.value.detail
