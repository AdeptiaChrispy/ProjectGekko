"""Alembic environment script — Plan 01-03 Task 3.

CRITICAL — passphrase handling:

    * ``alembic.ini`` has ``sqlalchemy.url`` BLANK (Pitfall 1 / Task 3
      threat T-01-03-04). The URL is NEVER persisted on disk.
    * The SQLCipher passphrase is read at runtime from the
      ``GEKKO_DB_PASSPHRASE`` env var. ``gekko init`` / ``gekko serve``
      prompts the operator and sets this var on the child Alembic
      subprocess; no fallback path embeds the passphrase in any config.
    * Engine bootstrap goes through ``gekko.db.engine.get_async_engine``
      so the PRAGMA-key connect-event handler activates and applies
      PRAGMA key as the FIRST statement on every connection.

D-21: the migration targets the per-user DB file at
``settings.db_path_for(settings.gekko_user_id)``. Multi-user installs
re-run ``alembic upgrade head`` once per user (single-process per user
per D-21).

FOREIGN KEY NOTE (SQLite batch migration):

SQLite's ``PRAGMA foreign_keys`` is a no-op inside a transaction, so it
MUST be toggled on the raw DBAPI connection BEFORE Alembic opens its
transaction. ``batch_alter_table`` recreates tables via CREATE-tmp →
COPY → DROP → RENAME. If foreign_keys is ON during the DROP, any child
rows referencing the parent table cause an IntegrityError. The pattern
below disables FK enforcement around the entire migration run, then
restores it. FK enforcement remains ON at runtime (the engine's
connect-event handler re-enables it on every new connection opened by
the application after migrations complete).

See: https://alembic.sqlalchemy.org/en/latest/batch.html#dealing-with-referencing-foreign-keys
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy.ext.asyncio import AsyncEngine

from gekko.config import get_settings
from gekko.db.engine import get_async_engine
from gekko.db.models import Base

# ---------------------------------------------------------------------------
# Alembic Config
# ---------------------------------------------------------------------------

config = context.config

# Interpret the config file for Python logging unless we're running under
# pytest, which reconfigures logging on every test.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

#: ``Base.metadata`` is the source-of-truth schema autogenerate compares
#: against. Plan 01-04+ migrations will be diffed against this.
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_passphrase() -> str:
    """Read GEKKO_DB_PASSPHRASE from env or fail with a clear error."""
    pp = os.environ.get("GEKKO_DB_PASSPHRASE")
    if not pp:
        raise RuntimeError(
            "GEKKO_DB_PASSPHRASE env var is required to run Alembic migrations "
            "(SQLCipher passphrase is read at runtime per AUTH-03 / D-19; "
            "alembic.ini deliberately has no sqlalchemy.url so the passphrase "
            "is never persisted)."
        )
    return pp


def _build_engine() -> AsyncEngine:
    """Construct the per-user encrypted ``AsyncEngine`` for migrations."""
    settings = get_settings()
    passphrase = _require_passphrase()
    return get_async_engine(settings.db_path_for(settings.gekko_user_id), passphrase)


def _get_raw_dbapi_connection(connection: Any) -> Any:
    """Extract the underlying DBAPI connection from a SQLAlchemy 2.x ``Connection``.

    SQLAlchemy 2.x wraps the DBAPI connection as:
        connection.connection          — SQLAlchemy's ``ConnectionFairy``
        connection.connection.dbapi_connection — the raw DBAPI object

    For the aiosqlite + sqlcipher3 stack we use, the raw DBAPI object is the
    sqlcipher3 connection that accepts cursor-level PRAGMA statements. We need
    it to issue ``PRAGMA foreign_keys`` OUTSIDE Alembic's transaction (the
    pragma is a no-op inside a transaction per SQLite semantics).
    """
    # SQLAlchemy 2.x: connection.connection is the pooled DBAPI connection
    # (a ConnectionFairy). .dbapi_connection unwraps to the raw driver object.
    return connection.connection.dbapi_connection


def _set_foreign_keys(connection: Any, enabled: bool) -> None:
    """Issue PRAGMA foreign_keys on the raw DBAPI connection outside any transaction.

    This is a no-op if called inside an active transaction — SQLite silently
    ignores it. We call this BEFORE Alembic opens its transaction and AFTER it
    commits, so the pragma takes effect for the batch DDL that follows.
    """
    raw = _get_raw_dbapi_connection(connection)
    val = "ON" if enabled else "OFF"
    cursor = raw.cursor()
    try:
        cursor.execute(f"PRAGMA foreign_keys = {val}")
    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# Migration entry points
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL, no engine).

    Offline mode is rarely useful for SQLCipher because the PRAGMA key is
    not part of the SQL DDL stream. We still implement it for completeness;
    operators normally use the online (engine-backed) path below.
    """
    settings = get_settings()
    url = f"sqlite:///{settings.db_path_for(settings.gekko_user_id).as_posix()}"
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE
    )

    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Any) -> None:
    """Sync inner — passed to ``connection.run_sync`` by the async wrapper.

    IMPORTANT: ``PRAGMA foreign_keys`` must be toggled on the raw DBAPI
    connection BEFORE Alembic opens its transaction (the pragma is a no-op
    inside a transaction). We disable FK enforcement here so that
    ``batch_alter_table`` can safely DROP FK-referenced parent tables
    (e.g. ``users``) during the CREATE-tmp → COPY → DROP → RENAME cycle.
    FK enforcement is re-enabled AFTER the migration completes; the
    engine's connect-event handler re-applies ``PRAGMA foreign_keys = ON``
    on every subsequent new connection opened by the application.
    """
    # Step 1: disable FK enforcement BEFORE any transaction is opened.
    _set_foreign_keys(connection, enabled=False)

    try:
        context.configure(
            connection=connection,  # type: ignore[arg-type]
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE
        )

        with context.begin_transaction():
            context.run_migrations()
    finally:
        # Step 2: re-enable FK enforcement AFTER the transaction commits
        # (or rolls back). This runs on the same raw connection so any
        # subsequent statements on this connection see FK enforcement ON.
        # Note: new connections from the engine pool will also have FK ON
        # via the connect-event handler in engine.py.
        _set_foreign_keys(connection, enabled=True)


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode via the async engine."""
    engine = _build_engine()
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
