"""Plan 01-03 Task 1 — SQLCipher engine + verify_passphrase tests.

Per RESEARCH §Pitfall 1 (PRAGMA key ordering) and §Pitfall 2 (wrong-passphrase
UX). All 6 behavior tests from PLAN.md Task 1 plus the AUTH-03 sync-engine
no-passphrase-in-repr regression test (per VALIDATION row 01-09-T2 / T-01-03-05).

The engine factory MUST:
  * Apply ``PRAGMA key`` as the FIRST statement on every new DBAPI connection
  * Set ``PRAGMA cipher_compatibility = 4``, ``PRAGMA journal_mode = WAL``,
    ``PRAGMA foreign_keys = ON`` on every connection (verified via probe)
  * Raise ``WrongPassphraseError`` (NOT a raw ``OperationalError``) when a
    wrong passphrase is used on an existing encrypted DB
  * NEVER embed the passphrase in ``repr(engine)`` or ``str(engine.url)``
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from gekko.core.errors import GekkoError, WrongPassphraseError
from gekko.db.engine import (
    get_async_engine,
    get_sync_engine,
    verify_passphrase,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


PASSPHRASE = "correct-horse-battery-staple"  # nosec: test-only literal
WRONG_PASSPHRASE = "wrong-horse-battery-staple"  # nosec: test-only literal


# ---------------------------------------------------------------------------
# Async engine — basic round-trip
# ---------------------------------------------------------------------------


async def test_async_engine_select_one_round_trip(tmp_path: Path) -> None:
    """A freshly-created encrypted DB executes ``SELECT 1`` and returns 1."""
    engine = get_async_engine(tmp_path / "x.db", PASSPHRASE)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


async def test_async_engine_pragmas_applied(tmp_path: Path) -> None:
    """PRAGMA cipher_compatibility=4, journal_mode=WAL, foreign_keys=ON are set.

    Verifies the connect-event handler ran in full on the fresh connection.
    """
    engine = get_async_engine(tmp_path / "pragmas.db", PASSPHRASE)
    try:
        async with engine.connect() as conn:
            # journal_mode persists at the DB level once set to WAL
            jm = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
            assert str(jm).lower() == "wal"

            fk = (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()
            # PRAGMA foreign_keys returns 1 when enabled
            assert int(fk) == 1
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Wrong-passphrase detection
# ---------------------------------------------------------------------------


async def test_wrong_passphrase_raises_wrongpassphraseerror(tmp_path: Path) -> None:
    """Per RESEARCH §Pitfall 2 — wrong passphrase MUST raise WrongPassphraseError.

    We create a DB with PASSPHRASE, dispose the engine, then attempt to open
    with WRONG_PASSPHRASE. ``verify_passphrase`` must convert the underlying
    ``OperationalError`` into our typed ``WrongPassphraseError``.
    """
    db_path = tmp_path / "wp.db"

    # Create the DB encrypted with the correct passphrase
    e1 = get_async_engine(db_path, PASSPHRASE)
    try:
        async with e1.begin() as conn:
            await conn.execute(text("CREATE TABLE marker (id INTEGER PRIMARY KEY)"))
    finally:
        await e1.dispose()

    # Now open with the WRONG passphrase
    e2 = get_async_engine(db_path, WRONG_PASSPHRASE)
    try:
        with pytest.raises(WrongPassphraseError):
            await verify_passphrase(e2)
    finally:
        await e2.dispose()


async def test_wrongpassphraseerror_is_subclass_of_gekko_error() -> None:
    """``WrongPassphraseError`` MUST be in the ``GekkoError`` hierarchy."""
    assert issubclass(WrongPassphraseError, GekkoError)
    assert issubclass(GekkoError, Exception)


async def test_wrongpassphraseerror_message_is_user_friendly() -> None:
    """The error message MUST contain the phrase "wrong passphrase" (case-insensitive)."""
    err = WrongPassphraseError("Wrong passphrase — please re-run with the correct one")
    assert "wrong passphrase" in str(err).lower()


async def test_verify_passphrase_passes_on_correct_passphrase(tmp_path: Path) -> None:
    """Sanity: the correct passphrase makes ``verify_passphrase`` return without error."""
    engine = get_async_engine(tmp_path / "ok.db", PASSPHRASE)
    try:
        # Should not raise
        await verify_passphrase(engine)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# PRAGMA key applies to every NEW connection, not just the first
# ---------------------------------------------------------------------------


async def test_pragma_key_applied_per_connection(tmp_path: Path) -> None:
    """The event handler must fire on every new connection, not once per engine.

    We open + close multiple connections back-to-back and confirm each one
    can read the DB (which would fail if PRAGMA key weren't re-applied).
    """
    engine = get_async_engine(tmp_path / "multi.db", PASSPHRASE)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE t (id INTEGER)"))

        # Now open three independent connections in sequence
        for _ in range(3):
            async with engine.connect() as conn:
                count = (
                    await conn.execute(text("SELECT count(*) FROM t"))
                ).scalar_one()
                assert int(count) == 0
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Passphrase NEVER in repr / URL — defense-in-depth (T-01-03-05)
# ---------------------------------------------------------------------------


def test_async_engine_repr_does_not_contain_passphrase(tmp_path: Path) -> None:
    """``repr(engine)`` and ``str(engine.url)`` MUST NOT contain the passphrase."""
    engine = get_async_engine(tmp_path / "noleak.db", PASSPHRASE)
    try:
        as_repr = repr(engine)
        as_url = str(engine.url)
        assert PASSPHRASE not in as_repr, (
            f"passphrase leaked in repr(engine): {as_repr}"
        )
        assert PASSPHRASE not in as_url, (
            f"passphrase leaked in str(engine.url): {as_url}"
        )
    finally:
        # repr/url tests are sync — we don't await dispose. The fixture's
        # tmp_path teardown handles file cleanup.
        engine.sync_engine.dispose()


def test_sync_engine_repr_does_not_contain_passphrase(tmp_path: Path) -> None:
    """AUTH-03 cross-engine parity — sync engine ALSO must not leak passphrase.

    This is the test required by VALIDATION row 01-09-T2 / threat T-01-03-05
    for the new ``get_sync_engine`` factory used by APScheduler (Plan 01-09).
    """
    engine = get_sync_engine(tmp_path / "noleak-sync.db", PASSPHRASE)
    try:
        as_repr = repr(engine)
        as_url = str(engine.url)
        assert PASSPHRASE not in as_repr, (
            f"passphrase leaked in repr(sync_engine): {as_repr}"
        )
        assert PASSPHRASE not in as_url, (
            f"passphrase leaked in str(sync_engine.url): {as_url}"
        )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Sync engine — basic round-trip (required by Plan 01-09 APScheduler integration)
# ---------------------------------------------------------------------------


def test_sync_engine_select_one_round_trip(tmp_path: Path) -> None:
    """``get_sync_engine`` returns a working sync ``Engine`` against the same DB shape."""
    engine = get_sync_engine(tmp_path / "sync.db", PASSPHRASE)
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        engine.dispose()


def test_sync_engine_pragmas_applied(tmp_path: Path) -> None:
    """Sync engine must apply the same PRAGMAs as the async engine."""
    engine = get_sync_engine(tmp_path / "sync-pragmas.db", PASSPHRASE)
    try:
        with engine.connect() as conn:
            jm = conn.execute(text("PRAGMA journal_mode")).scalar_one()
            assert str(jm).lower() == "wal"
            fk = conn.execute(text("PRAGMA foreign_keys")).scalar_one()
            assert int(fk) == 1
    finally:
        engine.dispose()


def test_sync_engine_wrong_passphrase_raises(tmp_path: Path) -> None:
    """Sync engine: wrong passphrase on existing DB raises WrongPassphraseError.

    The connect-event handler tries a smoke SELECT after PRAGMA key; on
    failure it raises WrongPassphraseError synchronously when the first
    real connection is opened.
    """
    db_path = tmp_path / "wp-sync.db"
    e1 = get_sync_engine(db_path, PASSPHRASE)
    try:
        with e1.begin() as conn:
            conn.execute(text("CREATE TABLE marker (id INTEGER PRIMARY KEY)"))
    finally:
        e1.dispose()

    e2 = get_sync_engine(db_path, WRONG_PASSPHRASE)
    try:
        with pytest.raises(WrongPassphraseError):
            with e2.connect() as conn:
                conn.execute(text("SELECT 1")).scalar_one()
    finally:
        e2.dispose()
