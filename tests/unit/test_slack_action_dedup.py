"""Wave 0 stub — populated in Plan 03-02.

Tests for the ``claim_action`` dedup helper:
- first-write / duplicate behavior
- IntegrityError race handling
- dedup_click audit event
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_first_click_first_write() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-02")


@pytest.mark.asyncio
async def test_second_click_duplicate() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-02")


@pytest.mark.asyncio
async def test_integrity_error_returns_duplicate() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-02")


@pytest.mark.asyncio
async def test_dedup_click_audit_event() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-02")
