"""Strategy promotion CLI + dashboard symmetry — Plan 02-06 Task 2 (D-31).

Tests both ingress paths:
  * ``gekko strategy promote-live <name>`` (CLI) via direct module call.
  * ``POST /strategies/{name}/promote-to-live`` (dashboard).

Both require typed-strategy-name confirmation per UI-SPEC
§"Destructive Action Confirmations" and both flip
``strategy_metadata.live_mode_eligible=True``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from gekko.dashboard.app import create_app
from gekko.db.models import Strategy as StrategyRow
from gekko.db.models import StrategyMetadata
from gekko.db.models import User
from gekko.db.session import make_session_factory
from gekko.strategy import promotion as promotion_mod
from gekko.strategy.promotion import (
    demote_strategy_from_live,
    promote_strategy_to_live,
    stamp_first_live_trade,
)


def _patch_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "test-alpaca-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test-alpaca-secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-bot")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-signing")
    monkeypatch.setenv("SLACK_USER_ID", "U_TEST_USER")
    monkeypatch.setenv("GEKKO_USER_ID", "test-user")
    from gekko.config import get_settings

    get_settings.cache_clear()


async def _seed_user_and_strategy(sf: Any) -> None:
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id="test-user",
                created_at=datetime.now(UTC).isoformat(),
                kill_active=False,
            )
        )
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id="strat-promote",
                user_id="test-user",
                strategy_name="promote-me",
                version=1,
                payload_json="{}",
                created_at=datetime.now(UTC).isoformat(),
            )
        )


# ---------------------------------------------------------------------------
# Direct helpers (CLI uses these — exact same call path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_creates_metadata_row_and_sets_eligible(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_settings_env(monkeypatch)
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user_and_strategy(sf)
    monkeypatch.setattr(
        promotion_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    await promote_strategy_to_live(
        user_id="test-user", strategy_name="promote-me"
    )

    async with sf() as session:
        meta = await session.get(
            StrategyMetadata, ("test-user", "promote-me")
        )
    assert meta is not None
    assert meta.live_mode_eligible is True
    assert meta.live_promoted_at is not None
    assert meta.first_live_trade_confirmed_at is None


@pytest.mark.asyncio
async def test_promote_is_idempotent(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_settings_env(monkeypatch)
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user_and_strategy(sf)
    monkeypatch.setattr(
        promotion_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    await promote_strategy_to_live(
        user_id="test-user", strategy_name="promote-me"
    )
    await promote_strategy_to_live(
        user_id="test-user", strategy_name="promote-me"
    )

    async with sf() as session:
        rows = list(
            (
                await session.execute(
                    select(StrategyMetadata).where(
                        StrategyMetadata.user_id == "test-user"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1  # idempotent — still ONE metadata row


@pytest.mark.asyncio
async def test_demote_preserves_first_live_trade_stamp(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Demote sets live_mode_eligible=False but keeps first_live_trade_confirmed_at."""
    _patch_settings_env(monkeypatch)
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user_and_strategy(sf)
    monkeypatch.setattr(
        promotion_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    await promote_strategy_to_live(
        user_id="test-user", strategy_name="promote-me"
    )
    await stamp_first_live_trade(
        user_id="test-user",
        strategy_name="promote-me",
        fill_ts="2026-06-16T15:00:00+00:00",
    )

    async with sf() as session:
        meta = await session.get(
            StrategyMetadata, ("test-user", "promote-me")
        )
    assert meta is not None
    assert meta.first_live_trade_confirmed_at == "2026-06-16T15:00:00+00:00"

    await demote_strategy_from_live(
        user_id="test-user", strategy_name="promote-me"
    )

    async with sf() as session:
        meta = await session.get(
            StrategyMetadata, ("test-user", "promote-me")
        )
    assert meta is not None
    assert meta.live_mode_eligible is False
    # Stamp preserved.
    assert meta.first_live_trade_confirmed_at == "2026-06-16T15:00:00+00:00"


@pytest.mark.asyncio
async def test_stamp_first_live_trade_is_set_once(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second call to stamp_first_live_trade does NOT overwrite the first stamp."""
    _patch_settings_env(monkeypatch)
    sf = make_session_factory(temp_sqlcipher_db)
    await _seed_user_and_strategy(sf)
    monkeypatch.setattr(
        promotion_mod, "_get_session_factory", lambda _u: (sf, None)
    )
    await promote_strategy_to_live(
        user_id="test-user", strategy_name="promote-me"
    )

    await stamp_first_live_trade(
        user_id="test-user",
        strategy_name="promote-me",
        fill_ts="2026-06-16T15:00:00+00:00",
    )
    await stamp_first_live_trade(
        user_id="test-user",
        strategy_name="promote-me",
        fill_ts="2026-06-17T10:00:00+00:00",
    )

    async with sf() as session:
        meta = await session.get(
            StrategyMetadata, ("test-user", "promote-me")
        )
    assert meta is not None
    assert meta.first_live_trade_confirmed_at == "2026-06-16T15:00:00+00:00"


# ---------------------------------------------------------------------------
# Dashboard route — symmetric to CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def app_with_state(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> Any:
    _patch_settings_env(monkeypatch)
    app = create_app()
    app.state.engine = temp_sqlcipher_db
    app.state.kill_active = False
    return app


@pytest.mark.asyncio
async def test_dashboard_promote_with_correct_name_succeeds(
    app_with_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(app_with_state.state.engine)
    await _seed_user_and_strategy(sf)
    monkeypatch.setattr(
        promotion_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    async with AsyncClient(
        transport=ASGITransport(app=app_with_state),
        base_url="http://test",
    ) as ac:
        r = await ac.post(
            "/strategies/promote-me/promote-to-live",
            data={"strategy_name_confirm": "promote-me"},
        )
    assert r.status_code == 200, r.text
    assert "LIVE" in r.text

    async with sf() as session:
        meta = await session.get(
            StrategyMetadata, ("test-user", "promote-me")
        )
    assert meta is not None
    assert meta.live_mode_eligible is True


@pytest.mark.asyncio
async def test_dashboard_promote_with_wrong_name_rejects(
    app_with_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(app_with_state.state.engine)
    await _seed_user_and_strategy(sf)
    monkeypatch.setattr(
        promotion_mod, "_get_session_factory", lambda _u: (sf, None)
    )

    async with AsyncClient(
        transport=ASGITransport(app=app_with_state),
        base_url="http://test",
    ) as ac:
        r = await ac.post(
            "/strategies/promote-me/promote-to-live",
            data={"strategy_name_confirm": "wrong-name"},
        )
    assert r.status_code == 400
    assert "did not match" in r.text.lower() or "promote-me" in r.text

    async with sf() as session:
        meta = await session.get(
            StrategyMetadata, ("test-user", "promote-me")
        )
    # Promotion did NOT fire.
    assert meta is None
