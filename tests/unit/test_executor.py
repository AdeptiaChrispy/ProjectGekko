"""Tests for the deterministic Executor — Plan 01-08 Task 4.

Eight behaviors per the plan:

1. ``execute_proposal`` happy path: APPROVED -> EXECUTING, places order,
   appends ``order_submitted`` audit event.
2. Market closed: APPROVED -> FAILED + ``error`` event with context
   ``executor.market_closed``.
3. Status != APPROVED: raises :class:`ValueError`.
4. :class:`BrokerOrderError`: status -> FAILED + ``error`` event with
   context ``executor.broker_rejected``.
5. Duplicate ``client_order_id``: broker dedup returns existing order;
   Executor treats this as success (the broker's 422 handler is the
   Pitfall 4 safety net — no special-case branch in the Executor).
6. :func:`on_fill_event`: EXECUTING -> FILLED, ``fill`` event written,
   Slack DM sent.
7. All ``Decimal`` values in audit payloads are
   :func:`normalize_decimals`-d before :func:`append_event`.
8. ``order_submitted`` payload conforms to
   :class:`OrderSubmittedEventPayload` (Pydantic v2 validation).

The Executor is deterministic Python (no ``claude_agent_sdk`` import).
Tests inject the broker, session factory, and market-hours guard so we
never hit a real Alpaca endpoint here. The full chain is tested in
``tests/integration/test_slack_approval_to_executor.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from gekko.brokers.base import OrderResult
from gekko.core.errors import BrokerOrderError
from gekko.core.types import OrderSide, OrderType
from gekko.db.models import Event, Proposal as ProposalRow, Strategy as StrategyRow, User
from gekko.db.session import make_session_factory
from gekko.schemas.event import OrderSubmittedEventPayload
from gekko.schemas.proposal import AlternativeConsidered, TradeProposal
from gekko.schemas.research import EvidenceSnippet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trade_proposal(
    *,
    user_id: str = "test-user",
    decision_id: str | None = None,
    client_order_id: str | None = None,
    order_type: str = "limit",
    limit_price: Decimal | None = Decimal("1234.56"),
    account_mode: str = "PAPER",
) -> TradeProposal:
    return TradeProposal(
        user_id=user_id,
        strategy_name="ai-infra-bull",
        decision_id=decision_id or uuid4().hex,
        ticker="NVDA",
        side="buy",
        qty=Decimal("5"),
        # Plan 02-01 Task 3: target_notional_usd (D-27) + account_mode (BLOCKER #5).
        target_notional_usd=Decimal("6172.80"),
        order_type=order_type,
        limit_price=limit_price,
        rationale="Bullish on AI infrastructure.",
        confidence=Decimal("0.78"),
        evidence=[
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/quotes/NVDA",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="NVDA last trade $1234.56.",
            ),
            EvidenceSnippet(
                source_type="finnhub_news",
                source_url="https://finnhub.io/news/nvda",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="Beat by 12%.",
            ),
            EvidenceSnippet(
                source_type="edgar_filing",
                source_url="https://sec.gov/edgar/data/nvda",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="10-Q filed.",
            ),
        ],
        alternatives_considered=[
            AlternativeConsidered(
                description="Wait for next earnings.",
                why_rejected="Catalyst already in market.",
            ),
        ],
        client_order_id=client_order_id or "a" * 32,
        account_mode=account_mode,  # type: ignore[arg-type]
    )


async def _seed(
    session_factory: Any,
    *,
    user_id: str = "test-user",
    status: str = "APPROVED",
    proposal: TradeProposal | None = None,
) -> tuple[str, str, TradeProposal]:
    """Seed User + Strategy + Proposal rows. Returns ``(proposal_id, strategy_id, tp)``."""
    strategy_id = "strat-" + uuid4().hex
    proposal_id = uuid4().hex
    tp = proposal or _make_trade_proposal(
        user_id=user_id, decision_id=proposal_id
    )
    if tp.user_id != user_id or tp.decision_id != proposal_id:
        tp = tp.model_copy(
            update={"user_id": user_id, "decision_id": proposal_id}
        )
    now = datetime.now(UTC).isoformat()
    async with session_factory() as session, session.begin():
        session.add(User(user_id=user_id, created_at=now))
        await session.flush()
        session.add(
            StrategyRow(
                strategy_id=strategy_id,
                user_id=user_id,
                strategy_name=tp.strategy_name,
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
                status=status,
                payload_json=tp.model_dump_json(),
                client_order_id=tp.client_order_id,
                broker_order_id=None,
                created_at=now,
                updated_at=now,
            )
        )
    return proposal_id, strategy_id, tp


def _success_order_result(*, client_order_id: str) -> OrderResult:
    return OrderResult(
        broker_order_id="broker-xyz-001",
        client_order_id=client_order_id,
        status="accepted",
        filled_qty=Decimal("0"),
        avg_fill_price=None,
        raw={"id": "broker-xyz-001", "status": "accepted"},
    )


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_proposal_happy_path(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """APPROVED -> EXECUTING. Broker called with deterministic client_order_id."""
    from gekko.execution import executor

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id, _, tp = await _seed(sf)

    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: True)

    broker = MagicMock()
    broker.place_order = AsyncMock(
        return_value=_success_order_result(client_order_id=tp.client_order_id)
    )
    monkeypatch.setattr(executor, "_build_broker", lambda _u: broker)

    await executor.execute_proposal(proposal_id, "test-user")

    broker.place_order.assert_awaited_once()
    req = broker.place_order.await_args.args[0]
    assert req.symbol == "NVDA"
    assert req.side is OrderSide.BUY
    assert req.qty == Decimal("5")
    assert req.order_type is OrderType.LIMIT
    assert req.limit_price == Decimal("1234.56")
    assert req.client_order_id == tp.client_order_id

    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
        assert row.status == "EXECUTING"

        order_submitted = (
            await session.execute(
                select(Event).where(Event.event_type == "order_submitted")
            )
        ).scalars().all()
        assert len(order_submitted) == 1


# ---------------------------------------------------------------------------
# 2. Market closed -> FAILED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_proposal_market_closed(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Market closed -> APPROVED -> FAILED, error event with context."""
    from gekko.execution import executor

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id, _, _ = await _seed(sf)

    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: False)

    broker = MagicMock()
    broker.place_order = AsyncMock()
    monkeypatch.setattr(executor, "_build_broker", lambda _u: broker)

    await executor.execute_proposal(proposal_id, "test-user")

    broker.place_order.assert_not_awaited()

    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
        assert row.status == "FAILED"

        err = (
            await session.execute(
                select(Event).where(Event.event_type == "error")
            )
        ).scalars().all()
        assert len(err) == 1
        assert "executor.market_closed" in err[0].payload_json


# ---------------------------------------------------------------------------
# 3. Status != APPROVED -> ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_proposal_rejects_non_approved_status(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PENDING proposal cannot be executed — raises ValueError."""
    from gekko.execution import executor

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id, _, _ = await _seed(sf, status="PENDING")

    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: True)
    monkeypatch.setattr(executor, "_build_broker", lambda _u: MagicMock())

    with pytest.raises(ValueError, match="APPROVED|status"):
        await executor.execute_proposal(proposal_id, "test-user")


# ---------------------------------------------------------------------------
# 4. BrokerOrderError -> FAILED + error event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_proposal_broker_error_transitions_to_failed(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BrokerOrderError -> error event + APPROVED -> FAILED."""
    from gekko.execution import executor

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id, _, _ = await _seed(sf)

    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: True)

    broker = MagicMock()
    broker.place_order = AsyncMock(
        side_effect=BrokerOrderError("simulated reject: insufficient buying power")
    )
    monkeypatch.setattr(executor, "_build_broker", lambda _u: broker)

    sent_dms: list[str] = []

    async def fake_send_dm(_user_id: str, msg: str) -> None:
        sent_dms.append(msg)

    monkeypatch.setattr(executor, "_send_slack_dm", fake_send_dm)

    await executor.execute_proposal(proposal_id, "test-user")

    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
        assert row.status == "FAILED"

        err = (
            await session.execute(
                select(Event).where(Event.event_type == "error")
            )
        ).scalars().all()
        assert len(err) == 1
        assert "executor.broker_rejected" in err[0].payload_json
        assert "insufficient buying power" in err[0].payload_json

    # User got a DM explaining the failure (defensive UX check).
    assert any("failed" in m.lower() for m in sent_dms)


# ---------------------------------------------------------------------------
# 5. Duplicate client_order_id -> treated as success (Pitfall 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_proposal_duplicate_client_order_id_is_success(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AlpacaBroker.place_order's 422 dedup returns an existing OrderResult.

    From the Executor's perspective this is indistinguishable from a fresh
    success — the duplicate-resubmit safety net is at the broker layer
    (Plan 01-05). The Executor records ``order_submitted`` and proceeds.
    """
    from gekko.execution import executor

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id, _, tp = await _seed(sf)

    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: True)

    # Broker dedup path returns the existing OrderResult (already filled).
    existing = OrderResult(
        broker_order_id="broker-original-001",
        client_order_id=tp.client_order_id,
        status="filled",
        filled_qty=Decimal("5"),
        avg_fill_price=Decimal("1230.00"),
        raw={"id": "broker-original-001", "status": "filled"},
    )
    broker = MagicMock()
    broker.place_order = AsyncMock(return_value=existing)
    monkeypatch.setattr(executor, "_build_broker", lambda _u: broker)

    await executor.execute_proposal(proposal_id, "test-user")

    async with sf() as session:
        order_submitted = (
            await session.execute(
                select(Event).where(Event.event_type == "order_submitted")
            )
        ).scalars().all()
        assert len(order_submitted) == 1
        assert "broker-original-001" in order_submitted[0].payload_json


# ---------------------------------------------------------------------------
# 6. on_fill_event: EXECUTING -> FILLED, fill event, Slack DM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_fill_event_transitions_to_filled_and_dms(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fill payload from AlpacaFillStream lands -> FILLED + DM."""
    from gekko.execution import executor

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id, _, tp = await _seed(sf, status="APPROVED")

    # Manually walk to EXECUTING so the fill transition is valid.
    async with sf() as session, session.begin():
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
        row.status = "EXECUTING"

    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf, None)
    )

    sent_dms: list[dict[str, Any]] = []

    async def fake_send_dm(user_id: str, msg: str) -> None:
        sent_dms.append({"user_id": user_id, "msg": msg})

    monkeypatch.setattr(executor, "_send_slack_dm", fake_send_dm)

    payload = {
        "client_order_id": tp.client_order_id,
        "broker_order_id": "broker-fill-001",
        "filled_qty": "5",
        "filled_avg_price": "1234.50",
        "ticker": "NVDA",
        "user_id": "test-user",
        "event": "fill",
    }
    await executor.on_fill_event(payload, user_id="test-user")

    async with sf() as session:
        row = (
            await session.execute(
                select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
            )
        ).scalar_one()
        assert row.status == "FILLED"

        fill = (
            await session.execute(
                select(Event).where(Event.event_type == "fill")
            )
        ).scalars().all()
        assert len(fill) == 1
        assert "broker-fill-001" in fill[0].payload_json

    assert len(sent_dms) == 1
    assert "NVDA" in sent_dms[0]["msg"]


# ---------------------------------------------------------------------------
# 7. Decimal normalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_proposal_normalizes_decimals_in_audit_payload(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All Decimal values in ``order_submitted`` payload go through normalize_decimals.

    Pitfall 6 — trailing-zero Decimals must canonicalize so the audit
    chain is stable across encoder variants.
    """
    from gekko.execution import executor

    sf = make_session_factory(temp_sqlcipher_db)
    # Use a quantity with trailing zeros (Decimal('5.00') vs Decimal('5'))
    tp = _make_trade_proposal()
    tp = tp.model_copy(update={"qty": Decimal("5.00")})
    proposal_id, _, _ = await _seed(sf, proposal=tp)

    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: True)

    broker = MagicMock()
    broker.place_order = AsyncMock(
        return_value=_success_order_result(client_order_id=tp.client_order_id)
    )
    monkeypatch.setattr(executor, "_build_broker", lambda _u: broker)

    await executor.execute_proposal(proposal_id, "test-user")

    async with sf() as session:
        order_submitted = (
            await session.execute(
                select(Event).where(Event.event_type == "order_submitted")
            )
        ).scalars().all()
        assert len(order_submitted) == 1
        # normalize_decimals collapses '5.00' to '5'; the payload_json
        # canonical-subset must contain '5' (not '5.00' or '5E+0').
        text = order_submitted[0].payload_json
        # qty appears as a string per OrderSubmittedEventPayload contract.
        # Canonical JSON uses separators=(',', ':') — no whitespace after :.
        assert '"qty":"5"' in text
        # Trailing-zero collapse means '5.00' must NOT survive.
        assert '"qty":"5.00"' not in text
        assert '"qty":"5E+0"' not in text


# ---------------------------------------------------------------------------
# 8. order_submitted payload conforms to OrderSubmittedEventPayload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_order_submitted_payload_conforms_to_schema(
    temp_sqlcipher_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validate the recorded payload against OrderSubmittedEventPayload."""
    import json

    from gekko.execution import executor

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id, _, tp = await _seed(sf)

    monkeypatch.setattr(
        executor, "_get_session_factory", lambda _u: (sf, None)
    )
    monkeypatch.setattr(executor, "is_market_open", lambda *a, **k: True)

    broker = MagicMock()
    broker.place_order = AsyncMock(
        return_value=_success_order_result(client_order_id=tp.client_order_id)
    )
    monkeypatch.setattr(executor, "_build_broker", lambda _u: broker)

    await executor.execute_proposal(proposal_id, "test-user")

    async with sf() as session:
        order_submitted = (
            await session.execute(
                select(Event).where(Event.event_type == "order_submitted")
            )
        ).scalars().all()
        assert len(order_submitted) == 1
        canonical = json.loads(order_submitted[0].payload_json)
        # canonical subset is {event_type, payload, ts, user_id} — the
        # OrderSubmittedEventPayload-shaped dict lives in ['payload'].
        inner = canonical["payload"]
        # event_kind discriminator required for the union validator.
        inner.setdefault("event_kind", "order_submitted")
        # Validates -> raises ValidationError if shape is wrong.
        OrderSubmittedEventPayload.model_validate(inner)


# ---------------------------------------------------------------------------
# 9. Architectural firewall: NO claude_agent_sdk imports
# ---------------------------------------------------------------------------


def test_executor_module_does_not_import_claude_agent_sdk() -> None:
    """Plan 01-08 success criterion 7 — Anti-Pattern 1 firewall.

    Reading the executor source bytes is the cheapest, most direct way
    to assert the architectural invariant. A future refactor that
    transitively pulls in the SDK would fail this gate.
    """
    import gekko.execution.executor as mod

    src = open(mod.__file__, encoding="utf-8").read()
    assert "claude_agent_sdk" not in src
    assert "from claude_agent_sdk" not in src


# ---------------------------------------------------------------------------
# 10. _send_slack_dm translates gekko_user_id -> settings.slack_user_id
#     (quick-task 260612-nlv — 6th 01-09 demo-discovery fix; same bug class as
#     commit 297a882 which fixed slack_user_id vs gekko_user_id split in 4
#     other call sites but missed _send_slack_dm)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_slack_dm_translates_gekko_user_id_to_slack_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LOAD-BEARING: _send_slack_dm must route by settings.slack_user_id.

    The function takes a ``user_id`` argument (the internal gekko_user_id
    like "chris") for caller-API stability + audit/log metadata, but it
    MUST translate to ``settings.slack_user_id`` (e.g. "U08LRFFRBS4")
    before calling Slack's chat.postMessage. Passing "chris" as
    ``channel=`` produces ``SlackApiError(channel_not_found)`` — this was
    the 2026-06-12 manual-demo finding.
    """
    import sys
    import types

    from gekko.execution import executor

    # Patch settings.slack_user_id (executor.py already imports
    # get_settings from gekko.config — reuse that symbol via monkeypatch).
    fake_settings = MagicMock()
    fake_settings.slack_user_id = "U08LRFFRBS4"
    monkeypatch.setattr(executor, "get_settings", lambda: fake_settings)

    # Stand in for `from gekko.slack.app import slack_app` (lazy import
    # inside _send_slack_dm so we don't need the real Slack env).
    chat_postMessage = AsyncMock(return_value=None)
    fake_slack_app = MagicMock()
    fake_slack_app.client.chat_postMessage = chat_postMessage
    fake_module = types.ModuleType("gekko.slack.app")
    fake_module.slack_app = fake_slack_app  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gekko.slack.app", fake_module)

    await executor._send_slack_dm(
        user_id="chris", text="Paper order filled: BUY 1 NVDA @ $204.97"
    )

    # LOAD-BEARING ASSERTION: routes by settings.slack_user_id, not the
    # gekko_user_id argument.
    assert (
        chat_postMessage.await_args.kwargs["channel"] == "U08LRFFRBS4"
    )
    # Explicit defence against the regression class — gekko_user_id MUST
    # NOT leak into the Slack channel kwarg.
    assert chat_postMessage.await_args.kwargs["channel"] != "chris"
    # Body must round-trip unchanged.
    assert (
        chat_postMessage.await_args.kwargs["text"]
        == "Paper order filled: BUY 1 NVDA @ $204.97"
    )


@pytest.mark.asyncio
async def test_send_slack_dm_preserves_text_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Complement: the fix must not escape/truncate the message body.

    Mrkdwn-ish characters round-trip verbatim through the ``text=`` kwarg.
    """
    import sys
    import types

    from gekko.execution import executor

    fake_settings = MagicMock()
    fake_settings.slack_user_id = "U08LRFFRBS4"
    monkeypatch.setattr(executor, "get_settings", lambda: fake_settings)

    chat_postMessage = AsyncMock(return_value=None)
    fake_slack_app = MagicMock()
    fake_slack_app.client.chat_postMessage = chat_postMessage
    fake_module = types.ModuleType("gekko.slack.app")
    fake_module.slack_app = fake_slack_app  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gekko.slack.app", fake_module)

    body = "*bold* _italic_ <https://example.com|link>"
    await executor._send_slack_dm(user_id="chris", text=body)

    assert chat_postMessage.await_args.kwargs["text"] == body
