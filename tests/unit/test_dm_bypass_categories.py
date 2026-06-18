"""Tests for bypass-category DM routing — Plan 03-03 Task 2 (HITL-05).

Kill_active, executor_error, and first_live_fill DMs always fire regardless
of quiet hours (D-48 bypass categories).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_kill_state_dm_bypasses_quiet_hours() -> None:
    """kill_active category fires even when quiet-hours predicate returns True."""
    sent: list[str] = []

    async def fake_send_dm(user_id: str, text: str) -> None:
        sent.append(text)

    async def fake_in_window(user_id: str, now: object, **kwargs: object) -> bool:
        return True  # simulate quiet hours active

    with (
        patch(
            "gekko.execution.executor._send_slack_dm",
            side_effect=fake_send_dm,
        ),
        patch(
            "gekko.approval.quiet_hours._resolve_quiet_hours",
            side_effect=fake_in_window,
        ),
    ):
        from gekko.execution.executor import _send_slack_dm_respecting_quiet_hours

        await _send_slack_dm_respecting_quiet_hours(
            "u1", "Kill active!", category="kill_active"
        )

    assert sent == ["Kill active!"], f"Expected DM to fire, got: {sent}"


@pytest.mark.asyncio
async def test_error_dm_bypasses_quiet_hours() -> None:
    """executor_error category fires even when quiet-hours predicate returns True."""
    sent: list[str] = []

    async def fake_send_dm(user_id: str, text: str) -> None:
        sent.append(text)

    async def fake_in_window(user_id: str, now: object, **kwargs: object) -> bool:
        return True

    with (
        patch(
            "gekko.execution.executor._send_slack_dm",
            side_effect=fake_send_dm,
        ),
        patch(
            "gekko.approval.quiet_hours._resolve_quiet_hours",
            side_effect=fake_in_window,
        ),
    ):
        from gekko.execution.executor import _send_slack_dm_respecting_quiet_hours

        await _send_slack_dm_respecting_quiet_hours(
            "u1", "Broker error!", category="executor_error"
        )

    assert sent == ["Broker error!"], f"Expected DM to fire, got: {sent}"


@pytest.mark.asyncio
async def test_first_live_dm_bypasses_quiet_hours() -> None:
    """first_live_fill category fires even when quiet-hours predicate returns True."""
    sent: list[str] = []

    async def fake_send_dm(user_id: str, text: str) -> None:
        sent.append(text)

    async def fake_in_window(user_id: str, now: object, **kwargs: object) -> bool:
        return True

    with (
        patch(
            "gekko.execution.executor._send_slack_dm",
            side_effect=fake_send_dm,
        ),
        patch(
            "gekko.approval.quiet_hours._resolve_quiet_hours",
            side_effect=fake_in_window,
        ),
    ):
        from gekko.execution.executor import _send_slack_dm_respecting_quiet_hours

        await _send_slack_dm_respecting_quiet_hours(
            "u1", "First live fill!", category="first_live_fill"
        )

    assert sent == ["First live fill!"], f"Expected DM to fire, got: {sent}"
