"""SQLCipher engine factories — Plan 01-03 Task 1 (AUTH-03).

This module is the single load-bearing place where SQLCipher encryption is
activated. Every Gekko process opens its per-user DB through one of:

    * :func:`get_async_engine` — SQLAlchemy 2.x ``AsyncEngine`` for the
      runtime (sessions, FastAPI/Slack handlers, agent runtime, etc.).
    * :func:`get_sync_engine` — synchronous ``Engine`` for APScheduler's
      ``SQLAlchemyJobStore`` (Plan 01-09) and Alembic migrations.

Both factories share the **same** connect-event handler that issues
``PRAGMA key = '...'`` as the FIRST statement on every new DBAPI connection,
per RESEARCH §Pitfall 1. The passphrase is captured by closure on the engine
object — it is NEVER embedded in the URL, NEVER logged, and NEVER returned
by ``repr(engine)``.

Driver / dialect strategy (Pitfall 1 — choice rationale):

We use a **``creator=`` / ``async_creator_fn=`` callback** instead of
embedding the passphrase in the SQLAlchemy URL (which the stock
``sqlite+pysqlcipher`` dialect would do via ``url.password``). The
callback returns a freshly opened ``sqlcipher3.dbapi2.Connection`` (sync)
or an ``aiosqlite.Connection`` wrapping a sqlcipher3 connector (async),
so:

  * The URL has NO user/password component — ``str(engine.url)`` cannot
    leak the passphrase (T-01-03-05 mitigation).
  * The PRAGMA key is set entirely by our ``connect``-event handler,
    giving us full control over ordering (Pitfall 1) and over the
    wrong-passphrase smoke-probe (Pitfall 2).
  * We avoid SQLAlchemy's pysqlcipher-dialect quirks (it has a stray
    ``print(query_pragmas)`` debug line and uses
    ``SingletonThreadPool``).

Mitigations enacted here:

* **T-01-03-01** Information Disclosure (DB at rest) — SQLCipher 4
  defaults via ``PRAGMA cipher_compatibility = 4``
* **T-01-03-02** Tampering (PRAGMA key ordering) — handler is the first
  statement on every connection
* **T-01-03-03** Repudiation (silent wrong-passphrase) — the connect
  handler probes ``SELECT count(*) FROM sqlite_master`` immediately after
  PRAGMA key and raises :exc:`WrongPassphraseError` if the probe fails;
  :func:`verify_passphrase` provides an idempotent async re-probe
* **T-01-03-05** Info Disclosure (passphrase in ``repr(engine)``) — URL
  has no password field; passphrase lives in handler closure only

Per D-21: callers pass a per-user ``db_path`` (e.g.,
``settings.db_path_for(user_id)``) so each user has an isolated encrypted
file. No code in this module ever crosses user boundaries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import aiosqlite
import sqlcipher3.dbapi2 as sqlcipher_dbapi
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import DatabaseError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.sql import text

from gekko.core.errors import WrongPassphraseError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Marker substrings (lowercased) emitted by sqlcipher3 when the passphrase
#: is wrong. We match on either to convert :exc:`OperationalError` /
#: :exc:`DatabaseError` into :exc:`WrongPassphraseError`.
_ENCRYPTED_DB_MARKERS: Final[tuple[str, ...]] = (
    "file is encrypted",
    "not a database",
)

#: User-friendly wrong-passphrase message — surfaces in CLI output via
#: ``gekko init`` / ``gekko serve``.
_WRONG_PASSPHRASE_MESSAGE: Final[str] = (
    "Wrong passphrase — please re-run with the correct one"
)

#: Dummy URL given to ``create_engine`` / ``create_async_engine``. The
#: ``creator=`` callback overrides connection creation, so this URL is
#: never actually used to connect — it must just parse as a valid SQLite
#: URL. Using ``:memory:`` makes the override explicit; the real path
#: lives in the creator closure.
_DUMMY_SYNC_URL: Final[str] = "sqlite:///:memory:"
_DUMMY_ASYNC_URL: Final[str] = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# Passphrase escaping
# ---------------------------------------------------------------------------


def _escape_passphrase_for_pragma(passphrase: str) -> str:
    """Escape a passphrase for safe inclusion in ``PRAGMA key = '...'``.

    SQLite ``PRAGMA key`` does not support parameter binding — the value
    MUST be a literal in the SQL text. We wrap the passphrase in single
    quotes and double any embedded single quotes (standard SQL string-
    literal escaping). All other characters pass through unchanged.

    The escaped value is then concatenated into the PRAGMA statement and
    executed via ``cursor.execute()`` — it is NEVER logged, NEVER returned
    by ``repr(engine)``, and lives only inside the closure of the connect
    event handler.
    """
    escaped = passphrase.replace("'", "''")
    return f"'{escaped}'"


# ---------------------------------------------------------------------------
# Connect-event handler — registers per engine via closure on `passphrase`
# ---------------------------------------------------------------------------


#: DBAPI exception class names that SQLAlchemy uses to translate driver
#: errors into its own ``sqlalchemy.exc.*`` hierarchy. We patch these on the
#: dialect to point at ``sqlcipher3.dbapi2.*`` instead of the default
#: ``sqlite3.*`` — without this, ``sqlcipher3.dbapi2.IntegrityError`` leaks
#: through as a raw DBAPI error instead of being wrapped in
#: ``sqlalchemy.exc.IntegrityError``.
_DBAPI_EXCEPTION_NAMES: Final[tuple[str, ...]] = (
    "Error",
    "Warning",
    "InterfaceError",
    "DatabaseError",
    "DataError",
    "OperationalError",
    "IntegrityError",
    "InternalError",
    "ProgrammingError",
    "NotSupportedError",
)


def _patch_dialect_dbapi_exceptions(sync_engine: Engine) -> None:
    """Point the dialect's DBAPI exception attrs at sqlcipher3's classes.

    SQLAlchemy's aiosqlite / pysqlite dialects bind their DBAPI exception
    classes (``self.dbapi.IntegrityError`` etc.) to ``sqlite3.*`` at
    dialect-init time. The actual connection objects we hand back via
    ``creator=`` / ``async_creator_fn=`` are sqlcipher3 connections, so
    the exceptions raised by them are ``sqlcipher3.dbapi2.IntegrityError``
    — a different class hierarchy. Without this patch, raw sqlcipher3
    exceptions escape SQLAlchemy's wrapping machinery and tests catching
    ``sqlalchemy.exc.IntegrityError`` (the standard contract) silently
    fail.

    We overwrite each exception name on the loaded DBAPI module/adapter
    to point at the sqlcipher3 class. This keeps the standard
    ``sqlalchemy.exc.IntegrityError`` contract working for downstream
    code.
    """
    dbapi = sync_engine.dialect.loaded_dbapi
    for name in _DBAPI_EXCEPTION_NAMES:
        sqlcipher_exc = getattr(sqlcipher_dbapi, name, None)
        if sqlcipher_exc is not None:
            setattr(dbapi, name, sqlcipher_exc)


def _install_sqlcipher_connect_handler(
    sync_engine: Engine, passphrase: str
) -> None:
    """Wire the SQLCipher PRAGMA handler onto a sync ``Engine``.

    For an async engine the caller passes ``async_engine.sync_engine`` —
    SQLAlchemy 2.x dispatches the ``connect`` event there. The handler
    fires for every new DBAPI connection (not once per engine), so every
    connection in a pool gets ``PRAGMA key`` applied before any other SQL.
    """
    quoted_passphrase = _escape_passphrase_for_pragma(passphrase)

    @event.listens_for(sync_engine, "connect")
    def _set_sqlcipher_pragmas(
        dbapi_connection: Any, _connection_record: Any
    ) -> None:
        """Issue PRAGMA key + standard PRAGMAs as the first statements.

        Per RESEARCH §Pitfall 1: ``PRAGMA key`` MUST be the first statement
        on every new SQLCipher connection. Any other SQL emitted first
        fails with ``file is encrypted or is not a database``.

        We additionally probe with ``SELECT count(*) FROM sqlite_master``
        per RESEARCH §Pitfall 2 — this surfaces a wrong passphrase
        synchronously at connection time as :exc:`WrongPassphraseError`,
        rather than letting the first real query fail with a confusing
        :exc:`OperationalError` deeper in the call stack.
        """
        cursor = dbapi_connection.cursor()
        try:
            try:
                # 1. PRAGMA key — MUST be first. The passphrase is escaped
                #    and interpolated directly because PRAGMA does not
                #    accept bind parameters; the value lives in the handler
                #    closure only and is never logged.
                cursor.execute(f"PRAGMA key = {quoted_passphrase}")
                # 2. Use SQLCipher 4 defaults (RESEARCH §Pitfall 1).
                cursor.execute("PRAGMA cipher_compatibility = 4")
                # 3. Foreign keys ON — SQLite default is OFF; we need it
                #    ON for cascade behavior on user_id / strategy_id FKs
                #    (D-21). Statement-only PRAGMA — no DB I/O.
                cursor.execute("PRAGMA foreign_keys = ON")
                # 4. WAL journal mode — better concurrency for the single-
                #    process modular monolith (D-18, D-22). This PRAGMA
                #    touches the DB header so a wrong PRAGMA key fails
                #    here, not at the smoke probe below.
                cursor.execute("PRAGMA journal_mode = WAL")

                # 5. Smoke probe — catch wrong passphrase NOW, not later.
                #    sqlite_master is the master schema table; if PRAGMA
                #    key was wrong, this SELECT fails with "file is
                #    encrypted" or "not a database". (For existing DBs
                #    the journal_mode PRAGMA above already failed; the
                #    probe defends against any future PRAGMA reordering.)
                cursor.execute("SELECT count(*) FROM sqlite_master")
                cursor.fetchone()
            except sqlcipher_dbapi.DatabaseError as exc:
                # Any DB-layer failure inside the connect handler — if its
                # message matches the SQLCipher wrong-passphrase signature,
                # convert to the typed error so callers see a clear
                # WrongPassphraseError instead of a raw DatabaseError.
                if any(
                    marker in str(exc).lower()
                    for marker in _ENCRYPTED_DB_MARKERS
                ):
                    raise WrongPassphraseError(
                        _WRONG_PASSPHRASE_MESSAGE
                    ) from exc
                raise
        finally:
            cursor.close()


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------


def _normalize_db_path(db_path: Path | str) -> Path:
    """Normalize the DB path: expand ``~`` and ensure parent directory exists.

    Returns the resolved ``Path``. We deliberately do NOT touch the file
    itself — SQLCipher creates it on first connect.
    """
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Public factories
# ---------------------------------------------------------------------------


def get_async_engine(db_path: Path | str, passphrase: str) -> AsyncEngine:
    """Return a SQLCipher-backed :class:`AsyncEngine` for ``db_path``.

    Args:
        db_path: Per-user encrypted DB file path (D-21). Parent directory
            is created if missing.
        passphrase: SQLCipher master passphrase. Held in the connect-event
            handler closure; never embedded in the engine URL or repr.

    Returns:
        An ``AsyncEngine`` whose every new connection has PRAGMA key applied
        first. Subsequent SQL executes against the encrypted DB.

    The caller is responsible for calling ``await engine.dispose()`` when
    finished (typically during the FastAPI lifespan shutdown).
    """
    path = _normalize_db_path(db_path)
    path_str = str(path)

    def _async_creator_fn(*_args: Any, **_kwargs: Any) -> aiosqlite.Connection:
        """Return an aiosqlite ``Connection`` backed by a sqlcipher3 connector.

        SQLAlchemy's aiosqlite dialect passes the URL database name plus a
        handful of kwargs (e.g., ``check_same_thread``); we ignore them
        because the real path lives in this closure. The aiosqlite
        ``Connection`` runs the sqlcipher3 connection on a worker thread,
        so ``check_same_thread`` is irrelevant here.
        """

        def _sqlcipher_connector() -> Any:
            return sqlcipher_dbapi.connect(path_str, check_same_thread=False)

        return aiosqlite.Connection(_sqlcipher_connector, iter_chunk_size=64)

    engine = create_async_engine(
        _DUMMY_ASYNC_URL,
        connect_args={"async_creator_fn": _async_creator_fn},
        future=True,
    )

    _install_sqlcipher_connect_handler(engine.sync_engine, passphrase)
    _patch_dialect_dbapi_exceptions(engine.sync_engine)
    return engine


def get_sync_engine(db_path: Path | str, passphrase: str) -> Engine:
    """Return a SQLCipher-backed sync :class:`Engine` for ``db_path``.

    Mirrors :func:`get_async_engine` but builds a synchronous engine. Used
    by APScheduler's ``SQLAlchemyJobStore`` (Plan 01-09) and Alembic
    migration scripts — both of which want a pre-built sync ``Engine``
    rather than a URL string (so the passphrase never has to travel
    through a config file or env var visible to APScheduler).

    Per VALIDATION row 01-09-T2 / T-01-03-05: the passphrase MUST NOT
    appear in ``repr(engine)`` or ``str(engine.url)``. We mitigate by
    keeping the passphrase in the handler closure only — the URL is a
    placeholder.
    """
    path = _normalize_db_path(db_path)
    path_str = str(path)

    def _sync_creator() -> Any:
        """Return a fresh SQLCipher DBAPI connection.

        SQLAlchemy invokes ``creator`` on every pool-miss, then routes the
        connection through our connect-event handler which applies
        ``PRAGMA key`` as the first statement.
        """
        return sqlcipher_dbapi.connect(path_str, check_same_thread=False)

    engine = create_engine(
        _DUMMY_SYNC_URL,
        creator=_sync_creator,
        future=True,
    )

    _install_sqlcipher_connect_handler(engine, passphrase)
    _patch_dialect_dbapi_exceptions(engine)
    return engine


# ---------------------------------------------------------------------------
# Passphrase verification helper
# ---------------------------------------------------------------------------


async def verify_passphrase(engine: AsyncEngine) -> None:
    """Verify the engine's passphrase matches the DB by probing ``sqlite_master``.

    Per RESEARCH §Pitfall 2: SQLCipher accepts any ``PRAGMA key`` without
    error; the mismatch only surfaces on the first real SELECT. This
    helper triggers that SELECT immediately so callers see a typed
    :exc:`WrongPassphraseError` rather than a generic
    :exc:`OperationalError` deeper in the call stack.

    The connect-event handler already probes on first-connect (Pitfall 2
    mitigation #1). This helper is the **async-callable** wrapper used by
    startup paths (``gekko init`` / ``gekko serve``) that need an awaitable
    check; it triggers a fresh connection if necessary.

    Idempotent — safe to call multiple times against the same engine.

    Raises:
        WrongPassphraseError: When the engine cannot decrypt the DB.
        OperationalError: Any other database-layer failure (re-raised
            unchanged so the caller sees the real cause).
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT count(*) FROM sqlite_master"))
    except WrongPassphraseError:
        # The connect-event handler already raised the typed error.
        raise
    except (OperationalError, DatabaseError) as exc:
        # Connect-handler path may have failed at engine-construction
        # time; convert the underlying message into the typed error.
        # We also handle the case where SQLAlchemy wraps our
        # WrongPassphraseError in a DBAPIError — unwrap and re-raise.
        cause = exc.__cause__
        if isinstance(cause, WrongPassphraseError):
            raise cause from exc
        if any(marker in str(exc).lower() for marker in _ENCRYPTED_DB_MARKERS):
            raise WrongPassphraseError(_WRONG_PASSPHRASE_MESSAGE) from exc
        raise


__all__: tuple[str, ...] = (
    "get_async_engine",
    "get_sync_engine",
    "verify_passphrase",
)
