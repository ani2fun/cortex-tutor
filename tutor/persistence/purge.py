"""Purge expired (idle) coach sessions.

The tutor keeps sessions only ephemerally — it's the live-interview working store, not the archive.
Durable "keep this" lives in cortex (POST /api/coach/saved, allow-listed). A session's ``expires_at`` is
a sliding window (set at create, refreshed on each turn / model switch in ``repo``); once it lapses the
row is swept here. Messages / gate / grounding cascade via the FK ``ON DELETE CASCADE``.
"""

from __future__ import annotations

from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tutor.persistence import models


async def purge_expired(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    """Delete every session whose sliding TTL has lapsed (``expires_at < now()``). Returns the count."""
    async with sessionmaker() as db:
        result = await db.execute(delete(models.Session).where(models.Session.expires_at < func.now()))
        await db.commit()
        return result.rowcount
