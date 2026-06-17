"""HITL-06 dual-channel first-live-trade gate — Plan 02-06 Task 2.

End-to-end test: Slack approve handler diverts the FIRST live trade per
strategy to ``AWAITING_2ND_CHANNEL`` (does NOT dispatch the executor).
The dashboard ``/live-confirm/{id}`` POST transitions
``AWAITING_2ND_CHANNEL → APPROVED_LIVE`` and dispatches the executor.

After a successful live FILL, ``strategy_metadata.first_live_trade_confirmed_at``
is stamped. The next live approve for the same strategy takes the
standard single-channel path.
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio

from gekko.approval import slack_handler
from gekko.approval.slack_handler import _approve_workflow
from gekko.db.models import Proposal as ProposalRow
from gekko.db.models import Strategy as StrategyRow
from gekko.db.models import StrategyMetadata
from gekko.db.models import User
from gekko.db.session import make_session_factory
from gekko.execution import executor as executor_mod


def _live_proposal_json(decision_id: str) -> str:
    """Build a TradeProposal payload_json with account_mode='LIVE'."""
    return _json.dumps(
        {
            "user_id": "test-user",
            "strategy_name": "first-live",
            "decision_id": decision_id,
            "ticker": "NVDA",
            "company_name": None,
            "sector": None,
            "side": "buy",
            "qty": "5",
            "target_notional_usd": "500",
            "order_type": "limit",
            "limit_price": "100",
            "stop_price": None,
            "rationale": "first live test",
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
    )


async def _seed_user_strategy_proposal(
    sf: Any,
    *,
    decision_id: str,
    account_mode: str = "LIVE",
    status: str = "PENDING",
    metadata: dict[str, Any] | None = None,
) -> None:
    async with sf() as session, session.begin():
        # User
        session.add(
            User(
                user_id="test-user",
                created_at=datetime.now(UTC).isoformat(),
                kill_active=False,
            )
        )
        await session.flush()
        # Strategy
        session.add(
            StrategyRow(
                strategy_id="strat-first-live",
                user_id="test-user",
                strategy_name="first-live",
                version=1,
                payload_json="{}",
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        await session.flush()
        # StrategyMetadata
        if metadata is not None:
            session.add(
                StrategyMetadata(
                    user_id="test-user",
                    strategy_name="first-live",
                    live_mode_eligible=metadata.get("live_mode_eligible", True),
                    live_promoted_at=metadata.get(
                        "live_promoted_at", datetime.now(UTC).isoformat()
                    ),
                    first_live_trade_confirmed_at=metadata.get(
                        "first_live_trade_confirmed_at"
                    ),
                )
            )
        # Proposal
        session.add(
            ProposalRow(
                proposal_id=decision_id,
                user_id="test-user",
                strategy_id="strat-first-live",
                status=status,
                payload_json=_live_proposal_json(decision_id),
                client_order_id="a" * 32,
                broker_order_id=None,
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
                account_mode=account_mode,
            )
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


@pytest.mark.asyncio
async def test_first_live_approve_diverts_to_awaiting_2nd_channel(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First-live approve transitions to AWAITING_2ND_CHANNEL + does NOT dispatch executor."""
    _patch_settings_env(monkeypatch)
    sf = make_session_factory(temp_sqlcipher_db)
    decision_id = "prop-" + uuid4().hex

    await _seed_user_strategy_proposal(
        sf,
        decision_id=decision_id,
        account_mode="LIVE",
        status="PENDING",
        metadata={"live_mode_eligible": True},
    )

    # Wire seams.
    monkeypatch.setattr(
        slack_handler, "_get_session_factory", lambda _u: (sf, None)
    )
    dispatched: list[str] = []

    async def _fake_execute_proposal(pid: str, uid: str) -> None:
        dispatched.append(pid)

    monkeypatch.setattr(
        slack_handler, "execute_proposal", _fake_execute_proposal
    )
    monkeypatch.setattr(
        executor_mod, "execute_proposal", _fake_execute_proposal
    )

    fake_client = MagicMock()
    fake_client.chat_postMessage = AsyncMock(return_value={"ok": True})

    await _approve_workflow(
        decision_id=decision_id,
        slack_user_id="U_TEST_USER",
        client=fake_client,
    )

    # Verify state machine moved to AWAITING_2ND_CHANNEL.
    async with sf() as session:
        row = await session.get(ProposalRow, decision_id)
    assert row is not None
    assert row.status == "AWAITING_2ND_CHANNEL"
    # Executor must NOT have been dispatched.
    assert dispatched == []
    # The DM must mention the dashboard URL.
    assert fake_client.chat_postMessage.await_count == 1
    call = fake_client.chat_postMessage.await_args
    text = call.kwargs.get("text") or (call.args[0] if call.args else "")
    assert "FIRST live trade" in text, f"text was: {text!r}"
    assert "/live-confirm/" in text
    assert decision_id in text


@pytest.mark.asyncio
async def test_subsequent_live_approve_uses_single_channel(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When first_live_trade_confirmed_at is set, approve goes PENDING → APPROVED + dispatches."""
    _patch_settings_env(monkeypatch)
    sf = make_session_factory(temp_sqlcipher_db)
    decision_id = "prop-" + uuid4().hex

    await _seed_user_strategy_proposal(
        sf,
        decision_id=decision_id,
        account_mode="LIVE",
        status="PENDING",
        metadata={
            "live_mode_eligible": True,
            "first_live_trade_confirmed_at": datetime.now(UTC).isoformat(),
        },
    )

    monkeypatch.setattr(
        slack_handler, "_get_session_factory", lambda _u: (sf, None)
    )
    dispatched: list[str] = []

    async def _fake_execute_proposal(pid: str, uid: str) -> None:
        dispatched.append(pid)

    monkeypatch.setattr(
        slack_handler, "execute_proposal", _fake_execute_proposal
    )
    monkeypatch.setattr(
        executor_mod, "execute_proposal", _fake_execute_proposal
    )

    fake_client = MagicMock()
    fake_client.chat_postMessage = AsyncMock(return_value={"ok": True})

    await _approve_workflow(
        decision_id=decision_id,
        slack_user_id="U_TEST_USER",
        client=fake_client,
    )

    # Wait briefly for the create_task to schedule.
    import asyncio as _asyncio

    await _asyncio.sleep(0)
    await _asyncio.sleep(0)

    async with sf() as session:
        row = await session.get(ProposalRow, decision_id)
    assert row is not None
    assert row.status == "APPROVED"
    # Executor WAS dispatched (single-channel path).
    assert dispatched == [decision_id]


@pytest.mark.asyncio
async def test_paper_approve_unchanged_by_dual_channel_branch(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paper proposals NEVER reach the dual-channel branch (Phase-1 path preserved)."""
    _patch_settings_env(monkeypatch)
    sf = make_session_factory(temp_sqlcipher_db)
    decision_id = "prop-" + uuid4().hex

    # PAPER account_mode — paper-only path, no metadata needed.
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
                strategy_id="strat-paper-first",
                user_id="test-user",
                strategy_name="paper-first",
                version=1,
                payload_json="{}",
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        await session.flush()
        paper_payload = _json.loads(_live_proposal_json(decision_id))
        paper_payload["account_mode"] = "PAPER"
        paper_payload["strategy_name"] = "paper-first"
        session.add(
            ProposalRow(
                proposal_id=decision_id,
                user_id="test-user",
                strategy_id="strat-paper-first",
                status="PENDING",
                payload_json=_json.dumps(paper_payload),
                client_order_id="a" * 32,
                broker_order_id=None,
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
                account_mode="PAPER",
            )
        )

    monkeypatch.setattr(
        slack_handler, "_get_session_factory", lambda _u: (sf, None)
    )
    dispatched: list[str] = []

    async def _fake_execute_proposal(pid: str, uid: str) -> None:
        dispatched.append(pid)

    monkeypatch.setattr(
        slack_handler, "execute_proposal", _fake_execute_proposal
    )
    monkeypatch.setattr(
        executor_mod, "execute_proposal", _fake_execute_proposal
    )

    fake_client = MagicMock()
    fake_client.chat_postMessage = AsyncMock(return_value={"ok": True})

    await _approve_workflow(
        decision_id=decision_id,
        slack_user_id="U_TEST_USER",
        client=fake_client,
    )

    import asyncio as _asyncio

    await _asyncio.sleep(0)
    await _asyncio.sleep(0)

    async with sf() as session:
        row = await session.get(ProposalRow, decision_id)
    assert row is not None
    assert row.status == "APPROVED"  # Phase-1 single-channel path
    assert dispatched == [decision_id]
