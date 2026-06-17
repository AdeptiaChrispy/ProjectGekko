"""``write_proposal`` — deterministic LLM-output -> Proposal row + audit events.

Plan 01-07 Task 5. The **deterministic barrier** between the Decision
subagent's structured tool call and the broker. Per RESEARCH
§"Architectural Responsibility Map":

    Trade proposal persistence: API+DB. ``proposals`` row written
    deterministically by Proposal Writer (NOT LLM).

This function does five things, in order:

1. Validate the LLM-supplied ``payload`` dict against the
   :class:`TradeProposal` (or :class:`NoActionProposal`) Pydantic schema
   — runtime fields (``user_id``, ``strategy_name``, ``decision_id``)
   are merged in here, NOT supplied by the LLM (D-11 / D-12).
2. Watchlist guard: if ``tool_outcome == "propose_trade"`` and the
   ticker is NOT in ``strategy.watchlist``, emit an ``error`` audit
   event and raise :exc:`ProposalRejected` — the hallucinated-ticker
   mitigation (RESEARCH §Security Domain).
3. Compute the deterministic ``client_order_id`` via
   :func:`compute_client_order_id` (D-20). This is what blocks
   Knight-Capital duplicate orders downstream — the LLM does NOT pick
   the id.
4. Persist the ``Proposal`` row with ``status="PENDING"`` (idempotent
   on ``proposal_id == decision_id`` — concurrent callers observe the
   first writer's row and return it).
5. Append two audit events through :func:`append_event`:

   * ``decision`` — minimal record per D-15 (run_id, strategy_id,
     prompt_model, research_brief_run_id, decision_outcome).
   * ``proposal`` — the **full** TradeProposal.model_dump (D-15:
     "Full structured rationale embedded in the event payload").

   ``Decimal`` values flow through :func:`normalize_decimals` before
   :func:`append_event` so the canonical-JSON hash is stable across
   trailing-zero variants (Pitfall 6).

The watchlist guard *commits* the error event by raising AFTER the
event is queued — callers that wrap this in a transaction will roll
back the error event too. Tests use ``async with session.begin()`` to
verify the rejection path commits the error event before the raise
propagates; the writer's contract is "raise on rejection; the error
event is best-effort and contingent on the caller's transaction."

For no_action: the Decision agent emitted ``propose_no_action`` so no
broker order will ever fire — we write the two audit events (decision
+ proposal-as-no_action) but skip the proposals row insert. The audit
log carries the rationale; the proposals table is reserved for things
that COULD reach the broker.

References:
  * .planning/.../01-CONTEXT.md  D-11, D-12, D-15, D-20
  * .planning/.../01-RESEARCH.md  §"Anti-Patterns" (deterministic
    persistence); §"Security Domain — Threat Patterns" (hallucinated
    ticker)
  * .planning/.../01-VALIDATION.md  REPT-04 (full rationale per event)
  * src/gekko/core/ids.py        compute_client_order_id (D-20)
  * src/gekko/audit/log.py       append_event (hash chain)
  * src/gekko/audit/canonical.py normalize_decimals (Pitfall 6)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.brokers.base import OrderRequest
from gekko.core.errors import ProposalRejected
from gekko.core.ids import compute_client_order_id
from gekko.db.models import Proposal as ProposalRow
from gekko.db.models import StrategyMetadata
from gekko.execution.checks._wash_sale import flag_wash_sale
from gekko.logging_config import get_logger
from gekko.schemas.proposal import NoActionProposal, TradeProposal
from gekko.schemas.strategy import Strategy

log = get_logger(__name__)

#: Default model name attributed to the persisted ``decision`` event.
#: docs/sdk-shape.md delta #7: prefer the alias.
_DEFAULT_PROMPT_MODEL: str = "sonnet"


async def write_proposal(
    session: AsyncSession,
    *,
    user_id: str,
    strategy: Strategy,
    strategy_db_id: str,
    run_id: str,
    decision_id: str,
    tool_outcome: str,
    payload: dict[str, Any],
    prompt_model: str = _DEFAULT_PROMPT_MODEL,
) -> TradeProposal | NoActionProposal:
    """Persist a Decision-agent tool call as a Proposal row + audit events.

    :param session: An async SQLAlchemy session. The caller is responsible
        for the transaction (``async with session.begin():``).
    :param user_id: The Slack/installation user id (D-21).
    :param strategy: The in-memory Strategy Pydantic instance — the
        watchlist guard reads ``strategy.watchlist``.
    :param strategy_db_id: The ``Strategy.strategy_id`` FK value for the
        Proposal row. Provided separately because the Strategy Pydantic
        model also carries this — but we keep them explicit so callers
        can wire a synthetic strategy in tests.
    :param run_id: Per-cycle run id (UUID; mirrored as
        ``research_brief_run_id`` in the decision event payload).
    :param decision_id: Per-cycle decision id (UUID). Also serves as the
        ``Proposal.proposal_id`` primary key — the 1:1 mapping makes the
        idempotency check trivial.
    :param tool_outcome: Either ``"propose_trade"`` or
        ``"propose_no_action"`` — the Decision agent's selected tool.
    :param payload: The LLM-supplied tool-call kwargs (already extracted
        from ``ToolUseBlock.input`` by the runtime). Runtime-computed
        fields (user_id, strategy_name, decision_id, client_order_id)
        are merged here.
    :param prompt_model: Audit annotation for the ``prompt_model`` field
        of the decision event. Defaults to ``"sonnet"`` per
        docs/sdk-shape.md delta #7.

    :returns: The validated :class:`TradeProposal` (for ``propose_trade``)
        or :class:`NoActionProposal` (for ``propose_no_action``).

    :raises ProposalRejected: When ``tool_outcome == "propose_trade"`` and
        the proposed ticker is not in ``strategy.watchlist``. An
        ``error`` audit event is queued before the raise.
    :raises ValueError: When ``tool_outcome`` is neither known value.
    """
    if tool_outcome == "propose_trade":
        return await _write_trade(
            session,
            user_id=user_id,
            strategy=strategy,
            strategy_db_id=strategy_db_id,
            run_id=run_id,
            decision_id=decision_id,
            payload=payload,
            prompt_model=prompt_model,
        )
    if tool_outcome == "propose_no_action":
        return await _write_no_action(
            session,
            user_id=user_id,
            strategy=strategy,
            strategy_db_id=strategy_db_id,
            run_id=run_id,
            decision_id=decision_id,
            payload=payload,
            prompt_model=prompt_model,
        )
    msg = (
        f"Unknown tool_outcome {tool_outcome!r}; expected 'propose_trade' "
        f"or 'propose_no_action'"
    )
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# propose_trade branch
# ---------------------------------------------------------------------------


async def _write_trade(
    session: AsyncSession,
    *,
    user_id: str,
    strategy: Strategy,
    strategy_db_id: str,
    run_id: str,
    decision_id: str,
    payload: dict[str, Any],
    prompt_model: str,
) -> TradeProposal:
    """Validate, watchlist-guard, persist, and audit a propose_trade payload."""
    # 1. Initial validation — pass through Pydantic to surface schema
    #    errors before we touch the DB. We hand the LLM dict a placeholder
    #    client_order_id (32 hex chars) so the model_validate succeeds; the
    #    real id replaces this once we know the ticker is valid.
    #
    # BLOCKER #5 / Plan 02-01 Task 3 + Plan 02-06 Task 2: stamp
    # ``account_mode`` AT PROPOSAL-BUILD TIME (T0). The stamp is final;
    # downstream callers (Slack approve handler, executor) read
    # ``tp.account_mode`` from the LOCKED proposal row and NEVER re-derive
    # from strategy state at execute-time. This closes the TOCTOU window
    # between proposal-gen (T0) and approve-click (T1).
    #
    # The decision rule per plan 02-06 must check BOTH:
    #   * ``strategy.mode == "live"`` (operator's intent for this strategy)
    #   * ``strategy_metadata.live_mode_eligible == True`` (operator's
    #     explicit promotion via typed-name confirm)
    # Either missing → defensive "PAPER" stamp. The LLM does NOT author
    # this field (``account_mode`` is in ``_runtime_only`` in
    # propose_trade.py).
    strategy_meta = await session.get(
        StrategyMetadata, (user_id, strategy.name)
    )
    is_live_eligible = (
        strategy_meta is not None and strategy_meta.live_mode_eligible
    )
    account_mode = (
        "LIVE"
        if strategy.mode == "live" and is_live_eligible
        else "PAPER"
    )
    merged: dict[str, Any] = {
        **payload,
        "user_id": user_id,
        "strategy_name": strategy.name,
        "decision_id": decision_id,
        "client_order_id": "0" * 32,
        "account_mode": account_mode,
    }
    tp = TradeProposal.model_validate(merged)

    # 2. Watchlist guard (hallucinated-ticker mitigation).
    if tp.ticker.upper() not in strategy.watchlist:
        log.warning(
            "proposal_writer.watchlist_violation",
            user_id=user_id,
            strategy=strategy.name,
            attempted_ticker=tp.ticker,
            watchlist=list(strategy.watchlist),
        )
        await append_event(
            session,
            user_id=user_id,
            strategy_id=strategy_db_id,
            event_type="error",
            payload=normalize_decimals(
                {
                    "context": "proposal_writer.watchlist_violation",
                    "error_class": "ProposalRejected",
                    "error_message": (
                        f"Hallucinated ticker {tp.ticker} not in watchlist "
                        f"{list(strategy.watchlist)}"
                    ),
                    "rejected_proposal": payload,
                }
            ),
        )
        msg = (
            f"Ticker {tp.ticker} not in strategy watchlist "
            f"{list(strategy.watchlist)}"
        )
        raise ProposalRejected(msg)

    # 3. Compute the deterministic client_order_id (D-20).
    client_order_id = compute_client_order_id(
        strategy_id=strategy_db_id,
        decision_id=decision_id,
        side=tp.side,
        qty=tp.qty,
        ticker=tp.ticker,
    )
    # Plan 02-03 / D-28 + EXEC-09: stamp the wash-sale FLAG at proposal-
    # build time (T0). The 30-day lookback over local fill events surfaces
    # a same-ticker prior fill in the HITL Slack card (plan 02-05/02-06
    # renders the warning line); OrderGuard does NOT re-check at place_order
    # time — wash-sale is FLAG-only per D-29.
    #
    # flag_wash_sale NEVER raises (PATTERNS §4 anti-pattern row 12) so this
    # call is safe inside the writer's transactional path. A None result
    # means no flag — the proposal is unmarked.
    wash_sale_flag = await flag_wash_sale(
        req=OrderRequest(
            symbol=tp.ticker,
            side=tp.side,
            qty=tp.qty,
            order_type=tp.order_type,
            limit_price=tp.limit_price,
            stop_price=tp.stop_price,
            client_order_id=client_order_id,
        ),
        user_id=user_id,
    )
    tp = tp.model_copy(
        update={
            "client_order_id": client_order_id,
            "wash_sale_flag": wash_sale_flag,
        }
    )

    # 4. Idempotent persistence — return existing row if it exists.
    existing_row = (
        await session.execute(
            select(ProposalRow).where(ProposalRow.proposal_id == decision_id)
        )
    ).scalar_one_or_none()

    if existing_row is not None:
        # Rebuild the TradeProposal from the persisted JSON to honor
        # idempotency: the second caller returns the SAME proposal the
        # first caller wrote, not a freshly constructed twin.
        return TradeProposal.model_validate_json(existing_row.payload_json)

    now_iso = datetime.now(UTC).isoformat()
    session.add(
        ProposalRow(
            proposal_id=decision_id,
            user_id=user_id,
            strategy_id=strategy_db_id,
            status="PENDING",
            payload_json=tp.model_dump_json(),
            client_order_id=client_order_id,
            broker_order_id=None,
            created_at=now_iso,
            updated_at=now_iso,
            # BLOCKER #5 runtime half (Plan 02-07 walking-skeleton fix):
            # mirror the proposal's account_mode onto the COLUMN. The
            # Slack approve handler reads ``row.account_mode`` (NOT
            # payload_json) to compute is_live_first; without this the
            # column would fall back to the server_default ``'PAPER'``
            # and the dual-channel divert would never fire for live
            # strategies. Closes TOCTOU end-to-end by making the column
            # the authoritative live/paper signal for downstream callers.
            account_mode=account_mode,
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        # Concurrent insert race: another writer beat us to the same
        # decision_id. Roll back our staged INSERT and return the
        # winning row (idempotent persistence per the contract).
        await session.rollback()
        async with session.begin():
            existing = (
                await session.execute(
                    select(ProposalRow).where(
                        ProposalRow.proposal_id == decision_id
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return TradeProposal.model_validate_json(existing.payload_json)
        # Shouldn't happen: IntegrityError without a winning row to find.
        raise

    # 5. Audit: decision + proposal events with D-15 structured rationale.
    decision_payload = normalize_decimals(
        {
            "run_id": run_id,
            "strategy_id": strategy_db_id,
            "prompt_model": prompt_model,
            "research_brief_run_id": run_id,
            "decision_outcome": "trade",
        }
    )
    await append_event(
        session,
        user_id=user_id,
        strategy_id=strategy_db_id,
        event_type="decision",
        payload=decision_payload,
    )

    # mode="python" preserves Decimal instances; normalize_decimals then
    # collapses trailing-zero variants (Pitfall 6 mitigation). canonical_json
    # in append_event renders Decimals via str() — the load-bearing
    # invariant is that Decimal("100.0") and Decimal("100") produce the same
    # canonical string downstream.
    proposal_payload = normalize_decimals(tp.model_dump(mode="python"))
    await append_event(
        session,
        user_id=user_id,
        strategy_id=strategy_db_id,
        event_type="proposal",
        payload=proposal_payload,
    )

    log.info(
        "proposal_writer.trade_persisted",
        user_id=user_id,
        decision_id=decision_id,
        client_order_id=client_order_id,
        ticker=tp.ticker,
        side=str(tp.side),
        qty=str(tp.qty),
    )

    return tp


# ---------------------------------------------------------------------------
# propose_no_action branch
# ---------------------------------------------------------------------------


async def _write_no_action(
    session: AsyncSession,
    *,
    user_id: str,
    strategy: Strategy,
    strategy_db_id: str,
    run_id: str,
    decision_id: str,
    payload: dict[str, Any],
    prompt_model: str,
) -> NoActionProposal:
    """Validate and audit a propose_no_action payload (no Proposal row)."""
    merged: dict[str, Any] = {
        **payload,
        "user_id": user_id,
        "strategy_name": strategy.name,
        "decision_id": decision_id,
    }
    nap = NoActionProposal.model_validate(merged)

    decision_payload = normalize_decimals(
        {
            "run_id": run_id,
            "strategy_id": strategy_db_id,
            "prompt_model": prompt_model,
            "research_brief_run_id": run_id,
            "decision_outcome": "no_action",
        }
    )
    await append_event(
        session,
        user_id=user_id,
        strategy_id=strategy_db_id,
        event_type="decision",
        payload=decision_payload,
    )

    proposal_payload = normalize_decimals(nap.model_dump(mode="python"))
    await append_event(
        session,
        user_id=user_id,
        strategy_id=strategy_db_id,
        event_type="proposal",
        payload=proposal_payload,
    )

    log.info(
        "proposal_writer.no_action_persisted",
        user_id=user_id,
        decision_id=decision_id,
    )

    return nap


__all__: tuple[str, ...] = ("write_proposal",)
