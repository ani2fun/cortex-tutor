"""Async persistence repo over the ``tutor`` schema — data access only, no orchestration.

The orchestration layer (a later phase) wraps a turn in ``SELECT … FOR UPDATE`` on the session row,
then calls these. The optimistic ``save_state`` (``WHERE version = expected``) is the second guard
against a two-tab double-submit: the loser gets ``False`` and a 409.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tutor.persistence import models


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def get_active(db: AsyncSession, user_sub: str, problem_id: str) -> models.Session | None:
    """The caller's single active session for a problem (the one-active partial unique index)."""
    stmt = select(models.Session).where(
        models.Session.user_sub == user_sub,
        models.Session.problem_id == problem_id,
        models.Session.status == "active",
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_active_locked(db: AsyncSession, user_sub: str, problem_id: str) -> models.Session | None:
    """Like ``get_active`` but takes a row lock (``SELECT … FOR UPDATE``) for the duration of a turn —
    the first guard against a two-tab double-submit (the optimistic ``save_state`` is the second)."""
    stmt = (
        select(models.Session)
        .where(
            models.Session.user_sub == user_sub,
            models.Session.problem_id == problem_id,
            models.Session.status == "active",
        )
        .with_for_update()
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_for_user(db: AsyncSession, session_id: UUID, user_sub: str) -> models.Session | None:
    """Fetch a session by id, scoped to its owner (so one user can't read another's)."""
    stmt = select(models.Session).where(
        models.Session.id == session_id,
        models.Session.user_sub == user_sub,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(
    db: AsyncSession,
    *,
    user_sub: str,
    problem_id: str,
    origin: str,
    rubric_version: str,
    byok: bool = False,
    model_hint: str | None = None,
) -> models.Session:
    now = _now()
    row = models.Session(
        id=uuid4(),
        user_sub=user_sub,
        problem_id=problem_id,
        origin=origin,
        status="active",
        current_step="clarify",
        step_index=0,
        attempts=0,
        hint_level=0,
        rubric_version=rubric_version,
        summary_msg_seq=0,
        byok=byok,
        model_hint=model_hint,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0,
        version=0,
        created_at=now,
        updated_at=now,
        expires_at=now + dt.timedelta(days=90),
    )
    db.add(row)
    await db.flush()
    return row


async def next_seq(db: AsyncSession, session_id: UUID) -> int:
    stmt = select(func.coalesce(func.max(models.Message.seq), 0) + 1).where(
        models.Message.session_id == session_id
    )
    return int((await db.execute(stmt)).scalar_one())


async def append_message(
    db: AsyncSession,
    *,
    session_id: UUID,
    role: str,
    step: str,
    content: str,
    turn_id: UUID | None = None,
    content_json: dict | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0,
) -> models.Message:
    msg = models.Message(
        session_id=session_id,
        seq=await next_seq(db, session_id),
        role=role,
        step=step,
        content=content,
        content_json=content_json,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        turn_id=turn_id,
        redacted=False,
        created_at=_now(),
    )
    db.add(msg)
    await db.flush()
    return msg


async def load_recent_messages(
    db: AsyncSession, session_id: UUID, limit: int = 40
) -> list[models.Message]:
    """The bounded verbatim window (excludes system rows), returned oldest-first for the prompt."""
    stmt = (
        select(models.Message)
        .where(models.Message.session_id == session_id, models.Message.role != "system")
        .order_by(models.Message.seq.desc())
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    rows.reverse()
    return rows


async def find_by_turn(
    db: AsyncSession, session_id: UUID, turn_id: UUID
) -> models.Message | None:
    """Idempotency lookup — has this learner-answer turn already been recorded?"""
    stmt = select(models.Message).where(
        models.Message.session_id == session_id,
        models.Message.turn_id == turn_id,
    )
    return (await db.execute(stmt)).scalars().first()


async def save_state(
    db: AsyncSession,
    *,
    session_id: UUID,
    expected_version: int,
    status: str,
    current_step: str,
    step_index: int,
    attempts: int,
    hint_level: int,
    last_turn_id: UUID | None = None,
) -> bool:
    """Optimistic write. Returns ``False`` if another writer advanced first (version mismatch)."""
    stmt = (
        update(models.Session)
        .where(
            models.Session.id == session_id,
            models.Session.version == expected_version,
        )
        .values(
            status=status,
            current_step=current_step,
            step_index=step_index,
            attempts=attempts,
            hint_level=hint_level,
            last_turn_id=last_turn_id,
            version=expected_version + 1,
            updated_at=_now(),
        )
    )
    return (await db.execute(stmt)).rowcount == 1


async def upsert_gate(
    db: AsyncSession,
    *,
    session_id: UUID,
    step: str,
    verdict: str,
    score: int,
    attempts: int,
    missing_json: dict | None = None,
    judge_kind: str = "llm",
) -> None:
    now = _now()
    stmt = pg_insert(models.Gate).values(
        session_id=session_id,
        step=step,
        verdict=verdict,
        score=score,
        attempts=attempts,
        missing_json=missing_json,
        judge_kind=judge_kind,
        created_at=now,
        updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[models.Gate.session_id, models.Gate.step],
        set_={
            "verdict": stmt.excluded.verdict,
            "score": stmt.excluded.score,
            "attempts": stmt.excluded.attempts,
            "missing_json": stmt.excluded.missing_json,
            "updated_at": now,
        },
    )
    await db.execute(stmt)


async def abandon_active(db: AsyncSession, user_sub: str, problem_id: str) -> int:
    """Mark the active session abandoned (used by reset). Returns rows affected."""
    stmt = (
        update(models.Session)
        .where(
            models.Session.user_sub == user_sub,
            models.Session.problem_id == problem_id,
            models.Session.status == "active",
        )
        .values(status="abandoned", updated_at=_now())
    )
    return (await db.execute(stmt)).rowcount
