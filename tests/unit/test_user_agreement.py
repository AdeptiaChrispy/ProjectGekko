"""Tests for the ``gekko init`` first-run wizard — REG-02.

Four behaviors per Plan 01-09 Task 1:

1. The user agreement TEXT appears in stdout during ``gekko init``.
2. If the operator types anything other than "I agree" (case-insensitive)
   ``gekko init`` exits with code 1 and writes no User row.
3. If the two passphrase prompts don't match, ``gekko init`` exits 1.
4. On a happy path the User row is inserted with
   ``agreement_acknowledged_at`` populated to an ISO-8601 timestamp.

All four tests avoid touching the real OS keyring / Slack / network by
monkeypatching :mod:`getpass`, :func:`typer.prompt`,
:func:`subprocess.run` (skip alembic), and routing the engine to a
``tmp_path`` SQLCipher DB.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from gekko.cli import app
from gekko.dashboard.templates import USER_AGREEMENT_TEXT
from gekko.db.engine import get_async_engine
from gekko.db.models import Base, User
from gekko.db.session import make_session_factory


_PASSPHRASE = "test-passphrase-123"


@pytest.fixture(autouse=True)
def _isolate_vault() -> Any:
    """Forget any cached passphrase between tests so each starts fresh."""
    from gekko.vault import passphrase as vault

    vault.clear()
    yield
    vault.clear()


@pytest.fixture
def _patched_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> Path:
    """Route the per-user DB into ``tmp_path`` and skip alembic subprocess."""
    monkeypatch.setenv("GEKKO_DATA_DIR", str(tmp_path))
    # Re-read settings so db_path_for picks up the override.
    from gekko.config import get_settings

    get_settings.cache_clear()

    # Pre-build the schema (we skip the alembic subprocess in init).
    async def _create_schema() -> None:
        db_path = tmp_path / f"{get_settings().gekko_user_id}.db"
        engine = get_async_engine(db_path, _PASSPHRASE)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    import asyncio

    asyncio.run(_create_schema())

    # Skip the alembic subprocess — tests pre-created the schema.
    monkeypatch.setattr(
        "gekko.cli.subprocess.run", lambda *a, **k: None
    )
    return tmp_path


def test_init_displays_user_agreement_text(
    _patched_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REG-02 — the agreement text must be visible to the operator."""
    inputs = iter([_PASSPHRASE, _PASSPHRASE])
    monkeypatch.setattr("gekko.cli.getpass.getpass", lambda *a, **k: next(inputs))
    monkeypatch.setattr("gekko.cli.typer.prompt", lambda *a, **k: "I agree")

    runner = CliRunner()
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    # A representative load-bearing sentence from the agreement text
    # must appear in stdout. We check a substring that's unique to the
    # agreement (so a future copy-edit catches the test).
    assert "personal trade-execution tooling" in USER_AGREEMENT_TEXT
    assert "personal trade-execution tooling" in result.output


def test_init_aborts_when_user_rejects_agreement(
    _patched_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typing anything other than 'I agree' must exit code 1."""
    inputs = iter([_PASSPHRASE, _PASSPHRASE])
    monkeypatch.setattr("gekko.cli.getpass.getpass", lambda *a, **k: next(inputs))
    monkeypatch.setattr("gekko.cli.typer.prompt", lambda *a, **k: "no")

    runner = CliRunner()
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1, result.output
    assert "not acknowledged" in result.output.lower()


def test_init_aborts_on_passphrase_mismatch(
    _patched_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two different passphrase entries must exit code 1 BEFORE agreement gate."""
    inputs = iter([_PASSPHRASE, "different-passphrase"])
    monkeypatch.setattr("gekko.cli.getpass.getpass", lambda *a, **k: next(inputs))
    # typer.prompt should NEVER be called — failure happens earlier.
    monkeypatch.setattr(
        "gekko.cli.typer.prompt",
        lambda *a, **k: pytest.fail("typer.prompt should not run on mismatch"),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1, result.output
    assert "did not match" in result.output.lower()


def test_init_writes_user_row_with_acknowledgment_timestamp(
    _patched_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: User row exists with ``agreement_acknowledged_at`` populated."""
    inputs = iter([_PASSPHRASE, _PASSPHRASE])
    monkeypatch.setattr("gekko.cli.getpass.getpass", lambda *a, **k: next(inputs))
    monkeypatch.setattr("gekko.cli.typer.prompt", lambda *a, **k: "I agree")

    runner = CliRunner()
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output

    from gekko.config import get_settings

    settings = get_settings()
    db_path = _patched_env / f"{settings.gekko_user_id}.db"

    async def _read_user() -> User | None:
        engine = get_async_engine(db_path, _PASSPHRASE)
        try:
            async with make_session_factory(engine)() as session:
                return (
                    await session.execute(
                        select(User).where(User.user_id == settings.gekko_user_id)
                    )
                ).scalar_one_or_none()
        finally:
            await engine.dispose()

    import asyncio

    user = asyncio.run(_read_user())
    assert user is not None
    assert user.agreement_acknowledged_at is not None
    # Loose ISO-8601 sanity check.
    assert "T" in user.agreement_acknowledged_at
    assert user.agreement_acknowledged_at.endswith("+00:00")
