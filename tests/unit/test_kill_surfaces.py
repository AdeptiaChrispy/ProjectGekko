"""Three kill surfaces — Plan 02-05 Task 2 (D-38 / UI-SPEC §2 / Slack parallel).

Covers the three trigger surfaces:

* Slack ``/gekko kill`` (no-arg) → warn DM
* Slack ``/gekko kill CONFIRM`` → fires ``_execute_kill`` via asyncio.create_task
* Slack ``/gekko kill anything-else`` → mismatch DM
* Slack ``/gekko unkill`` + ``/gekko unkill CONFIRM`` symmetric
* Cross-user defense (Phase-1 pattern at commands.py:91-101)
* CLI ``gekko kill`` / ``gekko unkill`` typed-confirm flow

Dashboard form-route tests live in
``tests/integration/test_dashboard_kill.py`` (separate file).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Slack /gekko kill — two-step + cross-user defense
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slash_kill_no_arg_responds_with_warn(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """`/gekko kill` (no CONFIRM) returns the two-step warn DM — does NOT halt."""
    from gekko.slack import commands

    fired: list[dict[str, Any]] = []

    async def _fake_kill(**kwargs: Any) -> dict[str, Any]:
        fired.append(kwargs)
        return {"cancelled": 0, "total": 0}

    monkeypatch.setattr(
        "gekko.execution.kill_switch._execute_kill", _fake_kill
    )

    ack = AsyncMock()
    respond = AsyncMock()
    command = {"text": "kill", "user_id": "U_TEST_USER"}
    await commands.handle_gekko_command(
        ack=ack, command=command, respond=respond
    )

    ack.assert_awaited()
    respond.assert_awaited()
    body = str(respond.await_args)
    assert "CONFIRM" in body
    assert "halt all trading" in body.lower()
    # Background _execute_kill must NOT have fired without CONFIRM.
    await asyncio.sleep(0.05)
    assert fired == []


@pytest.mark.asyncio
async def test_slash_kill_confirm_fires_execute_kill(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """`/gekko kill CONFIRM` schedules ``_execute_kill`` via create_task."""
    from gekko.slack import commands

    fired: list[dict[str, Any]] = []

    async def _fake_kill(**kwargs: Any) -> dict[str, Any]:
        fired.append(kwargs)
        return {"cancelled": 0, "total": 0}

    monkeypatch.setattr(
        "gekko.execution.kill_switch._execute_kill", _fake_kill
    )

    ack = AsyncMock()
    respond = AsyncMock()
    command = {"text": "kill CONFIRM", "user_id": "U_TEST_USER"}
    await commands.handle_gekko_command(
        ack=ack, command=command, respond=respond
    )

    ack.assert_awaited()
    respond.assert_awaited()
    # Give the create_task time to run.
    await asyncio.sleep(0.05)

    assert len(fired) == 1
    assert fired[0]["user_id"] == "test-user"
    assert fired[0]["source"] == "slack"


@pytest.mark.asyncio
async def test_slash_kill_wrong_arg_responds_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """`/gekko kill anything-else` → mismatch DM, no kill fires."""
    from gekko.slack import commands

    fired: list[Any] = []

    async def _fake_kill(**kwargs: Any) -> dict[str, Any]:
        fired.append(kwargs)
        return {}

    monkeypatch.setattr(
        "gekko.execution.kill_switch._execute_kill", _fake_kill
    )

    ack = AsyncMock()
    respond = AsyncMock()
    command = {"text": "kill stop-now", "user_id": "U_TEST_USER"}
    await commands.handle_gekko_command(
        ack=ack, command=command, respond=respond
    )

    body = str(respond.await_args)
    assert "CONFIRM" in body
    await asyncio.sleep(0.05)
    assert fired == []


@pytest.mark.asyncio
async def test_slash_unkill_no_arg_responds_with_warn(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """`/gekko unkill` (no CONFIRM) returns the unkill warn DM."""
    from gekko.slack import commands

    fired: list[Any] = []

    async def _fake_unkill(**kwargs: Any) -> None:
        fired.append(kwargs)

    monkeypatch.setattr(
        "gekko.execution.kill_switch._execute_unkill", _fake_unkill
    )

    ack = AsyncMock()
    respond = AsyncMock()
    command = {"text": "unkill", "user_id": "U_TEST_USER"}
    await commands.handle_gekko_command(
        ack=ack, command=command, respond=respond
    )

    body = str(respond.await_args)
    assert "unkill confirm" in body.lower()
    await asyncio.sleep(0.05)
    assert fired == []


@pytest.mark.asyncio
async def test_slash_unkill_confirm_fires_execute_unkill(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """`/gekko unkill CONFIRM` schedules ``_execute_unkill``."""
    from gekko.slack import commands

    fired: list[dict[str, Any]] = []

    async def _fake_unkill(**kwargs: Any) -> None:
        fired.append(kwargs)

    monkeypatch.setattr(
        "gekko.execution.kill_switch._execute_unkill", _fake_unkill
    )

    ack = AsyncMock()
    respond = AsyncMock()
    command = {"text": "unkill CONFIRM", "user_id": "U_TEST_USER"}
    await commands.handle_gekko_command(
        ack=ack, command=command, respond=respond
    )

    await asyncio.sleep(0.05)
    assert len(fired) == 1
    assert fired[0]["user_id"] == "test-user"
    assert fired[0]["source"] == "slack"


@pytest.mark.asyncio
async def test_slash_kill_cross_user_defense(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """A different Slack user's `/gekko kill CONFIRM` is rejected."""
    from gekko.slack import commands

    fired: list[Any] = []

    async def _fake_kill(**kwargs: Any) -> dict[str, Any]:
        fired.append(kwargs)
        return {}

    monkeypatch.setattr(
        "gekko.execution.kill_switch._execute_kill", _fake_kill
    )

    ack = AsyncMock()
    respond = AsyncMock()
    # Different user from clean_settings_env's SLACK_USER_ID=U_TEST_USER.
    command = {"text": "kill CONFIRM", "user_id": "U_WRONG_USER"}
    await commands.handle_gekko_command(
        ack=ack, command=command, respond=respond
    )

    body = str(respond.await_args)
    assert "refused" in body.lower() or "bound to a different operator" in body
    await asyncio.sleep(0.05)
    assert fired == []


@pytest.mark.asyncio
async def test_slash_kill_acks_before_background(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """ack() awaited BEFORE _execute_kill schedules (Pitfall 3 invariant)."""
    from gekko.slack import commands

    events: list[str] = []

    async def _fake_kill(**_kwargs: Any) -> dict[str, Any]:
        events.append("kill")
        return {}

    monkeypatch.setattr(
        "gekko.execution.kill_switch._execute_kill", _fake_kill
    )

    ack = AsyncMock(side_effect=lambda: events.append("ack"))
    respond = AsyncMock(
        side_effect=lambda *_a, **_k: events.append("respond")
    )

    command = {"text": "kill CONFIRM", "user_id": "U_TEST_USER"}
    await commands.handle_gekko_command(
        ack=ack, command=command, respond=respond
    )

    assert events[0] == "ack"


# ---------------------------------------------------------------------------
# CLI gekko kill / unkill — typed-KILL / typed-UNKILL flow
# ---------------------------------------------------------------------------


def test_cli_kill_with_correct_typed_confirm_invokes_execute_kill(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """`gekko kill` + typing "KILL" calls _execute_kill."""
    from typer.testing import CliRunner

    fired: list[dict[str, Any]] = []

    async def _fake_kill(**kwargs: Any) -> dict[str, Any]:
        fired.append(kwargs)
        return {"cancelled": 0, "total": 0, "pending": 0, "failed": 0}

    # Patch BEFORE importing cli so the late-import inside the function picks it up.
    monkeypatch.setattr(
        "gekko.execution.kill_switch._execute_kill", _fake_kill
    )
    monkeypatch.setattr(
        "gekko.vault.passphrase.get_passphrase", lambda: "test-passphrase"
    )

    from gekko.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["kill"], input="KILL\n")

    assert result.exit_code == 0, result.output
    assert "Kill ACTIVE" in result.output
    assert len(fired) == 1
    assert fired[0]["user_id"] == "test-user"
    assert fired[0]["source"] == "cli"


def test_cli_kill_with_wrong_typed_confirm_aborts(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """`gekko kill` + typing "kill" (lowercase) exits non-zero with abort msg."""
    from typer.testing import CliRunner

    fired: list[Any] = []

    async def _fake_kill(**kwargs: Any) -> dict[str, Any]:
        fired.append(kwargs)
        return {}

    monkeypatch.setattr(
        "gekko.execution.kill_switch._execute_kill", _fake_kill
    )
    monkeypatch.setattr(
        "gekko.vault.passphrase.get_passphrase", lambda: "test-passphrase"
    )

    from gekko.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["kill"], input="kill\n")

    assert result.exit_code == 1
    assert "aborted" in result.output.lower()
    assert fired == []


def test_cli_unkill_with_correct_typed_confirm_invokes_execute_unkill(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """`gekko unkill` + "UNKILL" calls _execute_unkill."""
    from typer.testing import CliRunner

    fired: list[dict[str, Any]] = []

    async def _fake_unkill(**kwargs: Any) -> None:
        fired.append(kwargs)

    monkeypatch.setattr(
        "gekko.execution.kill_switch._execute_unkill", _fake_unkill
    )
    monkeypatch.setattr(
        "gekko.vault.passphrase.get_passphrase", lambda: "test-passphrase"
    )

    from gekko.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["unkill"], input="UNKILL\n")

    assert result.exit_code == 0, result.output
    assert len(fired) == 1
    assert fired[0]["user_id"] == "test-user"
    assert fired[0]["source"] == "cli"


def test_cli_unkill_with_wrong_typed_confirm_aborts(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> None:
    """`gekko unkill` + wrong input aborts."""
    from typer.testing import CliRunner

    fired: list[Any] = []

    async def _fake_unkill(**kwargs: Any) -> None:
        fired.append(kwargs)

    monkeypatch.setattr(
        "gekko.execution.kill_switch._execute_unkill", _fake_unkill
    )
    monkeypatch.setattr(
        "gekko.vault.passphrase.get_passphrase", lambda: "test-passphrase"
    )

    from gekko.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["unkill"], input="resume\n")

    assert result.exit_code == 1
    assert "aborted" in result.output.lower()
    assert fired == []


# ---------------------------------------------------------------------------
# Source-bytes — asyncio.create_task wires Slack + dashboard to _execute_kill
# ---------------------------------------------------------------------------


def test_slack_commands_wires_create_task_to_execute_kill() -> None:
    """Source-bytes check: commands.py uses asyncio.create_task on _execute_kill_background."""
    import gekko.slack.commands as mod
    from pathlib import Path

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "asyncio.create_task" in src
    assert "_execute_kill_background" in src
    assert "_execute_unkill_background" in src


def test_dashboard_routes_wires_create_task_to_execute_kill() -> None:
    """Source-bytes check: routes.py uses asyncio.create_task on _execute_kill_background."""
    import gekko.dashboard.routes as mod
    from pathlib import Path

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "asyncio.create_task" in src
    assert "_execute_kill_background" in src
