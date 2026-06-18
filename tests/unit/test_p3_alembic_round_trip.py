"""Alembic 0004 round-trip test — Plan 03-01 Task 3.

Exercises upgrade head -> downgrade -1 -> upgrade head against a fresh
SQLCipher DB and asserts every new column + table is present after the
final upgrade.

Body filled by Task 3 of this plan (per Wave 0 spec).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_0004_alembic_round_trip() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-01 Task 3")
