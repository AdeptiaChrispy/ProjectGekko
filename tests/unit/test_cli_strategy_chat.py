"""Tests for ``gekko strategy create`` — flag mode + chat mode (STRAT-01).

Four behaviors per Plan 01-09 Task 1:

1. Chat mode: ``--from-chat`` reads stdin, calls
   ``compile_strategy_from_chat`` once with that transcript, and
   persists a StrategyRow with ``created_by_chat=True`` at version
   assigned by :func:`next_version`.
2. Mutual exclusion: ``--from-chat`` + ``--name foo`` exits with code 2.
3. Empty stdin in chat mode exits with code 2.
4. Flag mode without ``--name`` (or ``--thesis`` / ``--watchlist``)
   exits with code 2.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from gekko.cli import app
from gekko.db.engine import get_async_engine
from gekko.db.models import Base, Strategy as StrategyRow, User
from gekko.db.session import make_session_factory
from gekko.schemas.strategy import HardCaps, Strategy

_PASSPHRASE = "test-passphrase-xyz"


@pytest.fixture(autouse=True)
def _isolate_vault() -> Any:
    from gekko.vault import passphrase as vault

    vault.clear()
    yield
    vault.clear()


@pytest.fixture
def _seeded_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings_env: pytest.MonkeyPatch,
) -> Path:
    """Seed an encrypted DB with the User row + cache the passphrase."""
    monkeypatch.setenv("GEKKO_DATA_DIR", str(tmp_path))

    from gekko.config import get_settings
    from gekko.vault.passphrase import set_passphrase

    get_settings.cache_clear()
    settings = get_settings()
    set_passphrase(_PASSPHRASE)

    db_path = tmp_path / f"{settings.gekko_user_id}.db"

    async def _seed() -> None:
        engine = get_async_engine(db_path, _PASSPHRASE)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with make_session_factory(engine)() as session, session.begin():
                session.add(
                    User(
                        user_id=settings.gekko_user_id,
                        created_at=datetime.now(UTC).isoformat(),
                        agreement_acknowledged_at=datetime.now(UTC).isoformat(),
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(_seed())
    return tmp_path


def _canned_strategy(*, name: str = "ai-infra-bull") -> Strategy:
    return Strategy(
        strategy_id="strat-" + "a" * 32,
        user_id="placeholder",
        name=name,
        version=1,
        thesis="Bullish on AI infrastructure providers.",
        watchlist=["NVDA", "AMD", "AVGO"],
        hard_caps=HardCaps(
            max_position_pct=Decimal("0.05"),
            max_daily_loss_usd=Decimal("200"),
            max_trades_per_day=3,
            max_sector_exposure_pct=Decimal("0.25"),
        ),
        created_at=datetime.now(UTC).isoformat(),
        created_by_chat=False,
    )


def test_strategy_create_from_chat_reads_stdin_and_calls_compiler(
    _seeded_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--from-chat: stdin -> compiler -> persisted row with created_by_chat=True."""
    transcript = "Bullish on AI infra. Watch NVDA, AMD, AVGO."

    canned = _canned_strategy()
    fake_compile = AsyncMock(return_value=canned)
    # The CLI does a deferred `from gekko.agent.runtime import compile_strategy_from_chat`
    # at the call site — patch the source module so the lazy import picks up the mock.
    monkeypatch.setattr(
        "gekko.agent.runtime.compile_strategy_from_chat", fake_compile
    )

    runner = CliRunner()
    result = runner.invoke(app, ["strategy", "create", "--from-chat"], input=transcript)

    assert result.exit_code == 0, result.output

    fake_compile.assert_awaited_once()
    # The compiler was called with the EXACT stdin transcript.
    call_kwargs = fake_compile.await_args.kwargs
    assert call_kwargs["chat_transcript"] == transcript

    # And a StrategyRow exists in the DB with created_by_chat=True.
    from gekko.config import get_settings

    settings = get_settings()
    db_path = _seeded_env / f"{settings.gekko_user_id}.db"

    async def _read() -> StrategyRow | None:
        engine = get_async_engine(db_path, _PASSPHRASE)
        try:
            async with make_session_factory(engine)() as session:
                return (
                    await session.execute(
                        select(StrategyRow)
                        .where(StrategyRow.user_id == settings.gekko_user_id)
                        .order_by(StrategyRow.version.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
        finally:
            await engine.dispose()

    row = asyncio.run(_read())
    assert row is not None
    assert row.version == 1  # next_version assigned 1 for first row
    payload = json.loads(row.payload_json)
    assert payload["created_by_chat"] is True


def test_strategy_create_from_chat_rejects_flag_combination(
    _seeded_env: Path,
) -> None:
    """--from-chat + --name foo exits code 2 with a clear error."""
    runner = CliRunner()
    result = runner.invoke(
        app, ["strategy", "create", "--from-chat", "--name", "foo"]
    )
    assert result.exit_code == 2, result.output
    assert "mutually exclusive" in result.output.lower()


def test_strategy_create_from_chat_rejects_empty_stdin(
    _seeded_env: Path,
) -> None:
    """--from-chat with empty stdin exits code 2 with a clear error."""
    runner = CliRunner()
    result = runner.invoke(app, ["strategy", "create", "--from-chat"], input="")
    assert result.exit_code == 2, result.output
    assert "non-empty" in result.output.lower()


def test_strategy_create_flag_mode_requires_all_three_inputs(
    _seeded_env: Path,
) -> None:
    """flag mode without --name/--thesis/--watchlist exits code 2."""
    runner = CliRunner()
    result = runner.invoke(app, ["strategy", "create"])
    assert result.exit_code == 2, result.output
    assert "flag mode requires" in result.output.lower()
