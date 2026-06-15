"""Per-user storage quotas for coach persistence.

A homelab guard: an external (BYOK-tier) user must not be able to fill Postgres by accumulating
sessions/messages. The OPERATOR (homelab tier — ``ani2fun``) is unlimited, by IDENTITY: under
dual-mode the operator may run cloud models (``byok`` sessions) and must stay uncapped, so the
exemption keys off the tier, never the session's transport flag. Every check short-circuits cheaply
for the operator and is O(log N) otherwise (an indexed session count, or the next ``seq`` the append
already computes).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from tutor.auth import Principal, wants_byok
from tutor.config import Settings
from tutor.persistence import repo


async def check_session_quota(db: AsyncSession, principal: Principal, settings: Settings) -> None:
    """Refuse a NEW session once a capped (non-operator) user holds the max (active + completed)."""
    if not wants_byok(principal, settings):
        return  # operator / homelab tier — unrestricted
    count = await repo.count_sessions_for_user(db, principal.sub)
    if count >= settings.coach_max_sessions_per_user:
        raise HTTPException(
            status_code=429,
            detail=(
                f"quota_exceeded: you can keep at most {settings.coach_max_sessions_per_user} saved "
                "coaching sessions — clear some from the account menu to start another."
            ),
            headers={"Retry-After": "3600"},
        )


async def check_message_quota(
    db: AsyncSession, principal: Principal, settings: Settings, session_id: UUID
) -> None:
    """Refuse a new TURN once a session reaches its message cap. The operator is unlimited regardless
    of the model (exemption by identity, not transport). Uses the next ``seq`` (current count + 1) —
    the same index scan the append already needs."""
    if not wants_byok(principal, settings):
        return  # operator / homelab tier — unrestricted
    next_seq = await repo.next_seq(db, session_id)
    if next_seq > settings.coach_max_messages_per_session:
        raise HTTPException(
            status_code=429,
            detail=(
                "quota_exceeded: this conversation has reached its length limit — use Start over to "
                "clear it and keep practising."
            ),
        )
