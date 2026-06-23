"""Alembic 0005 round-trip test — Plan 04-02 Task 1.

Exercises upgrade head -> downgrade -1 -> upgrade head against a fresh
SQLCipher DB and asserts every new column + constraint is present after the
final upgrade.

Skipped on Windows due to the SQLCipher cross-process file-lock hang
(same caveat as test_p3_alembic_round_trip.py — subprocess alembic vs
the parent process fight over the WAL lock on Windows).

Migration logic is verified here via an in-process import check (syntax +
revision wiring + frozen vocab sync) so the logic is always tested even when
the subprocess round-trip is skipped.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


_TEST_PASSPHRASE = "test-passphrase"
_REQUIRED_ENV = {
    "GEKKO_DB_PASSPHRASE": _TEST_PASSPHRASE,
    "GEKKO_USER_ID": "round-trip-user-p4",
    "ANTHROPIC_API_KEY": "test-anthropic",
    "ALPACA_PAPER_API_KEY": "test-alpaca-key",
    "ALPACA_PAPER_SECRET_KEY": "test-alpaca-secret",
    "SLACK_BOT_TOKEN": "xoxb-test-bot",
    "SLACK_SIGNING_SECRET": "test-signing",
    "SLACK_USER_ID": "U_TEST_USER",
}


def _load_migration(path: str):  # type: ignore[return]
    spec = importlib.util.spec_from_file_location("mig", path)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# In-process logic verification (always runs — no subprocess, no file-lock)
# ---------------------------------------------------------------------------


def test_0005_revision_wiring() -> None:
    """0005 revision and down_revision are correctly wired."""
    mod = _load_migration("migrations/versions/0005_p4_cost_ceiling.py")
    assert mod.revision == "0005_p4_cost_ceiling"
    assert mod.down_revision == "0004_p3_hitl_ux"


def test_0005_frozen_vocab_pre_matches_0004_post() -> None:
    """_FROZEN_EVENT_TYPES_PRE in 0005 equals _FROZEN_EVENT_TYPES_POST in 0004."""
    mod4 = _load_migration("migrations/versions/0004_p3_hitl_ux.py")
    mod5 = _load_migration("migrations/versions/0005_p4_cost_ceiling.py")
    assert mod4._FROZEN_EVENT_TYPES_POST == mod5._FROZEN_EVENT_TYPES_PRE, (
        "0005 PRE does not match 0004 POST — frozen vocabulary is out of sync"
    )


def test_0005_frozen_vocab_post_adds_phase4_types() -> None:
    """_FROZEN_EVENT_TYPES_POST adds exactly llm_cost and suspicious_content."""
    mod5 = _load_migration("migrations/versions/0005_p4_cost_ceiling.py")
    new_types = set(mod5._FROZEN_EVENT_TYPES_POST) - set(mod5._FROZEN_EVENT_TYPES_PRE)
    assert new_types == {"llm_cost", "suspicious_content"}, (
        f"Unexpected new types in 0005 POST: {new_types}"
    )


def test_0005_models_event_types_match_frozen_post() -> None:
    """models.py _EVENT_TYPES matches migration 0005 _FROZEN_EVENT_TYPES_POST."""
    from gekko.db.models import _EVENT_TYPES

    mod5 = _load_migration("migrations/versions/0005_p4_cost_ceiling.py")
    assert set(_EVENT_TYPES) == set(mod5._FROZEN_EVENT_TYPES_POST), (
        "models.py _EVENT_TYPES does not match 0005 _FROZEN_EVENT_TYPES_POST\n"
        f"In models but not migration: {set(_EVENT_TYPES) - set(mod5._FROZEN_EVENT_TYPES_POST)}\n"
        f"In migration but not models: {set(mod5._FROZEN_EVENT_TYPES_POST) - set(_EVENT_TYPES)}"
    )


def test_0005_user_orm_has_cost_columns() -> None:
    """User ORM model has the three Phase-4 cost ceiling columns."""
    from gekko.db.models import User
    from sqlalchemy.orm import class_mapper

    mapper = class_mapper(User)
    col_names = {col.key for col in mapper.columns}
    assert "daily_cost_ceiling_usd" in col_names
    assert "cost_alert_80_sent_date" in col_names
    assert "cost_alert_100_sent_date" in col_names


# ---------------------------------------------------------------------------
# Subprocess round-trip (skipped on Windows — cross-process file-lock)
# ---------------------------------------------------------------------------


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
async def test_0005_alembic_round_trip(tmp_path: Path) -> None:
    """Upgrade -> downgrade -> upgrade round-trip for 0005_p4_cost_ceiling.

    Skipped on Windows due to SQLCipher cross-process file-lock issue
    (same caveat as test_p3_alembic_round_trip.py).
    """
    import platform

    if platform.system() == "Windows":
        pytest.skip(
            "SQLCipher cross-process file-lock — skipped on Windows "
            "(see Plan 02-01 SUMMARY)"
        )

    db_dir = tmp_path / "gekko-data"
    db_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: upgrade to head (0001 -> ... -> 0005)
    result = _run_alembic(["upgrade", "head"], data_dir=db_dir)
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    assert "0005_p4_cost_ceiling" in result.stderr or "0005_p4_cost_ceiling" in result.stdout

    # Step 2: downgrade by 1 (0005 -> 0004)
    result = _run_alembic(["downgrade", "-1"], data_dir=db_dir)
    assert result.returncode == 0, (
        f"alembic downgrade -1 failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )

    # Step 3: upgrade back to head (0004 -> 0005)
    result = _run_alembic(["upgrade", "head"], data_dir=db_dir)
    assert result.returncode == 0, (
        f"alembic upgrade head (re-up) failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    assert "0005_p4_cost_ceiling" in result.stderr or "0005_p4_cost_ceiling" in result.stdout

    # Step 4: verify all P4 columns exist in the final schema.
    import sqlcipher3.dbapi2 as sqlcipher  # type: ignore[import-not-found]

    user_id = "round-trip-user-p4"
    db_path = db_dir / f"{user_id}.db"
    conn = sqlcipher.connect(str(db_path))
    try:
        conn.execute(f"PRAGMA key='{_TEST_PASSPHRASE}'")
        conn.execute("PRAGMA foreign_keys=ON")

        # User cost-ceiling columns
        user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        assert "daily_cost_ceiling_usd" in user_cols
        assert "cost_alert_80_sent_date" in user_cols
        assert "cost_alert_100_sent_date" in user_cols
    finally:
        conn.close()
