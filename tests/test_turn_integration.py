"""Integration test for the turn orchestration against the live compose Postgres.

Skipped when the DB at ``DATABASE_URL`` isn't reachable or the ``tutor`` schema isn't migrated, so it
runs locally with ``make up`` + ``make migrate`` and is a clean no-op in a bare CI. Uses a fake gate
provider — no model calls.
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
    """A GateProvider stub returning a fixed verdict dict — no network. Captures the messages it
    was shown so tests can assert on the gate-visible answer."""

    def __init__(self, verdict: str, score: int = 80) -> None:
        self._verdict = verdict
        self._score = score
        self.seen_messages: list[list[dict]] = []

    async def gate(self, *, system, messages, tool_schema, tool_name) -> dict:
        self.seen_messages.append(list(messages))
        return {"verdict": self._verdict, "score": self._score}


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker]:
    engine = make_engine(DB_URL)
    try:
        async with engine.connect() as conn:
            # to_regclass returns NULL (no error) when the relation is missing, so one probe
            # covers both "DB unreachable" (raises) and "schema not migrated" (NULL). Probe the
            # NEWEST relation so a stale schema skips instead of failing mid-test.
            migrated = await conn.scalar(text("SELECT to_regclass('tutor.gate_call')"))
    except Exception:
        await engine.dispose()
        pytest.skip("Postgres not reachable — run `make up` to exercise the turn integration test")
    if migrated is None:
        await engine.dispose()
        pytest.skip("tutor schema not migrated — run `make migrate` to exercise the turn integration test")
    yield make_sessionmaker(engine)
    await engine.dispose()


async def test_pass_advances_and_persists(sessionmaker: async_sessionmaker):
    user, problem = f"itest-{uuid4()}", "itest/two-sum"
    async with sessionmaker() as db:
        out = await turn_orch.apply_turn(
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
    # The transcript the coach responds to ends with the learner's answer.
    assert out.coach_messages[-1]["role"] == "user"
    assert out.coach_messages[-1]["content"].startswith("I restate it as")

    async with sessionmaker() as db:
        s = await repo.get_active(db, user, problem)
        assert s is not None
        assert s.current_step == Step.EXAMPLES.value
        assert s.version == 1
        # apply_turn persists only the learner's answer; the coach reply is recorded after streaming.
        msgs = await repo.load_recent_messages(db, s.id)
        assert [m.role for m in msgs] == ["user"]
        # One append-only gate_call audit row per invocation (the eval-dataset feed).
        call_sql = text(
            "SELECT step, answer_seq, outcome, verdict, provider FROM tutor.gate_call WHERE session_id = :sid"
        )
        calls = (await db.execute(call_sql, {"sid": s.id})).mappings().all()
        assert len(calls) == 1
        call = calls[0]
        assert (call["step"], call["answer_seq"], call["outcome"]) == ("clarify", 1, "valid")
        assert call["verdict"] == "pass"
        assert call["provider"] == "FakeGate"  # no identity attrs → class-name fallback

    # The coach reply is persisted separately (what the SSE route does once the stream completes).
    async with sessionmaker() as db:
        await turn_orch.record_coach_reply(
            db, session_id=out.session.id, step=Step.CLARIFY, content="Nice — let's look at examples."
        )
    async with sessionmaker() as db:
        msgs = await repo.load_recent_messages(db, out.session.id)
        assert [m.role for m in msgs] == ["user", "coach"]


async def test_retry_stays_and_counts_attempt(sessionmaker: async_sessionmaker):
    user, problem = f"itest-{uuid4()}", "itest/two-sum-retry"
    async with sessionmaker() as db:
        out = await turn_orch.apply_turn(
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


async def test_workbench_evidence_threads_into_gate_and_persists(sessionmaker: async_sessionmaker):
    """The s16 fix: code/language/runResult reach the gate (composed answer) + content_json."""
    user, problem = f"itest-{uuid4()}", "itest/two-sum-evidence"
    # Walk the FSM to `implement` — apply_turn only accepts the session's current step.
    for step in (Step.CLARIFY, Step.EXAMPLES, Step.APPROACH, Step.PLAN):
        async with sessionmaker() as db:
            await turn_orch.apply_turn(
                db,
                provider=FakeGate("pass", 80),
                user_sub=user,
                problem_id=problem,
                origin="your_turn",
                step=step,
                answer=f"answer for {step.value}",
                problem_context="ctx",
            )

    gate_provider = FakeGate("pass", 80)
    async with sessionmaker() as db:
        out = await turn_orch.apply_turn(
            db,
            provider=gate_provider,
            user_sub=user,
            problem_id=problem,
            origin="your_turn",
            step=Step.IMPLEMENT,
            answer="Implemented sort + two pointers.",
            problem_context="ctx",
            code="def two_sum(arr, t): ...",
            language="python",
            run_result="[3, 4]",
        )

    # The gate judged the composed answer — snapshot + run result folded in.
    gate_view = gate_provider.seen_messages[-1][-1]["content"]
    assert "Implemented sort + two pointers." in gate_view
    assert "[workbench snapshot — python]" in gate_view
    assert "def two_sum(arr, t): ..." in gate_view
    assert "[run result]\n[3, 4]" in gate_view
    # The coach responds to the same composed view.
    assert out.coach_messages[-1]["content"] == gate_view

    async with sessionmaker() as db:
        s = await repo.get_active(db, user, problem)
        assert s is not None
        msgs = await repo.load_recent_messages(db, s.id)
        implement_msg = msgs[-1]
        # `content` stays the learner's own words; the evidence rides in content_json.
        assert implement_msg.content == "Implemented sort + two pointers."
        assert implement_msg.content_json == {
            "code": "def two_sum(arr, t): ...",
            "language": "python",
            "runResult": "[3, 4]",
        }
        # Earlier (non-code) steps persisted no evidence.
        assert msgs[0].content_json is None


async def test_wrong_step_raises_mismatch(sessionmaker: async_sessionmaker):
    user, problem = f"itest-{uuid4()}", "itest/two-sum-mismatch"
    async with sessionmaker() as db:
        # fresh session starts at CLARIFY; submitting PLAN must be rejected
        with pytest.raises(turn_orch.StepMismatch):
            await turn_orch.apply_turn(
                db,
                provider=FakeGate("pass", 80),
                user_sub=user,
                problem_id=problem,
                origin="your_turn",
                step=Step.PLAN,
                answer="…",
                problem_context="ctx",
            )
