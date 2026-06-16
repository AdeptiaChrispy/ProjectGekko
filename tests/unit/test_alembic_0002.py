"""Wave-0 stub — Alembic 0002 forward + backward migration.

# WAVE-0 STUB: owned by plan 02-01 Task 4 — DO NOT delete the skip until that plan's tasks land

Per plan 02-01 Task 4: this file is replaced with real assertions for forward
migration, account_mode backfill (BLOCKER #5), kind backfill, downgrade
round-trip, audit chain integrity, and ORM model attribute presence.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_0002_upgrade_creates_strategy_metadata_placeholder() -> None:
    """Will assert strategy_metadata table + columns exist after upgrade head."""
    pass


def test_0002_account_mode_backfill_paper_placeholder() -> None:
    """Will assert existing proposals get account_mode='PAPER' (BLOCKER #5)."""
    pass


def test_0002_downgrade_round_trips_placeholder() -> None:
    """Will assert downgrade -1 + upgrade head round-trips cleanly."""
    pass
