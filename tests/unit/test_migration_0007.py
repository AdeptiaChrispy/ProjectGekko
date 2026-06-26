"""Alembic 0007 round-trip test — TRUST-* schema substrate (Plan 05-01 Task 2).

Exercises upgrade head -> downgrade -1 -> upgrade head against a fresh
SQLCipher DB and asserts every new column + constraint is present after the
final upgrade.

Skipped on Windows due to the SQLCipher cross-process file-lock hang (same
caveat as test_p4_alembic_round_trip.py — subprocess alembic vs the parent
process fight over the WAL lock on Windows).

Migration logic is verified here via an in-process import check (syntax +
revision wiring + frozen vocab sync) so the logic is always tested even when
the subprocess round-trip is skipped — these in-process tests gate Task 2.
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
    "GEKKO_USER_ID": "round-trip-user-p5",
    "ANTHROPIC_API_KEY": "test-anthropic",
    "ALPACA_PAPER_API_KEY": "test-alpaca-key",
    "ALPACA_PAPER_SECRET_KEY": "test-alpaca-secret",
    "SLACK_BOT_TOKEN": "xoxb-test-bot",
    "SLACK_SIGNING_SECRET": "test-signing",
    "SLACK_USER_ID": "U_TEST_USER",
}

_NEW_EVENT_TYPES = (
    "trust_promoted",
    "trust_demoted",
    "anomaly_demotion",
    "capital_scaled",
    "auto_execution",
)
_NEW_STRATEGY_COLS = (
    "trust_level",
    "trust_promoted_at",
    "capital_ceiling_usd",
    "anomaly_threshold_pct",
)
_NEW_USER_COLS = (
    "max_total_exposure_pct",
    "max_sector_concentration_pct",
    "max_correlated_ticker_pct",
    "max_total_daily_loss_usd",
)


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


def test_0007_revision_wiring() -> None:
    """0007 revision and down_revision chain from the 0006 repair head."""
    mod = _load_migration("migrations/versions/0007_p5_trust_ladder.py")
    assert mod.revision == "0007_p5_trust_ladder"
    # CRITICAL: down_revision MUST be 0006 (the current head), NOT 0005.
    assert mod.down_revision == "0006_p4_cost_ceiling_repair", (
        "0007 down_revision must be 0006_p4_cost_ceiling_repair (the current "
        f"head) — got {mod.down_revision!r}; pinning to 0005 would skip the "
        "0006 repair and corrupt the chain"
    )


def test_0007_frozen_vocab_pre_matches_0006() -> None:
    """_FROZEN_EVENT_TYPES_PRE in 0007 equals 0006's frozen vocabulary."""
    mod6 = _load_migration("migrations/versions/0006_p4_cost_ceiling_repair.py")
    mod7 = _load_migration("migrations/versions/0007_p5_trust_ladder.py")
    assert tuple(mod6._FROZEN_EVENT_TYPES) == tuple(mod7._FROZEN_EVENT_TYPES_PRE), (
        "0007 PRE does not match 0006 _FROZEN_EVENT_TYPES — frozen vocabulary "
        "is out of sync"
    )


def test_0007_frozen_vocab_post_adds_phase5_types() -> None:
    """_FROZEN_EVENT_TYPES_POST adds exactly the five Phase-5 event types."""
    mod7 = _load_migration("migrations/versions/0007_p5_trust_ladder.py")
    new = set(mod7._FROZEN_EVENT_TYPES_POST) - set(mod7._FROZEN_EVENT_TYPES_PRE)
    assert new == set(_NEW_EVENT_TYPES), (
        f"Unexpected new types in 0007 POST: {new} (expected {set(_NEW_EVENT_TYPES)})"
    )


def test_0007_models_event_types_match_frozen_post() -> None:
    """models.py _EVENT_TYPES matches migration 0007 _FROZEN_EVENT_TYPES_POST."""
    from gekko.db.models import _EVENT_TYPES

    mod7 = _load_migration("migrations/versions/0007_p5_trust_ladder.py")
    assert set(_EVENT_TYPES) == set(mod7._FROZEN_EVENT_TYPES_POST), (
        "models.py _EVENT_TYPES does not match 0007 _FROZEN_EVENT_TYPES_POST\n"
        f"In models but not migration: {set(_EVENT_TYPES) - set(mod7._FROZEN_EVENT_TYPES_POST)}\n"
        f"In migration but not models: {set(mod7._FROZEN_EVENT_TYPES_POST) - set(_EVENT_TYPES)}"
    )


def test_0007_strategy_metadata_orm_has_trust_columns() -> None:
    """StrategyMetadata ORM model has the four new trust/capital/anomaly columns."""
    from sqlalchemy.orm import class_mapper

    from gekko.db.models import StrategyMetadata

    col_names = {col.key for col in class_mapper(StrategyMetadata).columns}
    for c in _NEW_STRATEGY_COLS:
        assert c in col_names, f"StrategyMetadata missing column {c!r}"


def test_0007_user_orm_has_portfolio_cap_columns() -> None:
    """User ORM model has the four new portfolio-cap columns."""
    from sqlalchemy.orm import class_mapper

    from gekko.db.models import User

    col_names = {col.key for col in class_mapper(User).columns}
    for c in _NEW_USER_COLS:
        assert c in col_names, f"User missing column {c!r}"


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
async def test_0007_alembic_round_trip(tmp_path: Path) -> None:
    """Upgrade -> downgrade -> upgrade round-trip for 0007_p5_trust_ladder.

    Skipped on Windows due to SQLCipher cross-process file-lock issue
    (same caveat as test_p4_alembic_round_trip.py).
    """
    import platform

    if platform.system() == "Windows":
        pytest.skip(
            "SQLCipher cross-process file-lock — skipped on Windows "
            "(see Plan 02-01 SUMMARY)"
        )

    db_dir = tmp_path / "gekko-data"
    db_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: upgrade to head (0001 -> ... -> 0007)
    result = _run_alembic(["upgrade", "head"], data_dir=db_dir)
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )

    # Step 2: downgrade by 1 (0007 -> 0006)
    result = _run_alembic(["downgrade", "-1"], data_dir=db_dir)
    assert result.returncode == 0, (
        f"alembic downgrade -1 failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )

    # Step 3: upgrade back to head (0006 -> 0007)
    result = _run_alembic(["upgrade", "head"], data_dir=db_dir)
    assert result.returncode == 0, (
        f"alembic upgrade head (re-up) failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )

    # Step 4: verify all P5 columns + extended CHECK exist in the final schema.
    import sqlcipher3.dbapi2 as sqlcipher  # type: ignore[import-not-found]

    user_id = "round-trip-user-p5"
    db_path = db_dir / f"{user_id}.db"
    conn = sqlcipher.connect(str(db_path))
    try:
        conn.execute(f"PRAGMA key='{_TEST_PASSPHRASE}'")
        conn.execute("PRAGMA foreign_keys=ON")

        strat_cols = {r[1] for r in conn.execute("PRAGMA table_info(strategy_metadata)")}
        for c in _NEW_STRATEGY_COLS:
            assert c in strat_cols, f"strategy_metadata missing {c!r}"

        user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        for c in _NEW_USER_COLS:
            assert c in user_cols, f"users missing {c!r}"

        # The extended ck_event_type CHECK must accept a new event type.
        events_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='events'"
        ).fetchone()[0]
        for et in _NEW_EVENT_TYPES:
            assert et in events_sql, f"ck_event_type does not list {et!r}"
    finally:
        conn.close()
