"""Tests for routine DM suppression during quiet hours — Plan 03-03 Task 2 (HITL-05).

Routine fill DMs (category="routine_fill") are suppressed when
_resolve_quiet_hours returns True; they fire when it returns False.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_routine_fill_suppressed_in_quiet_hours() -> None:
    """Routine fill DM is suppressed when quiet-hours predicate returns True."""
    sent: list[str] = []

    async def fake_send_dm(user_id: str, text: str) -> None:
        sent.append(text)

    async def fake_in_window(user_id: str, now: object, **kwargs: object) -> bool:
        return True  # quiet hours active

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
            "u1", "Paper fill: NVDA", category="routine_fill"
        )

    assert sent == [], f"Expected DM to be suppressed, got: {sent}"


@pytest.mark.asyncio
async def test_routine_fill_sent_outside_quiet_hours() -> None:
    """Routine fill DM fires when quiet-hours predicate returns False."""
    sent: list[str] = []

    async def fake_send_dm(user_id: str, text: str) -> None:
        sent.append(text)

    async def fake_not_in_window(user_id: str, now: object, **kwargs: object) -> bool:
        return False  # outside quiet hours

    with (
        patch(
            "gekko.execution.executor._send_slack_dm",
            side_effect=fake_send_dm,
        ),
        patch(
            "gekko.approval.quiet_hours._resolve_quiet_hours",
            side_effect=fake_not_in_window,
        ),
    ):
        from gekko.execution.executor import _send_slack_dm_respecting_quiet_hours

        await _send_slack_dm_respecting_quiet_hours(
            "u1", "Paper fill: NVDA", category="routine_fill"
        )

    assert sent == ["Paper fill: NVDA"], f"Expected DM to fire, got: {sent}"
