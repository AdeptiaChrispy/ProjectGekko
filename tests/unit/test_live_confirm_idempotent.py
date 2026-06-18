"""HITL-06 dashboard /live-confirm idempotency — Plan 02-06 Task 2.

Double-click defense: the operator clicking the dashboard "Confirm First
Live Trade" button twice MUST NOT advance the state machine or dispatch
the executor twice. Three idempotency layers per RESEARCH §7:

  1. The state machine itself is idempotent on same-target-status
     (Phase-1 transition_status invariant from Plan 01-08).
  2. The POST handler re-reads the row inside the transaction and
     returns the success template on APPROVED_LIVE without re-dispatching.
  3. The broker's deterministic client_order_id dedups any race-induced
     duplicate place_order POST (Knight Capital prevention per Pitfall 4).

These tests exercise layers 1 + 2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from gekko.dashboard.app import create_app
from gekko.db.models import Proposal as ProposalRow
from gekko.db.models import Strategy as StrategyRow
from gekko.db.models import User
from gekko.db.session import make_session_factory


def _seed_pending_live_proposal(
    sf: Any, *, proposal_id: str, status: str = "AWAITING_2ND_CHANNEL"
) -> None:
    """Insert a fixture proposal in the named status."""
    pass


async def _seed_user_strategy_proposal(
    sf: Any, *, proposal_id: str, status: str
) -> None:
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
                strategy_id="strat-live-conf",
                user_id="test-user",
                strategy_name="live-conf",
                version=1,
                payload_json="{}",
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        await session.flush()
        # Build a minimal TradeProposal JSON payload.
        payload_dict = {
            "user_id": "test-user",
            "strategy_name": "live-conf",
            "decision_id": proposal_id,
            "ticker": "NVDA",
            "company_name": None,
            "sector": None,
            "side": "buy",
            "qty": "5",
            "target_notional_usd": "500",
            "order_type": "limit",
            "limit_price": "100",
            "stop_price": None,
            "rationale": "live confirm idempotency test",
            "confidence": "0.5",
            "evidence": [
                {
                    "source_type": "alpaca_quote",
                    "source_url": "https://alpaca.markets/q/NVDA",
                    "fetched_at": "2026-06-08T11:30:00+00:00",
                    "summary": "$100",
                },
                {
                    "source_type": "finnhub_news",
                    "source_url": "https://finnhub.io/n/nvda",
                    "fetched_at": "2026-06-08T11:30:00+00:00",
                    "summary": "news",
                },
                {
                    "source_type": "edgar_filing",
                    "source_url": "https://sec.gov/edgar/nvda",
                    "fetched_at": "2026-06-08T11:30:00+00:00",
                    "summary": "10-Q",
                },
            ],
            "alternatives_considered": [
                {"description": "AMD", "why_rejected": "lower"}
            ],
            "client_order_id": "a" * 32,
            "account_mode": "LIVE",
        }
        import json as _json

        session.add(
            ProposalRow(
                proposal_id=proposal_id,
                user_id="test-user",
                strategy_id="strat-live-conf",
                status=status,
                payload_json=_json.dumps(payload_dict),
                client_order_id="a" * 32,
                broker_order_id=None,
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
                account_mode="LIVE",
            )
        )


@pytest.fixture
def app_with_state(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> Any:
    """Build a FastAPI app with state pre-populated (no real lifespan)."""
    # Need clean_settings_env semantics: ensure settings cache picks up
    # GEKKO_USER_ID=test-user.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "test-alpaca-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test-alpaca-secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-bot")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-signing")
    monkeypatch.setenv("SLACK_USER_ID", "U_TEST_USER")
    monkeypatch.setenv("GEKKO_USER_ID", "test-user")
    from gekko.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    app.state.engine = temp_sqlcipher_db
    app.state.kill_active = False
    # Plan 03-08 gated /live-confirm behind require_session. These tests
    # exercise the confirm route's business logic (idempotency, validation),
    # not the auth gate (covered by test_dashboard_auth_safety_routes.py), so
    # override the dependency to a fixed authenticated user — the canonical
    # FastAPI test idiom, scoped to this per-test app instance (no global state).
    from gekko.dashboard.routes import require_session

    app.dependency_overrides[require_session] = lambda: "test-user"
    return app


@pytest.mark.asyncio
async def test_live_confirm_double_post_is_idempotent(
    app_with_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two consecutive POSTs to /live-confirm/{id} dispatch the executor exactly once."""
    sf = make_session_factory(app_with_state.state.engine)
    proposal_id = "prop-" + uuid4().hex
    await _seed_user_strategy_proposal(
        sf, proposal_id=proposal_id, status="AWAITING_2ND_CHANNEL"
    )

    # Stub execute_proposal at the routes module level so we can count
    # dispatches and avoid hitting any broker / DB live path.
    dispatch_count: list[str] = []

    async def _fake_execute_proposal(pid: str, uid: str) -> None:
        dispatch_count.append(pid)

    from gekko.dashboard import routes as routes_mod
    # The route uses a local import: `from gekko.execution.executor import
    # execute_proposal as _execute_proposal`. We patch the source symbol
    # so subsequent local imports inside the route resolve to our stub.
    from gekko.execution import executor as executor_mod

    monkeypatch.setattr(
        executor_mod, "execute_proposal", _fake_execute_proposal
    )

    # Submit with a page_load_ts >= 5 seconds in the past so the timer
    # passes.
    import time as _time

    old_ts = _time.time() - 10

    async with AsyncClient(
        transport=ASGITransport(app=app_with_state),
        base_url="http://test",
    ) as ac:
        # First POST — should transition + dispatch.
        r1 = await ac.post(
            f"/live-confirm/{proposal_id}",
            data={
                "ack_real_money": "on",
                "ack_read_rationale": "on",
                "page_load_ts": str(old_ts),
            },
        )
        # Second POST — should detect APPROVED_LIVE and NOT re-dispatch.
        r2 = await ac.post(
            f"/live-confirm/{proposal_id}",
            data={
                "ack_real_money": "on",
                "ack_read_rationale": "on",
                "page_load_ts": str(old_ts),
            },
        )

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    # Both renders the success template; the second one with the
    # "already confirmed at" copy.
    assert "First live trade confirmed" in r1.text
    assert "First live trade confirmed" in r2.text
    assert "already confirmed at" in r2.text

    # Exactly one executor dispatch (the second POST is idempotent).
    assert len(dispatch_count) == 1


@pytest.mark.asyncio
async def test_live_confirm_rejects_missing_checkboxes(
    app_with_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(app_with_state.state.engine)
    proposal_id = "prop-" + uuid4().hex
    await _seed_user_strategy_proposal(
        sf, proposal_id=proposal_id, status="AWAITING_2ND_CHANNEL"
    )

    import time as _time

    old_ts = _time.time() - 10

    async with AsyncClient(
        transport=ASGITransport(app=app_with_state),
        base_url="http://test",
    ) as ac:
        # Missing ack_read_rationale
        r = await ac.post(
            f"/live-confirm/{proposal_id}",
            data={
                "ack_real_money": "on",
                "page_load_ts": str(old_ts),
            },
        )
    assert r.status_code == 400
    assert "acknowledgements are required" in r.text.lower() or (
        "both acknowledgements" in r.text.lower()
    )


@pytest.mark.asyncio
async def test_live_confirm_rejects_premature_submit(
    app_with_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(app_with_state.state.engine)
    proposal_id = "prop-" + uuid4().hex
    await _seed_user_strategy_proposal(
        sf, proposal_id=proposal_id, status="AWAITING_2ND_CHANNEL"
    )

    import time as _time

    # Submit RIGHT NOW (page_load_ts = current). Should fail 5s timer.
    recent_ts = _time.time()

    async with AsyncClient(
        transport=ASGITransport(app=app_with_state),
        base_url="http://test",
    ) as ac:
        r = await ac.post(
            f"/live-confirm/{proposal_id}",
            data={
                "ack_real_money": "on",
                "ack_read_rationale": "on",
                "page_load_ts": str(recent_ts),
            },
        )
    assert r.status_code == 400
    assert "5 seconds" in r.text or "5.00s" in r.text or (
        "read the trade details" in r.text.lower()
    )


@pytest.mark.asyncio
async def test_live_confirm_rejects_wrong_status(
    app_with_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(app_with_state.state.engine)
    proposal_id = "prop-" + uuid4().hex
    # Seed in FAILED status — should reject the confirm POST.
    await _seed_user_strategy_proposal(
        sf, proposal_id=proposal_id, status="FAILED"
    )

    import time as _time

    old_ts = _time.time() - 10
    async with AsyncClient(
        transport=ASGITransport(app=app_with_state),
        base_url="http://test",
    ) as ac:
        r = await ac.post(
            f"/live-confirm/{proposal_id}",
            data={
                "ack_real_money": "on",
                "ack_read_rationale": "on",
                "page_load_ts": str(old_ts),
            },
        )
    assert r.status_code == 400
    assert "FAILED" in r.text or "cannot be confirmed" in r.text.lower()
