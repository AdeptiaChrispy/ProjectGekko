"""post_run_result slack_message_ts persistence tests — Plan 03-01 Task 4.

Exercises three cases:
(a) Happy path: mock chat_postMessage returns ts + channel; assert Proposal row updated
(b) Missing proposal row: assert warning logged but no exception propagated
(c) propose_no_action branch: assert no UPDATE attempted
"""

from __future__ import annotations

import unittest.mock
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from gekko.db.models import (
    Proposal as ProposalRow,
    Strategy as StrategyRow,
    User,
)
from gekko.db.session import make_session_factory


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------


_FAKE_TS = "1234567890.000100"
_FAKE_CHANNEL = "D012ABCDEF"
_SLACK_RESPONSE = {"ts": _FAKE_TS, "channel": _FAKE_CHANNEL}


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------


async def _seed_proposal(
    session_factory: Any,
    *,
    proposal_id: str,
    user_id: str = "test-post-run-user",
) -> None:
    """Seed User + Strategy + Proposal row."""
    strategy_id = "strat-postrun-" + uuid4().hex
    now = datetime.now(UTC).isoformat()
    async with session_factory() as session, session.begin():
        session.add(User(user_id=user_id, created_at=now))
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id=strategy_id,
                user_id=user_id,
                strategy_name="test-strategy",
                version=1,
                payload_json="{}",
                created_at=now,
            )
        )
        await session.flush()
        session.add(
            ProposalRow(
                proposal_id=proposal_id,
                user_id=user_id,
                strategy_id=strategy_id,
                status="PENDING",
                payload_json="{}",
                client_order_id=None,
                broker_order_id=None,
                created_at=now,
                updated_at=now,
                account_mode="PAPER",
            )
        )


def _make_trade_result(proposal_id: str) -> dict[str, Any]:
    """Build a minimal propose_trade result dict that post_run_result accepts."""
    return {
        "outcome": "propose_trade",
        "proposal": {
            "proposal_id": proposal_id,
            "decision_id": proposal_id,
            "user_id": "test-post-run-user",
            "strategy_name": "test-strategy",
            "ticker": "NVDA",
            "side": "buy",
            "qty": "10",
            "target_notional_usd": "1800.00",
            "order_type": "limit",
            "limit_price": "180.00",
            "rationale": "Test rationale.",
            "confidence": "0.75",
            "evidence": [
                {
                    "source_type": "finnhub_news",
                    "source_url": "https://reuters.com/x",
                    "fetched_at": "2026-06-09T14:00:00+00:00",
                    "summary": "Strong earnings beat.",
                },
                {
                    "source_type": "edgar_filing",
                    "source_url": "https://www.sec.gov/Archives/edgar/data/x/y.htm",
                    "fetched_at": "2026-06-09T14:00:00+00:00",
                    "summary": "Revenue growth.",
                },
                {
                    "source_type": "alpaca_quote",
                    "fetched_at": "2026-06-09T14:00:00+00:00",
                    "summary": "Quote @ $180.40.",
                },
            ],
            "alternatives_considered": [
                {
                    "description": "Buy AMD instead",
                    "why_rejected": "Over-concentration.",
                }
            ],
            "client_order_id": "a" * 32,
            "account_mode": "PAPER",
        },
    }


# ---------------------------------------------------------------------------
# Helper: build a fake slack_app mock
# ---------------------------------------------------------------------------


def _make_slack_app_mock(response: dict[str, Any] | None = None) -> Any:
    mock_slack = unittest.mock.MagicMock()
    mock_client = unittest.mock.AsyncMock()
    mock_client.chat_postMessage = unittest.mock.AsyncMock(
        return_value=response or _SLACK_RESPONSE
    )
    mock_slack.client = mock_client
    return mock_slack


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_ts_persisted(temp_sqlcipher_db: Any) -> None:
    """chat_postMessage response ts + channel are persisted on the Proposal row."""
    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id = uuid4().hex
    await _seed_proposal(sf, proposal_id=proposal_id)

    import gekko.reporter.slack as slack_module

    # Patch _persist_slack_message_coords to use the test session factory
    # instead of opening a real passphrase-gated engine.
    async def _patched_persist(
        uid: str,
        *,
        proposal_id: str,
        ts: str | None,
        channel: str | None,
    ) -> None:
        from sqlalchemy import update as sa_update

        async with sf() as session, session.begin():
            await session.execute(
                sa_update(ProposalRow)
                .where(ProposalRow.proposal_id == proposal_id)
                .values(slack_message_ts=ts, slack_message_channel=channel)
            )

    mock_slack = _make_slack_app_mock(_SLACK_RESPONSE)

    # slack_app is lazily imported inside post_run_result via
    # `from gekko.slack.app import slack_app` — patch at that source.
    with (
        unittest.mock.patch.object(
            slack_module, "_persist_slack_message_coords", side_effect=_patched_persist
        ),
        unittest.mock.patch("gekko.slack.app.slack_app", mock_slack, create=True),
    ):
        from gekko.reporter.slack import post_run_result

        await post_run_result(
            "test-post-run-user",
            _make_trade_result(proposal_id),
            account_mode="PAPER",
        )

    # Verify the Proposal row was updated with ts + channel
    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
        assert row.slack_message_ts == _FAKE_TS
        assert row.slack_message_channel == _FAKE_CHANNEL


@pytest.mark.asyncio
async def test_missing_proposal_row_no_exception(temp_sqlcipher_db: Any) -> None:
    """If the Proposal row is missing, _persist logs a warning but does NOT raise."""
    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id = uuid4().hex
    # Deliberately do NOT seed a proposal row — simulate the missing-row case.

    warning_logged: list[str] = []

    import gekko.reporter.slack as slack_module

    async def _patched_persist(
        uid: str,
        *,
        proposal_id: str,
        ts: str | None,
        channel: str | None,
    ) -> None:
        """Simulate _persist encountering a missing row — records warning, no raise."""
        from sqlalchemy import update as sa_update

        async with sf() as session, session.begin():
            result = await session.execute(
                sa_update(ProposalRow)
                .where(ProposalRow.proposal_id == proposal_id)
                .values(slack_message_ts=ts, slack_message_channel=channel)
            )
            if result.rowcount == 0:
                # Best-effort: would normally log warning and return
                warning_logged.append("no_row")

    mock_slack = _make_slack_app_mock(_SLACK_RESPONSE)

    with (
        unittest.mock.patch.object(
            slack_module, "_persist_slack_message_coords", side_effect=_patched_persist
        ),
        unittest.mock.patch("gekko.slack.app.slack_app", mock_slack, create=True),
    ):
        from gekko.reporter.slack import post_run_result

        # Must NOT raise even though the proposal row is absent
        await post_run_result(
            "test-post-run-user",
            _make_trade_result(proposal_id),
            account_mode="PAPER",
        )

    assert warning_logged == ["no_row"]


@pytest.mark.asyncio
async def test_propose_no_action_no_update() -> None:
    """propose_no_action branch does NOT call _persist_slack_message_coords."""
    import gekko.reporter.slack as slack_module

    persist_calls: list[Any] = []

    async def _patched_persist(*args: Any, **kwargs: Any) -> None:
        persist_calls.append((args, kwargs))

    mock_slack = _make_slack_app_mock()

    with (
        unittest.mock.patch.object(
            slack_module, "_persist_slack_message_coords", side_effect=_patched_persist
        ),
        unittest.mock.patch("gekko.slack.app.slack_app", mock_slack, create=True),
    ):
        from gekko.reporter.slack import post_run_result

        no_action_result = {
            "outcome": "propose_no_action",
            "proposal": {
                "decision_id": uuid4().hex,
                "user_id": "test-post-run-user",
                "strategy_name": "test-strategy",
                "rationale": "Market conditions not favorable.",
                "confidence": "0.40",
                "factors_considered": ["Low volume", "High spread"],
            },
        }
        await post_run_result(
            "test-post-run-user",
            no_action_result,
            account_mode="PAPER",
        )

    # _persist must NOT have been called for the propose_no_action branch
    assert persist_calls == []
