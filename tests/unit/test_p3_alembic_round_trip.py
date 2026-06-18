"""Alembic 0004 round-trip test — Plan 03-01 Task 3.

Exercises upgrade head -> downgrade -1 -> upgrade head against a fresh
SQLCipher DB and asserts every new column + table is present after the
final upgrade.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


_TEST_PASSPHRASE = "test-passphrase"
_REQUIRED_ENV = {
    "GEKKO_DB_PASSPHRASE": _TEST_PASSPHRASE,
    "GEKKO_USER_ID": "round-trip-user",
    "ANTHROPIC_API_KEY": "test-anthropic",
    "ALPACA_PAPER_API_KEY": "test-alpaca-key",
    "ALPACA_PAPER_SECRET_KEY": "test-alpaca-secret",
    "SLACK_BOT_TOKEN": "xoxb-test-bot",
    "SLACK_SIGNING_SECRET": "test-signing",
    "SLACK_USER_ID": "U_TEST_USER",
}


def _run_alembic(cmd: list[str], data_dir: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **_REQUIRED_ENV, "GEKKO_DATA_DIR": str(data_dir)}
    return subprocess.run(  # nosec
        [sys.executable, "-m", "alembic", *cmd],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.asyncio
async def test_0004_alembic_round_trip(tmp_path: Path) -> None:
    """Upgrade -> downgrade -> upgrade round-trip for 0004_p3_hitl_ux.

    Skipped on Windows due to SQLCipher cross-process file-lock issue
    (same caveat as test_alembic_0002.py — two Alembic subprocesses
    fighting over the same SQLCipher DB file hang indefinitely on Windows).
    The migration logic is verified on non-Windows CI.
    """
    import platform

    if platform.system() == "Windows":
        pytest.skip("SQLCipher cross-process file-lock — skipped on Windows (see Plan 02-01 SUMMARY)")

    db_dir = tmp_path / "gekko-data"
    db_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: upgrade to head (0001 -> 0002 -> 0003 -> 0004)
    result = _run_alembic(["upgrade", "head"], data_dir=db_dir)
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    assert "0004_p3_hitl_ux" in result.stderr or "0004_p3_hitl_ux" in result.stdout

    # Step 2: downgrade by 1 (0004 -> 0003)
    result = _run_alembic(["downgrade", "-1"], data_dir=db_dir)
    assert result.returncode == 0, (
        f"alembic downgrade -1 failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )

    # Step 3: upgrade back to head (0003 -> 0004)
    result = _run_alembic(["upgrade", "head"], data_dir=db_dir)
    assert result.returncode == 0, (
        f"alembic upgrade head (re-up) failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    assert "0004_p3_hitl_ux" in result.stderr or "0004_p3_hitl_ux" in result.stdout

    # Step 4: verify all P3 columns + table exist in the final schema.
    # Open a direct SQLCipher connection to inspect the schema.
    import sqlcipher3.dbapi2 as sqlcipher  # type: ignore[import-not-found]

    user_id = "round-trip-user"
    db_path = db_dir / f"{user_id}.db"
    conn = sqlcipher.connect(str(db_path))
    try:
        conn.execute(f"PRAGMA key='{_TEST_PASSPHRASE}'")
        conn.execute("PRAGMA foreign_keys=ON")

        # slack_action_dedup table must exist
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "slack_action_dedup" in tables, f"slack_action_dedup not found in {tables}"

        # User quiet_hours_* + timezone columns
        user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        assert "quiet_hours_start" in user_cols
        assert "quiet_hours_end" in user_cols
        assert "timezone" in user_cols

        # Proposal expires_at + slack_message_ts + slack_message_channel
        proposal_cols = {r[1] for r in conn.execute("PRAGMA table_info(proposals)")}
        assert "expires_at" in proposal_cols
        assert "slack_message_ts" in proposal_cols
        assert "slack_message_channel" in proposal_cols
    finally:
        conn.close()
