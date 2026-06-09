"""Async SQLAlchemy engine + session factory for the tutor Postgres schema."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(database_url: str) -> AsyncEngine:
    """Create the async engine. ``database_url`` must use the asyncpg driver
    (``postgresql+asyncpg://…``)."""
    return create_async_engine(database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
