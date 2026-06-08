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
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

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


def _do_run_migrations(connection: object) -> None:
    """Sync inner — passed to ``connection.run_sync`` by the async wrapper."""
    context.configure(
        connection=connection,  # type: ignore[arg-type]
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE
    )

    with context.begin_transaction():
        context.run_migrations()


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
