"""In-process per-principal rate limiting.

cortex-tutor is single-replica, so an in-memory fixed-window counter per (principal, bucket) is
enough and needs no Redis. This is NOT a DDoS defense (that's the edge / the cortex proxy) — it
caps an AUTHENTICATED abuser from hammering the tutor's own resources (DB writes, the grounding MCP,
session churn) far beyond any human learner's pace. A BYOK turn makes no server model call, so the
cost of abuse is DB/CPU, not model spend — but it's still worth bounding, and it protects the
homelab (ani2fun) turn path, which DOES spend the gate model.
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import HTTPException


class FixedWindowLimiter:
    """A per-key fixed-window counter. ``check(key)`` raises 429 (with Retry-After) once ``key``
    exceeds ``limit`` hits within ``window_s`` seconds. Monotonic clock — immune to wall-clock jumps."""

    __slots__ = ("_hits", "_limit", "_window")

    def __init__(self, limit: int, window_s: float) -> None:
        self._limit = limit
        self._window = window_s
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        now = time.monotonic()
        cutoff = now - self._window
        hits = self._hits[key]
        hits[:] = [t for t in hits if t > cutoff]  # evict the window's expired hits
        if len(hits) >= self._limit:
            retry_after = int(self._window - (now - hits[0])) + 1
            raise HTTPException(
                status_code=429,
                detail="rate_limited: too many requests, slow down",
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)


def make_limiters() -> dict[str, FixedWindowLimiter]:
    """Per-authenticated-user limits — generous for a human learner, tight against a script. Built
    per-app (stored on app.state) so tests get fresh counters. Tune the numbers here."""
    return {
        # turns + byok-record: a human answers every ~10-60s; 30/min is plenty, caps a spam loop.
        "turn": FixedWindowLimiter(limit=30, window_s=60),
        # session create/reset: occasional; 15/min bounds churn against the one-active-session index.
        "session": FixedWindowLimiter(limit=15, window_s=60),
        # prompt-bundle: ~once per BYOK turn (sometimes a retry); 60/min is loose but bounded.
        "bundle": FixedWindowLimiter(limit=60, window_s=60),
        # reads (whoami, get-session, active-session lookup): cheap, but a script shouldn't hammer the
        # DB unbounded. 120/min ≈ 2/s per principal — loose for a human, tight for a loop.
        "read": FixedWindowLimiter(limit=120, window_s=60),
    }
