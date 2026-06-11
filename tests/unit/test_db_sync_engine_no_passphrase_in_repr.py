"""AUTH-03 / T-01-03-05 regression — Plan 01-09 Task 2.

The synchronous SQLAlchemy ``Engine`` returned by
:func:`gekko.db.engine.get_sync_engine` MUST NEVER embed the SQLCipher
passphrase in its URL. ``repr(engine)`` / ``str(engine.url)`` typically
leak through error logs and APScheduler's job-store telemetry; a URL
like ``sqlite+pysqlcipher://:supersecret@/path`` would broadcast the
passphrase to anyone who can read a stack trace.

We assert the passphrase is absent from:
  * :func:`repr(engine)`
  * :func:`str(engine.url)`
  * :func:`repr(engine.url)` (defends against URL.__repr__ overriding
    URL.__str__ to render the password)
"""

from __future__ import annotations

from pathlib import Path


_PASSPHRASE = "super-secret-passphrase-xyz-9001"


def test_sync_engine_repr_does_not_leak_passphrase(tmp_path: Path) -> None:
    from gekko.db.engine import get_sync_engine

    engine = get_sync_engine(tmp_path / "test.db", _PASSPHRASE)
    try:
        engine_repr = repr(engine)
        url_str = str(engine.url)
        url_repr = repr(engine.url)
    finally:
        engine.dispose()

    assert _PASSPHRASE not in engine_repr, engine_repr
    assert _PASSPHRASE not in url_str, url_str
    assert _PASSPHRASE not in url_repr, url_repr


def test_async_engine_repr_does_not_leak_passphrase(tmp_path: Path) -> None:
    """Belt-and-braces: same invariant for the async engine factory.

    Both ``get_async_engine`` and ``get_sync_engine`` use the same
    connect-event PRAGMA key pattern, so the URL-level assertion holds
    for both.
    """
    import asyncio

    from gekko.db.engine import get_async_engine

    async def _build_and_check() -> tuple[str, str, str]:
        engine = get_async_engine(tmp_path / "test_async.db", _PASSPHRASE)
        try:
            return repr(engine), str(engine.url), repr(engine.url)
        finally:
            await engine.dispose()

    engine_repr, url_str, url_repr = asyncio.run(_build_and_check())

    assert _PASSPHRASE not in engine_repr, engine_repr
    assert _PASSPHRASE not in url_str, url_str
    assert _PASSPHRASE not in url_repr, url_repr
