"""FixedWindowLimiter unit tests (tutor/ratelimit.py)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from tutor.ratelimit import FixedWindowLimiter, make_limiters


def test_allows_up_to_limit_then_429():
    lim = FixedWindowLimiter(limit=2, window_s=60)
    lim.check("u")
    lim.check("u")
    with pytest.raises(HTTPException) as ei:
        lim.check("u")
    assert ei.value.status_code == 429
    assert "Retry-After" in ei.value.headers


def test_keys_are_isolated():
    lim = FixedWindowLimiter(limit=1, window_s=60)
    lim.check("a")
    lim.check("b")  # a different principal is unaffected by a's hits
    with pytest.raises(HTTPException):
        lim.check("a")


def test_make_limiters_has_expected_buckets():
    assert set(make_limiters()) == {"turn", "session", "bundle", "read"}
