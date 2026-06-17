"""Live broker credential vault — Plan 02-06 Task 1 (BROK-A-02 / D-34).

Per D-34 invariant: live Alpaca API keys NEVER touch ``.env``. The SQLCipher
vault (Phase-1 D-19) is the single source of truth. This module provides the
``store_live_credentials`` + ``load_live_credentials`` accessors used by:

  * ``gekko credentials add-alpaca-live`` CLI — operator-supplied key + secret
    written to ``broker_credentials`` with ``kind='alpaca_live'``.
  * :func:`gekko.execution.executor._build_broker` — when the proposal's
    ``account_mode == "LIVE"``, the executor loads the credential pair and
    constructs ``AlpacaBroker(paper=False, _allow_live=True)``.

Encryption model (verified against Phase-1 D-19 / AUTH-03; ``src/gekko/db/
models.py:325-353`` BrokerCredential schema):

  * The ``key_blob`` and ``secret_blob`` columns store the PLAINTEXT API key
    + secret bytes-for-bytes. The application performs NO Fernet wrap.
  * Data-at-rest protection is provided entirely by SQLCipher whole-DB
    encryption — the ``.db`` file on disk is encrypted; SQLAlchemy reads and
    writes plaintext through the SQLCipher driver when given the correct
    passphrase.
  * Threat model: anyone with the SQLCipher passphrase can read these blobs
    by design. Phase 2 does NOT add an application-layer Fernet wrap on top
    of SQLCipher — that defense-in-depth layer is deferred to a future
    security-hardening phase if the threat model warrants it.

Per D-21 every function is scoped to a single ``user_id`` and uses the
module-local :func:`_get_session_factory` shim with a ``finally:
engine.dispose()`` block (PATTERNS §3c).

No ``claude_agent_sdk`` import here — this module sits on the credentials
path; LLM bytes never reach it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.config import get_settings
from gekko.db.engine import get_async_engine
from gekko.db.models import BrokerCredential
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.logging_config import get_logger
from gekko.vault.passphrase import get_passphrase as _get_passphrase

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module-level test seam — production builds engines from settings + cache
# ---------------------------------------------------------------------------


def _get_session_factory(
    user_id: str,
) -> tuple[AsyncSessionLocal, AsyncEngine | None]:
    """Build a session factory + owning engine for ``user_id``.

    Mirrors the indirection used by :mod:`gekko.execution.executor` so tests
    have a single seam to monkeypatch.
    """
    settings = get_settings()
    engine = get_async_engine(
        settings.db_path_for(user_id), _get_passphrase()
    )
    return make_session_factory(engine), engine


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def store_live_credentials(
    *, user_id: str, api_key: str, secret_key: str
) -> None:
    """Persist an Alpaca live API key + secret in the SQLCipher vault.

    Writes a single :class:`gekko.db.models.BrokerCredential` row with
    ``broker="alpaca"``, ``kind="alpaca_live"``, ``paper=False``. The key
    + secret are stored as PLAINTEXT strings in the blob columns; the
    SQLCipher whole-DB encryption is the at-rest defense (see module
    docstring).

    Emits a ``credentials_added`` audit event. The payload DELIBERATELY
    excludes the key value (only ``kind`` and a non-sensitive
    ``has_key=True`` marker land in the audit log).

    BL-01 fix (Phase-2 code review): previously this wrote
    ``event_type="error"`` with a ``context="credentials.added"``
    discriminator, polluting the error bucket. ``_EVENT_TYPES`` now
    carries ``credentials_added`` directly (Alembic 0003 extended
    ``ck_event_type`` to accept it).

    :param user_id: Per-user DB scope (D-21).
    :param api_key: Alpaca live API key. PLAINTEXT — must NEVER be logged
        or returned in repr output.
    :param secret_key: Alpaca live secret. PLAINTEXT — same constraint.
    :raises sqlalchemy.exc.IntegrityError: When a ``(user_id, "alpaca",
        "alpaca_live")`` row already exists (composite PK).
    """
    sf, engine = _get_session_factory(user_id)
    try:
        now_iso = datetime.now(UTC).isoformat()
        async with sf() as session, session.begin():
            session.add(
                BrokerCredential(
                    user_id=user_id,
                    broker="alpaca",
                    kind="alpaca_live",
                    key_blob=api_key,
                    secret_blob=secret_key,
                    paper=False,
                    created_at=now_iso,
                )
            )
            # Audit the addition. BL-01 fix: now uses the dedicated
            # ``credentials_added`` event_type (added to D-14 in the
            # Alembic 0003 migration) instead of the prior workaround
            # of ``event_type="error"`` + ``context="credentials.added"``.
            await append_event(
                session,
                user_id=user_id,
                strategy_id=None,
                event_type="credentials_added",
                payload=normalize_decimals(
                    {
                        "broker": "alpaca",
                        "kind": "alpaca_live",
                        "has_key": True,
                    }
                ),
            )
        # NB: Structured keyword args ONLY — never f-string the key value.
        log.info(
            "credentials.added",
            user_id=user_id,
            broker="alpaca",
            kind="alpaca_live",
        )
    finally:
        if engine is not None:
            await engine.dispose()


async def load_live_credentials(
    user_id: str,
) -> tuple[str, str] | None:
    """Load the Alpaca live API key + secret from the SQLCipher vault.

    Returns ``(api_key, secret_key)`` as plaintext strings (SQLCipher
    decrypts transparently when given the correct passphrase), or
    ``None`` when no ``kind="alpaca_live"`` row exists for the user.

    :param user_id: Per-user DB scope.
    :returns: ``(api_key, secret_key)`` tuple, or ``None`` if no row.
    """
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            row = (
                await session.execute(
                    select(BrokerCredential).where(
                        BrokerCredential.user_id == user_id,
                        BrokerCredential.broker == "alpaca",
                        BrokerCredential.kind == "alpaca_live",
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return (row.key_blob, row.secret_blob)
    finally:
        if engine is not None:
            await engine.dispose()


__all__: tuple[str, ...] = (
    "load_live_credentials",
    "store_live_credentials",
)
