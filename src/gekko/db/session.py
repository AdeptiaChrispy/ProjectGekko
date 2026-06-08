"""Async session factory — Plan 01-03 Task 2.

Thin wrapper around :class:`sqlalchemy.ext.asyncio.async_sessionmaker` so
callers don't have to wire ``expire_on_commit=False`` + ``class_=AsyncSession``
themselves. Per D-21 (per-user isolated deployment) each Gekko process owns
exactly one ``AsyncEngine`` (built via :func:`gekko.db.engine.get_async_engine`)
and one session factory bound to it — there's no multi-tenant connection
multiplexing in Phase 1.

Usage:

    engine = get_async_engine(settings.db_path_for(user_id), passphrase)
    SessionLocal = make_session_factory(engine)

    async with SessionLocal() as session:
        ...  # ORM work
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

#: Public type alias for the session factory shape — Plan 01-04 / 01-07 / 01-08
#: import this for type-hint purposes.
AsyncSessionLocal = async_sessionmaker[AsyncSession]


def make_session_factory(engine: AsyncEngine) -> AsyncSessionLocal:
    """Build an async session factory bound to ``engine``.

    ``expire_on_commit=False`` is required so attribute access on ORM rows
    after a commit does not trigger an implicit refresh — important for
    fire-and-forget tasks (e.g., the executor's background fill listener)
    that access fields on a Proposal row after committing the status
    transition.
    """
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )


__all__: tuple[str, ...] = ("AsyncSessionLocal", "make_session_factory")
