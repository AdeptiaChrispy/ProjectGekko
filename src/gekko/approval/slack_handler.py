"""Slack action handlers — Plan 01-08 Task 3 (HITL-04).

Four ``@app.action(...)`` handlers register here (registration happens in
:mod:`gekko.slack.interactivity`; this module owns the logic):

* :func:`handle_approve` — PENDING -> APPROVED + fire Executor.
* :func:`handle_reject` — PENDING -> REJECTED. No Executor.
* :func:`handle_edit_size` — P3 (Plan 03-05): opens the Block Kit modal via views.open.
* :func:`handle_edit_size_view_submission` — P3 view_submission: drift check + transition.
* :func:`handle_escalate_stub` — Deprecated (D-60). URL button replaces action button.

The Pitfall 3 invariant per RESEARCH §"Slack Bolt + FastAPI adapter
wiring": ``ack()`` is the FIRST awaited call in every handler. Slack
times out interactivity payloads after 3 seconds — any DB/broker work
done before ack risks Slack retrying (T-01-08-03 in the threat model).

Cross-user defense (V4 Access Control / T-01-08-01): when
``body['user']['id'] != proposal.user_id`` the handler refuses and
DMs "not the owner". The check happens AFTER ack but BEFORE any state
mutation.

Test seams:
  * :data:`execute_proposal` — module-level reference so tests can
    monkeypatch the executor without touching the import path.
  * :func:`_get_session_factory` — module-level so tests pass a pre-built
    factory (and ``None`` for the engine, which means "don't dispose").

Production wiring (Plan 01-09):
  * The CLI bootstrap calls
    :func:`gekko.agent.runtime.set_passphrase` with the operator-supplied
    passphrase BEFORE any Slack request can fire (D-19).
  * :func:`_get_session_factory` reads that cached passphrase and builds a
    per-user SQLCipher engine each call. The engine is disposed in a
    ``finally`` block so we don't leak SQLCipher connections.

Exactly-once execution (Plan 03-10 — gap closure WR-08):
  The X-Slack-Retry-Num HTTP header is absent in Socket Mode (the
  production transport). The ``_extract_retry_num`` helper and the
  retry-gate blocks that read from ``body["headers"]`` were removed in
  Plan 03-10. Exactly-once execution is provided solely by
  ``claim_action``'s UNIQUE constraint in ``dedup.py``.  Do NOT add
  retry-gate logic that reads HTTP headers here; it will silently not
  work in Socket Mode.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from gekko.approval.dedup import claim_action
from gekko.approval.proposals import (
    approve_proposal,
    reject_proposal,
    transition_status,
)
from gekko.audit.log import append_event
from gekko.config import get_settings
from gekko.db.engine import get_async_engine
from gekko.db.models import Proposal as ProposalRow
from gekko.db.models import SlackActionDedup, StrategyMetadata
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.execution.executor import execute_proposal
from gekko.logging_config import get_logger
from gekko.vault.passphrase import get_passphrase as _get_passphrase

log = get_logger(__name__)

#: Bolt-supplied ack/client callable shapes — duck-typed.
_AckFn = Callable[..., Awaitable[None]]


# ---------------------------------------------------------------------------
# Session factory accessor — test seam
# ---------------------------------------------------------------------------


def _get_session_factory(
    user_id: str,
) -> tuple[AsyncSessionLocal, AsyncEngine | None]:
    """Build a session factory + owning engine for ``user_id``.

    Production: opens a per-user SQLCipher engine via the cached passphrase
    (Plan 01-09's CLI bootstrap populates the cache). Returns the engine
    so the caller can dispose it.

    Tests: :func:`monkeypatch.setattr` replaces this with a lambda that
    returns ``(pre_built_factory, None)``. ``None`` signals "do not
    dispose" — the test owns the engine's lifecycle via the
    ``temp_sqlcipher_db`` fixture.
    """
    settings = get_settings()
    engine = get_async_engine(
        settings.db_path_for(user_id), _get_passphrase()
    )
    return make_session_factory(engine), engine


# ---------------------------------------------------------------------------
# D-43 ephemeral helper
# ---------------------------------------------------------------------------


async def _post_ephemeral(response_url: str, text: str) -> None:
    """POST a Slack ephemeral message to ``response_url``.

    Uses ``httpx.AsyncClient`` (already in tree).  ``response_url`` is the
    single-use URL Slack provides with ~30min TTL (RESEARCH §HITL-02).

    A >=400 response is logged as a WARNING but does NOT raise — the
    duplicate detection already happened via the dedup row; the ephemeral
    is best-effort UX feedback.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                response_url,
                json={"response_type": "ephemeral", "text": text},
                timeout=5.0,
            )
        if resp.status_code >= 400:
            log.warning(
                "slack.ephemeral.post_failed",
                status_code=resp.status_code,
                response_url=response_url,
            )
    except Exception:  # noqa: BLE001 — ephemeral failure is non-critical
        log.warning(
            "slack.ephemeral.post_error",
            response_url=response_url,
        )


def _format_hhmm(iso_ts: str) -> str:
    """Extract HH:MM from an ISO timestamp string."""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso_ts)
        return dt.strftime("%H:%M")
    except Exception:  # noqa: BLE001
        return iso_ts[:16]


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


async def handle_approve(
    *, ack: _AckFn, body: dict[str, Any], client: Any
) -> None:
    """Approve button — HITL-04.

    Flow:
      1. ack FIRST (Pitfall 3).
      2. Dispatch background workflow via :func:`asyncio.create_task` so
         the bolt handler returns quickly.

    The background workflow does the owner check, inserts a dedup row via
    ``claim_action`` (the sole exactly-once guard — Plan 03-10), transitions
    PENDING -> APPROVED with the ``approval`` audit event, and fires the
    Executor.

    Note: The X-Slack-Retry-Num HTTP header is NOT present in Socket Mode
    (the production transport). The former retry-gate block was removed in
    Plan 03-10 (WR-08 gap closure) because it always returned 0 in production
    and was dead code. ``claim_action``'s UNIQUE constraint is the sole and
    sufficient idempotency layer.
    """
    await ack()
    decision_id = body["actions"][0]["value"]
    slack_user_id = body["user"]["id"]

    asyncio.create_task(
        _approve_workflow(
            decision_id=decision_id,
            slack_user_id=slack_user_id,
            body=body,
            client=client,
        )
    )


async def _approve_workflow(
    *,
    decision_id: str,
    slack_user_id: str,
    body: dict[str, Any] | None = None,
    client: Any,
) -> None:
    """Background half of the approve handler.

    ``slack_user_id`` is the Slack id of the clicker (used for the
    cross-user check + DM channel). ``gekko_user_id`` is the INTERNAL
    identity (from settings.gekko_user_id) used for DB / engine /
    executor calls. The two are usually different even for the same
    human — REG-03 + REG-04.

    ``body`` is the original Slack interactivity payload dict; it carries
    ``trigger_id`` (for retry-debugging per D-45) and ``response_url``
    (for the D-43 duplicate ephemeral).
    """
    from gekko.config import get_settings

    _body = body or {}
    settings = get_settings()
    gekko_user_id = settings.gekko_user_id

    # V4 access control — refuse any Slack id other than the configured
    # operator. Per-process per-user-isolated runtime means there's
    # exactly one valid clicker.
    if slack_user_id != settings.slack_user_id:
        log.warning(
            "slack.approval.cross_user_refused",
            decision_id=decision_id,
            slack_user_id=slack_user_id,
            configured_user_id=settings.slack_user_id,
        )
        # WR-06 fix: route through the _send_slack_dm identity-split
        # seam (PATTERNS §10) so the gekko_user_id -> slack_user_id
        # translation happens in exactly one place. The DM lands in
        # the configured operator's channel (settings.slack_user_id) —
        # in the per-user single-operator runtime this is exactly who
        # should be notified that someone else attempted to click
        # their approve button.
        from gekko.execution.executor import _send_slack_dm

        await _send_slack_dm(
            gekko_user_id,
            "You are not the owner of this proposal.",
        )
        return

    sf, engine = _get_session_factory(gekko_user_id)
    try:
        # Snapshot account_mode + strategy_name from the LOCKED proposal row,
        # plus the first_live_trade stamp from StrategyMetadata. These
        # decide whether to take the standard approve path or divert to
        # the HITL-06 dual-channel branch. Reading account_mode from the
        # proposal row (NOT from strategy state) closes the TOCTOU
        # window per BLOCKER #5.
        proposal_account_mode: str | None = None
        strategy_name_snapshot: str | None = None
        # CR-02 fix: snapshot payload_json INSIDE the transaction so the
        # first-live DM block (which fires after the with-block commits)
        # can re-parse the TradeProposal without lazy-loading from a
        # detached/expired SQLAlchemy row.
        payload_json_snapshot: str | None = None
        is_live_first: bool = False
        async with sf() as session, session.begin():
            # -----------------------------------------------------------------
            # D-41 dedup gate — FIRST thing inside the transaction, AFTER the
            # cross-user check. Inserts a SlackActionDedup row; on duplicate
            # fires the D-43 ephemeral and returns WITHOUT touching state.
            # -----------------------------------------------------------------
            dedup_outcome = await claim_action(
                session,
                proposal_id=decision_id,
                action_id="approve_proposal",
                actor_slack_user_id=slack_user_id,
                actor_gekko_user_id=gekko_user_id,
                source="slack",
                slack_trigger_id=_body.get("trigger_id"),
            )
            if dedup_outcome == "duplicate":
                # The claim_action duplicate path rolled back the session.
                # Open a FRESH read-only query to get the original dedup row
                # (for inserted_at + original actor) and the proposal's current
                # status for the D-43 ephemeral copy.
                orig_slack_user: str = slack_user_id
                hh_mm: str = "??"
                current_status: str = "UNKNOWN"
                try:
                    async with sf() as read_session:
                        orig_row = (
                            await read_session.execute(
                                select(SlackActionDedup).where(
                                    SlackActionDedup.proposal_id == decision_id,
                                    SlackActionDedup.action_id == "approve_proposal",
                                    SlackActionDedup.result == "first_write",
                                )
                            )
                        ).scalar_one_or_none()
                        proposal_row = await read_session.get(
                            ProposalRow, decision_id
                        )
                        if orig_row:
                            orig_slack_user = (
                                orig_row.actor_slack_user_id or slack_user_id
                            )
                            hh_mm = _format_hhmm(orig_row.inserted_at)
                        if proposal_row:
                            current_status = proposal_row.status
                except Exception:  # noqa: BLE001
                    log.warning(
                        "slack.approve.dedup_query_failed",
                        decision_id=decision_id,
                    )
                eph_text = (
                    f"✅ Already approved by <@{orig_slack_user}>"
                    f" at {hh_mm}. Status: {current_status}."
                )
                response_url = _body.get("response_url", "")
                if response_url:
                    await _post_ephemeral(response_url, eph_text)
                return

            row = await session.get(ProposalRow, decision_id)
            if row is None:
                # WR-06 fix: route through the identity-split seam.
                from gekko.execution.executor import _send_slack_dm

                await _send_slack_dm(
                    gekko_user_id,
                    f"Proposal `{decision_id}` not found.",
                )
                return
            proposal_account_mode = row.account_mode
            payload_json_snapshot = row.payload_json
            # Pull strategy_name from the persisted TradeProposal payload
            # so we can look up StrategyMetadata.
            from gekko.schemas.proposal import TradeProposal as _TP

            try:
                _tp = _TP.model_validate_json(row.payload_json)
                strategy_name_snapshot = _tp.strategy_name
            except Exception:  # noqa: BLE001 — defensive
                strategy_name_snapshot = None

            # Compute is_live_first ONLY from the proposal row + metadata
            # stamp. Re-reading strategy.mode or live_mode_eligible here
            # would reopen the TOCTOU window — those gates already fired
            # at proposal-build time (T0).
            if proposal_account_mode == "LIVE" and strategy_name_snapshot:
                meta = await session.get(
                    StrategyMetadata,
                    (gekko_user_id, strategy_name_snapshot),
                )
                is_live_first = (
                    meta is None
                    or meta.first_live_trade_confirmed_at is None
                )

            if is_live_first:
                # HITL-06 dual-channel: divert PENDING → AWAITING_2ND_CHANNEL.
                # Do NOT dispatch the executor here; the dashboard
                # /live-confirm route fires it once the second channel
                # confirms.
                await transition_status(
                    session,
                    decision_id,
                    from_status="PENDING",
                    to_status="AWAITING_2ND_CHANNEL",
                )
                await append_event(
                    session,
                    user_id=row.user_id,
                    strategy_id=row.strategy_id,
                    event_type="approval",
                    payload={
                        "proposal_id": decision_id,
                        "actor": slack_user_id,
                        "slack_action_id": "approve_proposal",
                        "awaiting_2nd_channel": True,
                    },
                )
            else:
                # Standard single-channel approve (Phase-1 path).
                await approve_proposal(
                    session, decision_id, actor=slack_user_id
                )

        if is_live_first:
            # CR-02 fix: HITL-06 first-live DM now renders the rich
            # build_first_live_card Block Kit (UI-SPEC §3a) and routes
            # through the _send_slack_dm_blocks identity-split seam in
            # gekko.execution.executor (invariant #8). The seam is the
            # single chokepoint that does the gekko_user_id ->
            # slack_user_id translation per quick-260612-nlv. Sending
            # via client.chat_postMessage(channel=slack_user_id, ...)
            # only worked because the cross-user check above forces
            # slack_user_id == settings.slack_user_id; the seam makes
            # the invariant load-bearing instead of latent.
            #
            # DM is sent AFTER the transaction commits so the row state
            # on disk matches what the dashboard /live-confirm route
            # will see.
            dashboard_url = getattr(
                settings, "dashboard_url", "http://localhost:8000"
            )
            # Re-parse the TP from the in-transaction payload snapshot so
            # we can render the rich card. Defensive: if the payload parse
            # fails (or the snapshot is missing), fall back to a plain-text
            # DM through the seam so the operator still gets the dashboard
            # URL.
            from gekko.schemas.proposal import TradeProposal as _TP

            _tp_card = None
            if payload_json_snapshot is not None:
                try:
                    _tp_card = _TP.model_validate_json(payload_json_snapshot)
                except Exception:  # noqa: BLE001 — defensive
                    _tp_card = None

            if _tp_card is not None:
                from gekko.execution.executor import _send_slack_dm_blocks
                from gekko.reporter.slack import build_first_live_card

                blocks = build_first_live_card(_tp_card, dashboard_url)
                await _send_slack_dm_blocks(
                    gekko_user_id,
                    blocks=blocks,
                    fallback=(
                        f"FIRST LIVE TRADE — confirm at "
                        f"{dashboard_url}/live-confirm/{decision_id}"
                    ),
                )
            else:
                from gekko.execution.executor import _send_slack_dm

                await _send_slack_dm(
                    gekko_user_id,
                    (
                        f":warning: FIRST live trade for "
                        f"`{strategy_name_snapshot}`. Confirm at "
                        f"{dashboard_url}/live-confirm/{decision_id}"
                    ),
                )
            return

        # Outside the approval transaction — the Executor opens its own.
        # Note: pass gekko_user_id (internal id), NOT slack_user_id, so
        # the executor opens the per-user DB at the right path.
        asyncio.create_task(
            execute_proposal(decision_id, gekko_user_id)
        )
        # WR-06 fix: route through the identity-split seam (PATTERNS §10).
        from gekko.execution.executor import _send_slack_dm

        await _send_slack_dm(
            gekko_user_id,
            f"Approved `{decision_id}`. Placing order…",
        )
    except Exception:
        log.exception(
            "slack.approval.workflow_failed",
            decision_id=decision_id,
            gekko_user_id=gekko_user_id,
            slack_user_id=slack_user_id,
        )
    finally:
        if engine is not None:
            await engine.dispose()


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------


async def handle_reject(
    *, ack: _AckFn, body: dict[str, Any], client: Any
) -> None:
    """Reject button — HITL-04. ack-first, then background workflow.

    Note: The X-Slack-Retry-Num HTTP header is NOT present in Socket Mode
    (the production transport). The former retry-gate block was removed in
    Plan 03-10 (WR-08 gap closure). ``claim_action``'s UNIQUE constraint
    is the sole and sufficient idempotency layer.
    """
    await ack()
    decision_id = body["actions"][0]["value"]
    slack_user_id = body["user"]["id"]

    asyncio.create_task(
        _reject_workflow(
            decision_id=decision_id,
            slack_user_id=slack_user_id,
            body=body,
            client=client,
        )
    )


async def _reject_workflow(
    *,
    decision_id: str,
    slack_user_id: str,
    body: dict[str, Any] | None = None,
    client: Any,
) -> None:
    """Background half of the reject handler. Same identity model as approve."""
    from gekko.config import get_settings

    _body = body or {}
    settings = get_settings()
    gekko_user_id = settings.gekko_user_id

    if slack_user_id != settings.slack_user_id:
        log.warning(
            "slack.rejection.cross_user_refused",
            decision_id=decision_id,
            slack_user_id=slack_user_id,
            configured_user_id=settings.slack_user_id,
        )
        # WR-06 fix: route through identity-split seam (PATTERNS §10).
        from gekko.execution.executor import _send_slack_dm

        await _send_slack_dm(
            gekko_user_id,
            "You are not the owner of this proposal.",
        )
        return

    sf, engine = _get_session_factory(gekko_user_id)
    try:
        async with sf() as session, session.begin():
            # D-41 dedup gate — FIRST thing inside the transaction.
            dedup_outcome = await claim_action(
                session,
                proposal_id=decision_id,
                action_id="reject_proposal",
                actor_slack_user_id=slack_user_id,
                actor_gekko_user_id=gekko_user_id,
                source="slack",
                slack_trigger_id=_body.get("trigger_id"),
            )
            if dedup_outcome == "duplicate":
                # Open a fresh read session to get the original dedup row.
                orig_slack_user: str = slack_user_id
                hh_mm: str = "??"
                try:
                    async with sf() as read_session:
                        orig_row = (
                            await read_session.execute(
                                select(SlackActionDedup).where(
                                    SlackActionDedup.proposal_id == decision_id,
                                    SlackActionDedup.action_id == "reject_proposal",
                                    SlackActionDedup.result == "first_write",
                                )
                            )
                        ).scalar_one_or_none()
                        if orig_row:
                            orig_slack_user = (
                                orig_row.actor_slack_user_id or slack_user_id
                            )
                            hh_mm = _format_hhmm(orig_row.inserted_at)
                except Exception:  # noqa: BLE001
                    log.warning(
                        "slack.reject.dedup_query_failed",
                        decision_id=decision_id,
                    )
                eph_text = (
                    f"❌ Already rejected by <@{orig_slack_user}>"
                    f" at {hh_mm}."
                )
                response_url = _body.get("response_url", "")
                if response_url:
                    await _post_ephemeral(response_url, eph_text)
                return

            row = await session.get(ProposalRow, decision_id)
            if row is None:
                # WR-06 fix: route through identity-split seam.
                from gekko.execution.executor import _send_slack_dm

                await _send_slack_dm(
                    gekko_user_id,
                    f"Proposal `{decision_id}` not found.",
                )
                return
            await reject_proposal(
                session, decision_id, actor=slack_user_id
            )
        # WR-06 fix: route through identity-split seam.
        from gekko.execution.executor import _send_slack_dm

        await _send_slack_dm(
            gekko_user_id,
            f"Rejected `{decision_id}`. No order will be placed.",
        )
    except Exception:
        log.exception(
            "slack.rejection.workflow_failed",
            decision_id=decision_id,
            gekko_user_id=gekko_user_id,
            slack_user_id=slack_user_id,
        )
    finally:
        if engine is not None:
            await engine.dispose()


# ---------------------------------------------------------------------------
# P3-deferred stubs
# ---------------------------------------------------------------------------


async def handle_edit_size(
    *, ack: _AckFn, body: dict[str, Any], client: Any
) -> None:
    """Edit-size button — opens Block Kit modal via views.open (D-54, Plan 03-05).

    Pitfall 3: ack() is the FIRST awaited call — before any DB work.
    After ack the handler loads the Proposal row + TradeProposal, builds
    the ref_price, and calls client.views_open with the edit_size_modal.
    """
    await ack()

    decision_id = body["actions"][0]["value"]
    trigger_id = body["trigger_id"]

    settings = get_settings()
    gekko_user_id = settings.gekko_user_id
    sf, engine = _get_session_factory(gekko_user_id)
    try:
        async with sf() as session:
            row = (
                await session.execute(
                    select(ProposalRow).where(
                        ProposalRow.proposal_id == decision_id,
                        ProposalRow.user_id == gekko_user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                log.warning("slack.edit_size.proposal_not_found", decision_id=decision_id)
                return
            # Parse TradeProposal to get qty + pricing
            from gekko.schemas.proposal import TradeProposal
            tp = TradeProposal.model_validate_json(row.payload_json)
    finally:
        if engine is not None:
            await engine.dispose()

    # Determine ref_price: limit_price > stop_price > fallback (use target_notional / qty)
    if tp.limit_price is not None:
        ref_price = tp.limit_price
    elif tp.stop_price is not None:
        ref_price = tp.stop_price
    elif tp.qty and tp.target_notional_usd:
        ref_price = tp.target_notional_usd / tp.qty
    else:
        ref_price = Decimal("0")

    target = tp.target_notional_usd or Decimal("0")
    original_qty = tp.qty

    await client.views_open(
        trigger_id=trigger_id,
        view={
            "type": "modal",
            "callback_id": "edit_size_modal",
            "private_metadata": json.dumps({
                "decision_id": decision_id,
                "ref_price": str(ref_price),
                "target_notional_usd": str(target),
                "original_qty": str(original_qty),
                "ticker": tp.ticker,
                "side": str(tp.side),
                "response_url": body.get("response_url"),
            }),
            "title": {"type": "plain_text", "text": f"Edit size — {tp.ticker}"},
            "submit": {"type": "plain_text", "text": "Approve at this size"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "qty_block",
                    "label": {"type": "plain_text", "text": "New quantity"},
                    "element": {
                        "type": "number_input",
                        "action_id": "qty_input",
                        "initial_value": str(original_qty),
                        "is_decimal_allowed": True,
                        "min_value": "0",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*Ref price:* ${ref_price}\n"
                            f"*Target notional:* ${target}\n"
                            f"*Original qty:* {original_qty} → "
                            f"*Original notional:* ${original_qty * ref_price}"
                        ),
                    },
                },
                {
                    "type": "context",
                    "elements": [{
                        "type": "mrkdwn",
                        "text": "Drift > 2% will be rejected (OrderGuard).",
                    }],
                },
            ],
        },
    )


async def handle_edit_size_view_submission(
    *, ack: _AckFn, body: dict[str, Any], client: Any, view: dict[str, Any]
) -> None:
    """view_submission handler for 'edit_size_modal' — D-54 drift check.

    Validates the operator-submitted qty against the 2% drift threshold.
    On failure: ``response_action='errors'`` re-renders the modal (no state change).
    On pass: ``ack()`` closes modal + spawns ``_edit_size_submit_workflow``.

    Pitfall 3: ack() is the FIRST call — either with errors dict or empty body.
    Pitfall 8: response_action='errors' requires BOTH keys: response_action + errors.
    """
    from gekko.approval.actions import _drift_check

    meta = json.loads(view["private_metadata"])
    decision_id = meta["decision_id"]
    ref_price = Decimal(meta["ref_price"])
    target_notional = Decimal(meta["target_notional_usd"])

    raw_qty = view["state"]["values"]["qty_block"]["qty_input"]["value"]

    try:
        new_qty = Decimal(raw_qty)
    except (InvalidOperation, TypeError):
        await ack({
            "response_action": "errors",
            "errors": {"qty_block": "Please enter a numeric quantity."},
        })
        return

    drift_pct = _drift_check(new_qty, ref_price, target_notional)

    if drift_pct > Decimal("0.02"):
        new_notional = new_qty * ref_price
        await ack({
            "response_action": "errors",
            "errors": {
                "qty_block": (
                    f"Drift {drift_pct:.2%} exceeds the 2% safety bound. "
                    f"Target ${target_notional}; this qty = ${new_notional}. "
                    "Adjust qty or re-run the strategy."
                ),
            },
        })
        return

    # Pass: close the modal; do state-machine work in background.
    await ack()
    asyncio.create_task(
        _edit_size_submit_workflow(
            decision_id=decision_id,
            new_qty=new_qty,
            slack_user_id=body["user"]["id"],
            meta=meta,
        )
    )


async def _edit_size_submit_workflow(
    *,
    decision_id: str,
    new_qty: Decimal,
    slack_user_id: str,
    meta: dict[str, Any],
) -> None:
    """Background: dedup + update proposal qty + PENDING -> APPROVED + executor.

    D-54 step (a): dedup INSERT with action_id='edit_size', source='slack'.
    D-54 step (c): write edit_size audit event, update proposal.qty, transition,
    dispatch executor. The Knight Capital defense is preserved — never calls
    place_order directly (T-03-05-07 / D-27 invariant).
    """
    from gekko.audit.canonical import normalize_decimals
    from gekko.schemas.proposal import TradeProposal

    settings = get_settings()
    gekko_user_id = settings.gekko_user_id
    sf, engine = _get_session_factory(gekko_user_id)
    try:
        async with sf() as session, session.begin():
            outcome = await claim_action(
                session,
                proposal_id=decision_id,
                action_id="edit_size",
                actor_slack_user_id=slack_user_id,
                actor_gekko_user_id=gekko_user_id,
                source="slack",
            )
            if outcome == "duplicate":
                return

            row = (
                await session.execute(
                    select(ProposalRow).where(
                        ProposalRow.proposal_id == decision_id,
                        ProposalRow.user_id == gekko_user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                log.warning(
                    "slack.edit_size_workflow.proposal_not_found",
                    decision_id=decision_id,
                )
                return

            tp = TradeProposal.model_validate_json(row.payload_json)
            ref_price = Decimal(meta["ref_price"])
            old_qty = tp.qty
            old_notional = old_qty * ref_price
            new_notional = new_qty * ref_price

            await append_event(
                session,
                user_id=gekko_user_id,
                strategy_id=row.strategy_id,
                event_type="edit_size",
                payload=normalize_decimals({
                    "old_qty": old_qty,
                    "new_qty": new_qty,
                    "old_notional": old_notional,
                    "new_notional": new_notional,
                    "drift_pct": abs(new_notional - Decimal(meta["target_notional_usd"]))
                                  / Decimal(meta["target_notional_usd"]),
                    "actor": slack_user_id,
                }),
            )

            # Update payload_json with new qty (PATTERNS §3 re-serialize)
            tp_updated = tp.model_copy(update={"qty": new_qty})
            row.payload_json = tp_updated.model_dump_json()

            await transition_status(
                session,
                decision_id,
                from_status="PENDING",
                to_status="APPROVED",
            )

        asyncio.create_task(execute_proposal(decision_id, gekko_user_id))

    except Exception:
        log.exception(
            "slack.edit_size_workflow.failed",
            decision_id=decision_id,
            gekko_user_id=gekko_user_id,
        )
    finally:
        if engine is not None:
            await engine.dispose()


# Backwards-compat alias — interactivity.py may still reference this
handle_edit_size_stub = handle_edit_size


async def handle_escalate_stub(
    *, ack: _AckFn, body: dict[str, Any], client: Any
) -> None:
    """DEPRECATED — D-60 (Plan 03-05 Task 2) converted the Escalate button to a
    URL button that opens /approvals/{proposal_id} in the operator's browser.
    URL buttons do NOT round-trip to Slack action handlers — this function is
    never called in production. It remains here for backward-compat with any
    older Bolt registration that still references handle_escalate_stub by name.
    """
    # no-op: URL buttons do not trigger action handlers (Slack does not post
    # an action event for URL buttons). Log a warning if this is ever reached
    # — it means the button type conversion in build_proposal_card failed.
    await ack()
    log.warning(
        "escalate_stub.should_not_be_called",
        note=(
            "D-60 converted escalate to a URL button in Plan 03-05 Task 2. "
            "If you see this, check build_proposal_card in reporter/slack.py."
        ),
    )


__all__: tuple[str, ...] = (
    "handle_approve",
    "handle_edit_size",
    "handle_edit_size_stub",  # backwards-compat alias for handle_edit_size
    "handle_edit_size_view_submission",
    "handle_escalate_stub",
    "handle_reject",
)
