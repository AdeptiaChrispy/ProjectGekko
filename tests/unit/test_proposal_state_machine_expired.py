"""Proposal state machine EXPIRED edge tests — Plan 03-01 Task 3.

Covers:
(a) PENDING -> EXPIRED valid via transition_status
(b) Re-entering EXPIRED is idempotent no-op (existing line 139-141 path)
(c) APPROVED -> EXPIRED raises ValueError
(d) expire_proposal convenience writes the audit event with D-50 payload shape

Body filled by Task 3 of this plan.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_pending_to_expired_valid() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-01 Task 3")


@pytest.mark.asyncio
async def test_expired_to_expired_idempotent() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-01 Task 3")


@pytest.mark.asyncio
async def test_approved_to_expired_raises() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-01 Task 3")


@pytest.mark.asyncio
async def test_expire_proposal_helper_audit_event() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-01 Task 3")
