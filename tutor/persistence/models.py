"""SQLAlchemy 2.0 (async) ORM over the ``tutor`` Postgres schema.

Liquibase owns the schema (``migrations/``); these models mirror it for the repo and a future CI
metadata diff. Enum columns are typed as ``str`` here — the Postgres ENUM types enforce the value
set at write time; tightening the ORM enum types for an exact metadata diff is a follow-up.
"""

from __future__ import annotations

import datetime as dt
from typing import ClassVar
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "tutor"


class Base(DeclarativeBase):
    pass


def _fk(table: str) -> ForeignKey:
    return ForeignKey(f"{SCHEMA}.{table}.id", ondelete="CASCADE")


class Session(Base):
    __tablename__ = "session"
    __table_args__: ClassVar = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_sub: Mapped[str] = mapped_column(Text)
    problem_id: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    current_step: Mapped[str] = mapped_column(String)
    step_index: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer)
    hint_level: Mapped[int] = mapped_column(Integer)
    coach_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    gate_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    rubric_version: Mapped[str] = mapped_column(Text)
    running_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_msg_seq: Mapped[int] = mapped_column(Integer)
    byok: Mapped[bool] = mapped_column(Boolean)
    model_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6))
    version: Mapped[int] = mapped_column(Integer)
    last_turn_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "message"
    __table_args__: ClassVar = {"schema": SCHEMA}

    session_id: Mapped[UUID] = mapped_column(_fk("session"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String)
    step: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    # none_as_null: a Python None must store SQL NULL, not JSON null — raw-SQL audits
    # (content_json IS NOT NULL) distinguish evidence-bearing turns by it.
    content_json: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    input_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6))
    turn_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    summarized_into: Mapped[int | None] = mapped_column(Integer, nullable=True)
    redacted: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class Gate(Base):
    __tablename__ = "gate"
    __table_args__: ClassVar = {"schema": SCHEMA}

    session_id: Mapped[UUID] = mapped_column(_fk("session"), primary_key=True)
    step: Mapped[str] = mapped_column(String, primary_key=True)
    verdict: Mapped[str] = mapped_column(String)
    score: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer)
    missing_json: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    judge_kind: Mapped[str] = mapped_column(String)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class GateCall(Base):
    """Append-only audit log: one row per gate invocation (``gate`` keeps only the last verdict
    per step). The eval dataset is extracted from here — see ``evals/README.md``."""

    __tablename__ = "gate_call"
    __table_args__: ClassVar = {"schema": SCHEMA}

    session_id: Mapped[UUID] = mapped_column(_fk("session"), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    turn_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    step: Mapped[str] = mapped_column(String)
    answer_seq: Mapped[int] = mapped_column(Integer)
    rubric_version: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String)
    raw_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    verdict: Mapped[str] = mapped_column(String)
    score: Mapped[int] = mapped_column(Integer)
    missing_json: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    hint: Mapped[str] = mapped_column(Text)
    problem_context_hash: Mapped[str] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class GroundingRef(Base):
    __tablename__ = "grounding_ref"
    __table_args__: ClassVar = {"schema": SCHEMA}

    session_id: Mapped[UUID] = mapped_column(_fk("session"), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    step: Mapped[str] = mapped_column(String)
    tool: Mapped[str] = mapped_column(Text)
    citation_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
