"""Shared pytest setup — keeps tier-sensitive tests hermetic against the developer's .env."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _hermetic_coach_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The developer's .env sets FORCE_BYOK=true (to exercise the BYOK path under devcombined). That
    # must NOT bleed into tier-sensitive tests, where the synthetic dev principal should ride the
    # HOMELAB tier. Pin the tier-affecting toggles off by default; a test that needs them on sets them
    # explicitly (a later setenv on the same monkeypatch wins). os.environ overrides the .env file.
    monkeypatch.setenv("FORCE_BYOK", "false")
    monkeypatch.setenv("FORCE_LOCAL", "false")
