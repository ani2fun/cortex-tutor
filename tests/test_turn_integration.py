"""Integration test for the turn orchestration against the live compose Postgres.

Skipped when the DB at ``DATABASE_URL`` isn't reachable, so it runs locally with ``make up`` and is a
clean no-op in a bare CI. Uses a fake gate provider — no model calls.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from tutor.domain.steps import Step
from tutor.domain.verdict import Verdict
from tutor.orchestration import turn as turn_orch
from tutor.persistence import repo
from tutor.persistence.db import make_engine, make_sessionmaker

DB_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://cortex:cortex@localhost:5432/cortex")


class FakeGate:
    """A GateProvider stub returning a fixed verdict dict — no network."""

    def __init__(self, verdict: str, score: int = 80) -> None:
        self._verdict = verdict
        self._score = score

    async def gate(self, *, system, messages, tool_schema, tool_name) -> dict:
        return {"verdict": self._verdict, "score": self._score}


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker]:
    engine = make_engine(DB_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("Postgres not reachable — run `make up` to exercise the turn integration test")
    yield make_sessionmaker(engine)
    await engine.dispose()


async def test_pass_advances_and_persists(sessionmaker: async_sessionmaker):
    user, problem = f"itest-{uuid4()}", "itest/two-sum"
    async with sessionmaker() as db:
        out = await turn_orch.run_turn(
            db,
            provider=FakeGate("pass", 80),
            user_sub=user,
            problem_id=problem,
            origin="your_turn",
            step=Step.CLARIFY,
            answer="I restate it as: given nums + target, return the two indices…",
            problem_context="ctx",
        )
    assert out.verdict.verdict is Verdict.PASS
    assert out.advanced is True
    assert out.session.current_step == Step.EXAMPLES.value

    async with sessionmaker() as db:
        s = await repo.get_active(db, user, problem)
        assert s is not None
        assert s.current_step == Step.EXAMPLES.value
        assert s.version == 1
        msgs = await repo.load_recent_messages(db, s.id)
        assert [m.role for m in msgs] == ["user", "coach"]  # answer + placeholder coach reply


async def test_retry_stays_and_counts_attempt(sessionmaker: async_sessionmaker):
    user, problem = f"itest-{uuid4()}", "itest/two-sum-retry"
    async with sessionmaker() as db:
        out = await turn_orch.run_turn(
            db,
            provider=FakeGate("retry", 0),
            user_sub=user,
            problem_id=problem,
            origin="your_turn",
            step=Step.CLARIFY,
            answer="dunno",
            problem_context="ctx",
        )
    assert out.verdict.verdict is Verdict.RETRY
    assert out.advanced is False
    assert out.session.current_step == Step.CLARIFY.value
    assert out.session.attempts == 1


async def test_wrong_step_raises_mismatch(sessionmaker: async_sessionmaker):
    user, problem = f"itest-{uuid4()}", "itest/two-sum-mismatch"
    async with sessionmaker() as db:
        # fresh session starts at CLARIFY; submitting PLAN must be rejected
        with pytest.raises(turn_orch.StepMismatch):
            await turn_orch.run_turn(
                db,
                provider=FakeGate("pass", 80),
                user_sub=user,
                problem_id=problem,
                origin="your_turn",
                step=Step.PLAN,
                answer="…",
                problem_context="ctx",
            )
