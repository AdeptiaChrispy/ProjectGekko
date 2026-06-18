"""Regression test: alembic upgrade head succeeds on a seeded DB (FK rows present).

Covers the root cause identified in debug session alembic-fk-batch-migration:
``batch_alter_table("users")`` in 0002 and ``batch_alter_table("users")`` +
``batch_alter_table("proposals")`` in 0004 recreate their tables via the
CREATE-tmp -> COPY -> DROP -> RENAME pattern. With ``PRAGMA foreign_keys = ON``
and child rows in referencing tables, the DROP fails with:

    sqlcipher3.dbapi2.IntegrityError: FOREIGN KEY constraint failed
    [SQL: DROP TABLE users]

The fix in env.py disables FK enforcement on the raw DBAPI connection BEFORE
Alembic opens its transaction (where the pragma would be a no-op) and
re-enables it after commit.

This test:
1. Creates a fresh SQLCipher DB.
2. Runs ``alembic upgrade 0001_initial`` (stamps the DB at rev 0001).
3. Opens the DB directly and inserts a ``users`` row + all FK-dependent child
   rows that exist at rev 0001 (strategies, proposals, events, broker_credentials).
4. Closes all connections (critical — SQLCipher file lock).
5. Runs ``alembic upgrade head`` (0001 -> 0002 -> 0003 -> 0004).
6. Asserts the upgrade completed without FK errors.
7. Asserts the final schema has all expected columns.

Skipped on Windows: the subprocess + SQLCipher file-lock interaction requires
the engine to be fully disposed before launching a second alembic subprocess.
See test_alembic_0002.py for the same caveat.
"""

from __future__ import annotations

import gc
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest


_TEST_PASSPHRASE = "regression-fk-passphrase"  # nosec: test-only literal
_TEST_USER_ID = "fk-test-user"
_ALEMBIC_TIMEOUT_S = 60


def _alembic_env(db_dir: Path) -> dict[str, str]:
    """Build env for an Alembic subprocess targeting the per-test DB dir."""
    env = dict(os.environ)
    env.update(
        {
            "GEKKO_DB_PASSPHRASE": _TEST_PASSPHRASE,
            "GEKKO_USER_ID": _TEST_USER_ID,
            "GEKKO_DATA_DIR": str(db_dir),
            "ANTHROPIC_API_KEY": "test-anthropic",
            "ALPACA_PAPER_API_KEY": "test-alpaca-key",
            "ALPACA_PAPER_SECRET_KEY": "test-alpaca-secret",
            "SLACK_BOT_TOKEN": "xoxb-test-bot",
            "SLACK_SIGNING_SECRET": "test-signing",
            "SLACK_USER_ID": "U_TEST_USER",
        }
    )
    return env


def _run_alembic(args: list[str], db_dir: Path) -> subprocess.CompletedProcess[str]:
    """Run alembic in a subprocess; return the CompletedProcess (never raises)."""
    return subprocess.run(  # nosec
        [sys.executable, "-m", "alembic", *args],
        env=_alembic_env(db_dir),
        capture_output=True,
        text=True,
        check=False,
        timeout=_ALEMBIC_TIMEOUT_S,
    )


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason=(
        "SQLCipher cross-process file-lock: two sequential alembic subprocesses "
        "against the same SQLCipher DB file hang on Windows. "
        "See test_alembic_0002.py for the same caveat. "
        "Run this test on macOS/Linux CI."
    ),
)
def test_upgrade_head_succeeds_with_seeded_fk_rows(tmp_path: Path) -> None:
    """Upgrading from 0001 to head must succeed even when child rows exist.

    This is the regression guard for the FOREIGN KEY constraint failed bug:
    batch_alter_table on users/proposals drops those tables while child rows
    in strategies/proposals/events/broker_credentials reference them. With
    PRAGMA foreign_keys = ON inside a transaction, the DROP is refused.
    The env.py fix disables FK enforcement outside the transaction before
    batch DDL runs and re-enables it after.
    """
    import sqlcipher3.dbapi2 as sqlcipher  # type: ignore[import-not-found]

    db_dir = tmp_path / "gekko-data"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / f"{_TEST_USER_ID}.db"

    # --- Step 1: upgrade to 0001_initial (creates tables, no data). ---
    result = _run_alembic(["upgrade", "0001_initial"], db_dir)
    assert result.returncode == 0, (
        f"alembic upgrade 0001_initial failed:\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )

    # --- Step 2: seed FK-dependent rows directly via sqlcipher3. ---
    # We open the DB directly (not via SQLAlchemy) so there is no engine
    # handle lingering when the next alembic subprocess launches.
    conn = sqlcipher.connect(str(db_path))
    try:
        conn.execute(f"PRAGMA key='{_TEST_PASSPHRASE}'")
        conn.execute("PRAGMA cipher_compatibility = 4")
        # Enable FK enforcement for the seed inserts (normal runtime behavior).
        conn.execute("PRAGMA foreign_keys = ON")

        # users — the FK-referenced parent that batch_alter_table("users") drops.
        conn.execute(
            "INSERT INTO users (user_id, created_at) "
            "VALUES ('fk-user-001', '2026-06-17T00:00:00+00:00')"
        )

        # strategies — references users.user_id
        conn.execute(
            "INSERT INTO strategies "
            "(strategy_id, user_id, strategy_name, version, payload_json, created_at) "
            "VALUES ('strat-001', 'fk-user-001', 'test-strategy', 1, '{}', "
            "'2026-06-17T00:00:00+00:00')"
        )

        # proposals — references users.user_id + strategies.strategy_id
        conn.execute(
            "INSERT INTO proposals "
            "(proposal_id, user_id, strategy_id, status, payload_json, "
            "created_at, updated_at) "
            "VALUES ('prop-001', 'fk-user-001', 'strat-001', 'PENDING', '{}', "
            "'2026-06-17T00:00:00+00:00', '2026-06-17T00:00:00+00:00')"
        )

        # events — references users.user_id (strategy_id nullable, omit for simplicity)
        conn.execute(
            "INSERT INTO events "
            "(ts, user_id, event_type, payload_json, prev_hash, row_hash) "
            "VALUES ('2026-06-17T00:00:00+00:00', 'fk-user-001', 'decision', '{}', "
            "'0000000000000000000000000000000000000000000000000000000000000000', "
            "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"
        )

        # broker_credentials — references users.user_id
        conn.execute(
            "INSERT INTO broker_credentials "
            "(user_id, broker, key_blob, secret_blob, paper, created_at) "
            "VALUES ('fk-user-001', 'alpaca', 'key-blob', 'secret-blob', 1, "
            "'2026-06-17T00:00:00+00:00')"
        )

        conn.commit()

        # Verify FK rows are in place before running upgrade.
        (user_count,) = conn.execute(
            "SELECT count(*) FROM users WHERE user_id='fk-user-001'"
        ).fetchone()
        assert user_count == 1, "Seed users row missing"

        (strat_count,) = conn.execute(
            "SELECT count(*) FROM strategies WHERE user_id='fk-user-001'"
        ).fetchone()
        assert strat_count == 1, "Seed strategies row missing"

        (prop_count,) = conn.execute(
            "SELECT count(*) FROM proposals WHERE user_id='fk-user-001'"
        ).fetchone()
        assert prop_count == 1, "Seed proposals row missing"

        (event_count,) = conn.execute(
            "SELECT count(*) FROM events WHERE user_id='fk-user-001'"
        ).fetchone()
        assert event_count == 1, "Seed events row missing"

        (cred_count,) = conn.execute(
            "SELECT count(*) FROM broker_credentials WHERE user_id='fk-user-001'"
        ).fetchone()
        assert cred_count == 1, "Seed broker_credentials row missing"

    finally:
        conn.close()
        del conn

    # Force GC to ensure the file handle is released before the subprocess.
    gc.collect()

    # --- Step 3: upgrade from 0001 to head (0002 -> 0003 -> 0004). ---
    # This is the operation that previously failed with FOREIGN KEY constraint.
    result = _run_alembic(["upgrade", "head"], db_dir)
    assert result.returncode == 0, (
        f"alembic upgrade head FAILED on seeded DB (FK regression):\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}\n\n"
        "This is the FK batch-migration regression. See debug session "
        "alembic-fk-batch-migration for the fix in migrations/env.py."
    )

    # --- Step 4: verify final schema has all expected 0004 columns. ---
    conn = sqlcipher.connect(str(db_path))
    try:
        conn.execute(f"PRAGMA key='{_TEST_PASSPHRASE}'")
        conn.execute("PRAGMA cipher_compatibility = 4")

        # 0002: users should have kill_active columns.
        user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        assert "kill_active" in user_cols, f"kill_active missing from users: {user_cols}"
        assert "kill_active_since" in user_cols
        assert "kill_active_reason" in user_cols

        # 0002: proposals should have account_mode.
        prop_cols = {r[1] for r in conn.execute("PRAGMA table_info(proposals)")}
        assert "account_mode" in prop_cols, (
            f"account_mode missing from proposals: {prop_cols}"
        )

        # 0002: broker_credentials should have kind.
        cred_cols = {r[1] for r in conn.execute("PRAGMA table_info(broker_credentials)")}
        assert "kind" in cred_cols, (
            f"kind missing from broker_credentials: {cred_cols}"
        )

        # 0002: strategy_metadata table should exist.
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "strategy_metadata" in tables, (
            f"strategy_metadata missing from tables: {tables}"
        )

        # 0004: users should have quiet_hours_* + timezone.
        assert "quiet_hours_start" in user_cols
        assert "quiet_hours_end" in user_cols
        assert "timezone" in user_cols

        # 0004: proposals should have expires_at + slack_message_ts + slack_message_channel.
        assert "expires_at" in prop_cols
        assert "slack_message_ts" in prop_cols
        assert "slack_message_channel" in prop_cols

        # 0004: slack_action_dedup table should exist.
        assert "slack_action_dedup" in tables, (
            f"slack_action_dedup missing from tables: {tables}"
        )

        # Verify the seeded user row survived the migrations intact.
        (user_count,) = conn.execute(
            "SELECT count(*) FROM users WHERE user_id='fk-user-001'"
        ).fetchone()
        assert user_count == 1, (
            "Seeded user row missing after upgrade — data loss during migration"
        )

        # Verify proposals.account_mode was backfilled to 'PAPER' for the seeded row.
        (account_mode,) = conn.execute(
            "SELECT account_mode FROM proposals WHERE proposal_id='prop-001'"
        ).fetchone()
        assert account_mode == "PAPER", (
            f"Backfill failed: expected account_mode='PAPER', got {account_mode!r}"
        )

    finally:
        conn.close()
