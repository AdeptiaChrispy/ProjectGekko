"""Plan 01-03 Task 3 — SQLCipher + Alembic integration tests.

Per VALIDATION row 01-03-T3: ``alembic upgrade head`` succeeds on a fresh
encrypted DB; wrong passphrase after migration is rejected; double-run is
idempotent.

These tests invoke Alembic via subprocess so the env-var passphrase plumbing
matches the production ``gekko init`` flow (Plan 01-09). They are marked
``@pytest.mark.integration`` so the unit-only quick-feedback path
(``uv run pytest tests/unit -q``) does not pay the migration cost.

Note: VALIDATION.md §"Manual-Only Verifications" row 3 says cross-platform
wrong-passphrase parity is a Windows-specific manual gate. The
``test_wrong_passphrase_after_migration_rejects`` test below runs on the
executor's OS (Windows in this case); a fresh-Mac run remains a manual
phase-gate verification.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect

from gekko.core.errors import WrongPassphraseError
from gekko.db.engine import get_async_engine, verify_passphrase


def _build_env(*, db_dir: Path, passphrase: str, user_id: str = "test-user") -> dict[str, str]:
    """Return the env-var dict alembic + Settings need to construct cleanly."""
    return {
        **os.environ,
        "GEKKO_DB_PASSPHRASE": passphrase,
        "GEKKO_USER_ID": user_id,
        "GEKKO_DATA_DIR": str(db_dir),
        # Minimal required Settings env.
        "ANTHROPIC_API_KEY": "test-anthropic",
        "ALPACA_PAPER_API_KEY": "test-alpaca-key",
        "ALPACA_PAPER_SECRET_KEY": "test-alpaca-secret",
        "SLACK_BOT_TOKEN": "xoxb-test-bot",
        "SLACK_SIGNING_SECRET": "test-signing",
        "SLACK_USER_ID": "U_TEST_USER",
    }


def _run_alembic_upgrade(env: dict[str, str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run ``python -m alembic upgrade head`` with the given env."""
    return subprocess.run(  # nosec
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.integration
async def test_alembic_upgrade_on_fresh_encrypted_db(tmp_path: Path) -> None:
    """alembic upgrade head creates the 6 P1 tables on a fresh encrypted DB.

    After the migration we re-open the DB with ``get_async_engine`` (the
    same connect-event-handler path the runtime uses) and confirm all 6
    tables are present.
    """
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    passphrase = "integration-passphrase-1"
    env = _build_env(db_dir=db_dir, passphrase=passphrase)

    # Run from the repo root so alembic.ini is discoverable.
    repo_root = Path(__file__).resolve().parents[2]
    result = _run_alembic_upgrade(env, cwd=repo_root)

    assert result.returncode == 0, (
        f"alembic upgrade head failed (exit {result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    # Confirm all 6 P1 tables exist + alembic_version table.
    db_path = db_dir / "test-user.db"
    assert db_path.exists(), "migration didn't create the per-user DB file"

    engine = get_async_engine(db_path, passphrase)
    try:

        def _tables(sync_conn: object) -> set[str]:
            return set(inspect(sync_conn).get_table_names())

        async with engine.connect() as conn:
            tables = await conn.run_sync(_tables)
    finally:
        await engine.dispose()

    assert {
        "users",
        "strategies",
        "guidance",
        "proposals",
        "events",
        "broker_credentials",
    }.issubset(tables), f"missing tables: {tables}"


@pytest.mark.integration
async def test_wrong_passphrase_after_migration_rejects(tmp_path: Path) -> None:
    """Opening a migrated DB with the wrong passphrase raises WrongPassphraseError.

    AUTH-03 contract: cross-platform parity is the load-bearing behavior;
    this test runs on the executor's OS (per VALIDATION manual-only note,
    a fresh-Mac confirmation remains a phase-gate manual verification).
    """
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    correct = "integration-correct-pp"
    wrong = "WRONG-PASSPHRASE"
    env = _build_env(db_dir=db_dir, passphrase=correct)

    repo_root = Path(__file__).resolve().parents[2]
    result = _run_alembic_upgrade(env, cwd=repo_root)
    assert result.returncode == 0, (
        f"setup migration failed:\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )

    db_path = db_dir / "test-user.db"
    engine = get_async_engine(db_path, wrong)
    try:
        with pytest.raises(WrongPassphraseError):
            await verify_passphrase(engine)
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_idempotent_upgrade(tmp_path: Path) -> None:
    """Running alembic upgrade head twice is a no-op the second time."""
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    passphrase = "integration-idempotent-pp"
    env = _build_env(db_dir=db_dir, passphrase=passphrase)
    repo_root = Path(__file__).resolve().parents[2]

    first = _run_alembic_upgrade(env, cwd=repo_root)
    assert first.returncode == 0

    second = _run_alembic_upgrade(env, cwd=repo_root)
    assert second.returncode == 0, (
        f"second run failed:\nSTDOUT:\n{second.stdout}\n"
        f"STDERR:\n{second.stderr}"
    )

    # Confirm tables still exist and no schema corruption.
    db_path = db_dir / "test-user.db"
    engine = get_async_engine(db_path, passphrase)
    try:

        def _tables(sync_conn: object) -> set[str]:
            return set(inspect(sync_conn).get_table_names())

        async with engine.connect() as conn:
            tables = await conn.run_sync(_tables)
    finally:
        await engine.dispose()

    assert {
        "users",
        "strategies",
        "guidance",
        "proposals",
        "events",
        "broker_credentials",
    }.issubset(tables)
