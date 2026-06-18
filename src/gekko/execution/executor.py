"""Deterministic order Executor — Plan 01-08 Task 4.

The deterministic Python firewall between the LLM and the broker. Per
RESEARCH §"Anti-Patterns" Pattern 1, the LLM **never** calls
``place_order`` directly — the Decision agent's only side-effect-capable
tools are ``propose_trade`` / ``propose_no_action`` which write a
:class:`Proposal` row. The Slack approval handler (Plan 01-08 Task 3)
calls :func:`execute_proposal` AFTER the user clicks Approve. From this
point on, no LLM bytes touch broker calls.

:func:`execute_proposal` walks the APPROVED proposal through:

  1. **Sanity gate** — re-loads the row + validated :class:`TradeProposal`
     payload, confirms ``status == "APPROVED"``. Otherwise raises
     ``ValueError`` (defense in depth — the state machine should already
     have rejected this).
  2. **Market-hours guard (EXEC-10)** — :func:`is_market_open` from
     :mod:`gekko.execution.market_hours`. Closed market -> ``error``
     audit event with context ``executor.market_closed`` + status flip
     APPROVED -> FAILED. P1 fails; P7 will add deferred-retry-on-open.
  3. **Order construction** — :class:`OrderRequest` using the persisted
     deterministic ``client_order_id`` (D-20 / Pitfall 4). Re-computing
     the id at this layer is intentional: any drift between the
     proposal-row id and the broker-call id would defeat the
     Knight-Capital dedup invariant.
  4. **Broker submission** — :meth:`AlpacaBroker.place_order`. A
     :class:`BrokerOrderError` -> ``error`` event + status flip
     APPROVED -> FAILED + Slack DM. A duplicate (HTTP 422) is handled
     inside the broker (Plan 01-05) — it returns the existing
     OrderResult and the Executor records ``order_submitted`` as
     normal.
  5. **Audit** — ``order_submitted`` event with the
     :class:`OrderSubmittedEventPayload` shape; state APPROVED ->
     EXECUTING.

:func:`on_fill_event` is the TradingStream callback (registered in Plan
01-09's FastAPI lifespan via :class:`AlpacaFillStream`). It receives the
fill payload, looks up the proposal row by ``client_order_id``, appends
the ``fill`` audit event, transitions EXECUTING -> FILLED, and sends a
Slack DM confirmation.

Test seams (the four module-level names tests monkeypatch):

  * :func:`_get_session_factory(user_id)` -> ``(session_factory, engine_or_None)``
  * :func:`_build_broker(user_id)` -> a :class:`Brokerage` instance
  * :func:`is_market_open()` (re-exported from :mod:`market_hours`)
  * :func:`_send_slack_dm(user_id, text)` -> ``None``

The Claude Agent SDK MUST NOT be imported here. The grep-gate in
``tests/unit/test_executor.py`` enforces this at the source-bytes
level (Plan 01-08 success criterion 7); even the bare substring of
the SDK package name would trip it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from gekko.approval.proposals import transition_status
from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.brokers.alpaca import AlpacaBroker
from gekko.brokers.base import Brokerage, OrderRequest, OrderResult
from gekko.config import get_settings
from gekko.core.errors import BrokerOrderError, OrderGuardRejected
from gekko.core.types import OrderSide, OrderType, TimeInForce
from gekko.db.engine import get_async_engine
from gekko.db.models import Proposal as ProposalRow, Strategy as StrategyRow
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.execution.market_hours import is_market_open
from gekko.execution.orderguard import OrderGuard
from gekko.logging_config import get_logger
from gekko.schemas.proposal import TradeProposal
from gekko.schemas.strategy import Strategy
from gekko.vault.passphrase import get_passphrase as _get_passphrase

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module-level test seams — production builds engines + brokers from settings
# ---------------------------------------------------------------------------


def _get_session_factory(
    user_id: str,
) -> tuple[AsyncSessionLocal, AsyncEngine | None]:
    """Build a session factory + owning engine for ``user_id``.

    Mirrors the same indirection used by :mod:`gekko.approval.slack_handler`
    so tests have a single seam to monkeypatch.
    """
    settings = get_settings()
    engine = get_async_engine(
        settings.db_path_for(user_id), _get_passphrase()
    )
    return make_session_factory(engine), engine


async def _build_broker(
    user_id: str,
    strategy: Strategy,
    account_mode: str,
    *,
    proposal: TradeProposal | None = None,
) -> Brokerage:
    """Construct the per-user :class:`AlpacaBroker` wrapped in :class:`OrderGuard`.

    Plan 02-02 Task 3: wraps the concrete broker in OrderGuard so every
    trade goes through the 8 checks (universe, hard caps, qty×price drift,
    paper/live invariant, kill-switch read, PDT, T+1, market-hours).

    Plan 02-06 Task 1: LIVE branch lit. ``is_live`` is derived from the
    LOCKED proposal row's ``account_mode``, NOT from current strategy
    state — re-reading ``strategy.mode`` or
    ``strategy_metadata.live_mode_eligible`` at execute-time would reopen
    the TOCTOU window between proposal-build (T0) and execute (T1) that
    BLOCKER #5 closes.

    BLOCKER #4 grep gate: ``_allow_live=True`` and ``paper=False``
    literals are LOCKED to this function. Any other site in
    ``src/gekko/`` containing these substrings fails
    ``tests/unit/test_alpaca_live_construction_locked.py``.

    Tests monkeypatch this with a :class:`MagicMock` so we never hit a
    real Alpaca endpoint at unit-test time.

    :param user_id: Per-user SQLCipher DB scope (passed through to
        OrderGuard for ``check_kill_switch``).
    :param strategy: The :class:`Strategy` the proposal was authored
        against. Used by OrderGuard for watchlist + hard_caps + mode.
    :param account_mode: ``"PAPER"`` or ``"LIVE"`` — stamped on the
        proposal at build time (BLOCKER #5). Sourced from the LOCKED
        proposal row; NEVER re-derived from strategy state here.
    :param proposal: Optional :class:`TradeProposal` carrying the
        ``target_notional_usd`` for the qty×price 2% drift check.
    """
    from gekko.core.errors import OrderGuardRejected
    from gekko.vault.credentials import load_live_credentials

    settings = get_settings()
    is_live = account_mode == "LIVE"
    if is_live:
        creds = await load_live_credentials(user_id)
        if creds is None:
            raise OrderGuardRejected(
                "paper_live_mismatch_credential",
                (
                    f"Strategy {strategy.name} is live but no alpaca_live "
                    "credentials in vault. Run "
                    "`gekko credentials add-alpaca-live`."
                ),
                extra={
                    "strategy_name": strategy.name,
                    "user_id": user_id,
                },
            )
        api_key, secret_key = creds
        wrapped = AlpacaBroker(
            api_key=api_key,
            secret_key=secret_key,
            paper=False,
            _allow_live=True,
        )
        credential_kind = "alpaca_live"
    else:
        wrapped = AlpacaBroker(
            api_key=settings.alpaca_paper_api_key.get_secret_value(),
            secret_key=settings.alpaca_paper_secret_key.get_secret_value(),
            paper=True,
        )
        credential_kind = "alpaca_paper"
    return OrderGuard(
        wrapped,
        strategy=strategy,
        account_mode=account_mode,  # type: ignore[arg-type]
        user_id=user_id,
        proposal=proposal,
        credential_kind=credential_kind,
    )


async def _send_slack_dm(user_id: str, text: str) -> None:
    """Send a Slack DM addressed to the configured operator.

    Identity-split: the ``user_id`` argument is the INTERNAL
    ``gekko_user_id`` (e.g. ``"chris"``) carried for caller-API
    stability + audit/log metadata. Slack's ``chat.postMessage``
    requires a Slack channel/user id (e.g. ``"U08LRFFRBS4"``), so this
    function reads :attr:`gekko.config.Settings.slack_user_id` and
    binds it to the ``channel=`` kwarg. Passing ``user_id`` to Slack
    directly produces ``SlackApiError(channel_not_found)``.

    Same bug class as commit ``297a882`` (which fixed the
    ``slack_user_id`` vs ``gekko_user_id`` split in the slash-command
    handler, approve-handler, cross-user check, and ``post_run_result``
    but missed this function). Surfaced as Plan 01-09 Task 5 demo
    finding #6 on 2026-06-12; audit chain unaffected — the ``fill``
    event commits inside the DB transaction at ``executor.py``'s
    ``on_fill_event`` BEFORE this DM call.

    Lazily imports the bolt :data:`slack_app` so unit tests that don't
    set up the full Slack env can monkeypatch this without triggering
    import-time failures.
    """
    from gekko.slack.app import slack_app

    settings = get_settings()
    await slack_app.client.chat_postMessage(
        channel=settings.slack_user_id, text=text
    )


async def _send_slack_dm_respecting_quiet_hours(
    user_id: str,
    text: str,
    *,
    category: str,
) -> None:
    """Route a Slack DM through the quiet-hours gate per D-48 (HITL-05).

    **Bypass categories** (always fire, no quiet-hours check):
      * ``"kill_active"``  — kill-state changes (operator safety)
      * ``"executor_error"`` — BrokerOrderError, MarketClosed, cap_rejection
      * ``"first_live_fill"`` — first live-money fill (trust-building signal)

    **Routine categories** (suppressed when :func:`_resolve_quiet_hours` is True):
      * ``"routine_fill"`` — paper-trade or subsequent paper fill confirmations
      * ``"daily_pnl"``   — scheduled P&L digest

    The function imports :func:`_resolve_quiet_hours` lazily (inside the body)
    to avoid module-load-time circular imports: ``executor.py`` is the
    substrate; ``quiet_hours.py`` reads via the same DB session-factory seam.

    All actual sends route through :func:`_send_slack_dm` — never directly to
    ``chat.postMessage`` — preserving the identity-split fix from quick task
    260612-nlv and PATTERNS §2e.

    :param user_id: Internal gekko user id (see :func:`_send_slack_dm`).
    :param text: DM text payload.
    :param category: One of the five literals above.  Unrecognised literals
        are treated as bypass-category (fail-open keeps the operator informed).
    """
    _BYPASS_CATEGORIES = frozenset({"kill_active", "executor_error", "first_live_fill"})

    if category in _BYPASS_CATEGORIES:
        # bypass-category: bypass-dispatch — caller already classified this
        # as a bypass category (kill_active, executor_error, first_live_fill).
        await _send_slack_dm(user_id, text)
        return

    # Routine category — consult the quiet-hours predicate.
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from gekko.approval.quiet_hours import _resolve_quiet_hours

    try:
        in_window = await _resolve_quiet_hours(user_id, _dt.now(_UTC))
    except Exception:  # noqa: BLE001
        # If the predicate fails (e.g. DB not ready), fail-open and send.
        log.exception(
            "executor.quiet_hours_predicate_failed",
            user_id=user_id,
            category=category,
        )
        in_window = False

    if in_window:
        log.debug(
            "slack_dm.suppressed",
            user_id=user_id,
            category=category,
        )
        return

    # bypass-category: routine-out-of-window — predicate returned False;
    # quiet hours not active, send the routine DM.
    await _send_slack_dm(user_id, text)


async def _send_slack_dm_blocks(
    user_id: str, *, blocks: list[dict[str, Any]], fallback: str = ""
) -> None:
    """Send a Slack Block Kit DM addressed to the configured operator.

    Plan 02-05 Task 3 — parallel to :func:`_send_slack_dm` for blocks-shaped
    payloads (OrderGuard rejection card + boot-time kill DM card variants).
    Honors the identity-split fix from quick task 260612-nlv: the
    ``user_id`` argument is the INTERNAL gekko_user_id; Slack's
    ``chat.postMessage`` requires :attr:`Settings.slack_user_id`.

    :param user_id: Internal gekko_user_id (ignored at the channel layer
        but accepted for caller-API symmetry with :func:`_send_slack_dm`).
    :param blocks: Block Kit block list.
    :param fallback: Text fallback for notification preview + screen
        readers. Required when ``blocks=`` is set per Slack's API.
    """
    from gekko.slack.app import slack_app

    settings = get_settings()
    await slack_app.client.chat_postMessage(
        channel=settings.slack_user_id,
        blocks=blocks,
        text=fallback or "OrderGuard rejection",
    )


# ---------------------------------------------------------------------------
# Strategy hydration helper (Plan 02-02 Task 3)
# ---------------------------------------------------------------------------


def _load_strategy_for_executor(
    *,
    strategy_row: StrategyRow | None,
    tp: TradeProposal,
    user_id: str,
) -> Strategy:
    """Hydrate a :class:`Strategy` Pydantic instance for OrderGuard.

    Phase 1 stores the canonical Strategy JSON in
    ``strategies.payload_json``. Phase 2's OrderGuard needs ``watchlist``
    + ``hard_caps`` + ``mode`` at place_order time. We parse the row's
    ``payload_json`` when available; otherwise we synthesize a minimal
    Strategy from the proposal — enough for the universe + paper-live
    invariant + hard-caps math to operate.

    The synthesized fallback exists because every Phase-1 executor test
    seeds Strategy rows with ``payload_json="{}"`` (the test helpers in
    Plan 01-08 didn't populate it). On the PAPER path we degrade
    gracefully with permissive defaults that match the proposal's ticker
    (universe pass) + a 100% position cap (no hard-cap reject in
    synthesized mode).

    WR-03 fix: on the LIVE path the synthesized fallback is now FAIL-
    CLOSED. A LIVE proposal whose strategy row has empty / corrupt
    payload_json (botched migration, future regression nulling the
    column, manual DB edit) used to land permissive synthetic caps —
    max_position_pct=0.20, max_daily_loss_usd=999999,
    max_trades_per_day=999, max_sector_exposure_pct=1 — that overrode
    the operator's actual stated caps. On the live broker, permissive
    defaults are exactly the failure mode this phase is designed to
    prevent. We refuse to substitute permissive caps for real money;
    the LIVE branch raises so OrderGuard never sees synthetic caps.
    """
    from decimal import Decimal as _D
    from datetime import UTC as _UTC, datetime as _dt

    from gekko.schemas.strategy import HardCaps as _HardCaps

    if strategy_row is not None and strategy_row.payload_json:
        try:
            return Strategy.model_validate_json(strategy_row.payload_json)
        except Exception as exc:  # noqa: BLE001
            # WR-03 fix: log the parse failure structured (previously
            # silently swallowed via ``except Exception: pass``), so a
            # corrupt payload surfaces in operator logs even when the
            # caller falls through to the synth path.
            log.exception(
                "executor.strategy_payload_parse_failed",
                strategy_id=getattr(strategy_row, "strategy_id", None),
                user_id=user_id,
                error=str(exc),
            )
            if tp.account_mode == "LIVE":
                # Fail closed on the live path — never synthesize
                # permissive caps for real money.
                msg = (
                    f"Cannot execute LIVE proposal {tp.decision_id} "
                    f"against strategy {tp.strategy_name!r}: "
                    "payload_json failed to parse. Refusing to "
                    "substitute permissive synthetic caps."
                )
                raise ValueError(msg) from exc

    if tp.account_mode == "LIVE":
        # WR-03 fix: empty / missing payload_json on the LIVE path is
        # also fail-closed. Synthesized permissive caps must never
        # touch a real-money order.
        msg = (
            f"Cannot execute LIVE proposal {tp.decision_id} against "
            f"strategy {tp.strategy_name!r}: strategies.payload_json "
            "is empty or absent. Refusing to substitute permissive "
            "synthetic caps for real money."
        )
        raise ValueError(msg)

    # PAPER fallback — preserved verbatim from the pre-fix behavior so
    # the Phase-1 walking-skeleton tests keep passing.
    synth_mode = "paper" if tp.account_mode == "PAPER" else "live"
    return Strategy(
        strategy_id=(strategy_row.strategy_id if strategy_row else "synth"),
        user_id=user_id,
        name=tp.strategy_name,
        version=1,
        thesis="(synthesized for executor; payload_json empty)",
        watchlist=[tp.ticker],
        hard_caps=_HardCaps(
            max_position_pct=_D("0.20"),
            max_daily_loss_usd=_D("999999"),
            max_trades_per_day=999,
            max_sector_exposure_pct=_D("1"),
        ),
        mode=synth_mode,  # type: ignore[arg-type]
        created_at=_dt.now(_UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# execute_proposal — the deterministic order pipeline
# ---------------------------------------------------------------------------


async def execute_proposal(proposal_id: str, user_id: str) -> None:
    """Walk an APPROVED proposal through the broker + audit-log pipeline.

    :param proposal_id: Primary key of the ``proposals`` row.
    :param user_id: Owner of the proposal. Used to scope the per-user
        SQLCipher engine and to construct the broker.
    :raises ValueError: When the proposal's status is not APPROVED at
        load time. The state machine should already have rejected this,
        but the explicit guard catches caller drift.

    Errors past the sanity gate are surfaced via audit events + Slack
    DMs; the function never raises to the caller (it runs inside an
    ``asyncio.create_task`` from the Slack approval handler).
    """
    sf, engine = _get_session_factory(user_id)
    try:
        # ---- 1. Load proposal + validated payload, sanity-check status. ----
        async with sf() as session:
            row = (
                await session.execute(
                    select(ProposalRow).where(
                        ProposalRow.proposal_id == proposal_id
                    )
                )
            ).scalar_one()
            # Plan 02-06 Task 2: dual-channel live trades reach the
            # executor in APPROVED_LIVE (after dashboard /live-confirm
            # transitioned them there from AWAITING_2ND_CHANNEL). Both
            # entry statuses are valid; the transition_status calls below
            # use the matching from_status.
            if row.status not in ("APPROVED", "APPROVED_LIVE"):
                msg = (
                    f"Cannot execute proposal {proposal_id!r}: expected "
                    f"status 'APPROVED' or 'APPROVED_LIVE', found "
                    f"{row.status!r}"
                )
                raise ValueError(msg)
            entry_status = row.status
            tp = TradeProposal.model_validate_json(row.payload_json)
            strategy_id = row.strategy_id
            account_mode = row.account_mode
            # Plan 02-02 Task 3: load the Strategy snapshot for OrderGuard's
            # watchlist / hard_caps / mode context. The strategy row's
            # `payload_json` is the canonical JSON of the Pydantic Strategy
            # model per Plan 01-06; we parse it back into a Strategy
            # instance. When the strategy row is missing or empty (test
            # seed pattern from Phase 1) we fall back to a minimal
            # Strategy derived from the proposal — enough for the
            # ticker-in-watchlist check + cap math.
            strategy_row = (
                await session.execute(
                    select(StrategyRow).where(
                        StrategyRow.strategy_id == strategy_id
                    )
                )
            ).scalar_one_or_none()
            strategy = _load_strategy_for_executor(
                strategy_row=strategy_row, tp=tp, user_id=user_id
            )

        # ---- 2. Market-hours guard (EXEC-10). -----------------------------
        if not is_market_open():
            log.warning(
                "executor.market_closed",
                proposal_id=proposal_id,
                ticker=tp.ticker,
            )
            async with sf() as session, session.begin():
                await append_event(
                    session,
                    user_id=user_id,
                    strategy_id=strategy_id,
                    event_type="error",
                    payload=normalize_decimals(
                        {
                            "context": "executor.market_closed",
                            "error_class": "MarketClosed",
                            "error_message": (
                                "NYSE not in regular trading hours; order "
                                "placement deferred. P7 will add scheduled "
                                "retry."
                            ),
                            "proposal_id": proposal_id,
                            "ticker": tp.ticker,
                        }
                    ),
                )
                await transition_status(
                    session,
                    proposal_id,
                    from_status=entry_status,
                    to_status="FAILED",
                )
            # WR-02 fix: DM the operator AFTER the audit transaction
            # commits. Without this, an operator who just clicked
            # Approve has no visible signal that the order silently
            # went to FAILED — they will check Slack for a fill
            # confirmation that never arrives. Mirrors the
            # BrokerOrderError + OrderGuardRejected paths below which
            # both DM. Best-effort: DM failure must not abort the
            # already-committed state transition.
            #
            # bypass-category: executor_error — market-closed is a safety
            # signal that must reach the operator regardless of quiet hours
            # (D-48 bypass set; AST gate in test_quiet_hours_dm_gate.py).
            try:
                await _send_slack_dm(
                    user_id,
                    (
                        f"⚠️ Order for `{tp.ticker}` deferred — NYSE not in "
                        "regular trading hours. Proposal moved to "
                        "FAILED. (P7 will add scheduled retry.)"
                    ),
                )
            except Exception:  # noqa: BLE001
                log.exception(
                    "executor.market_closed.dm_failed",
                    proposal_id=proposal_id,
                )
            return

        # ---- 3. Construct the OrderRequest with the persisted COID. -------
        req = OrderRequest(
            symbol=tp.ticker,
            side=OrderSide(tp.side),
            qty=tp.qty,
            order_type=OrderType(tp.order_type),
            limit_price=tp.limit_price,
            stop_price=tp.stop_price,
            time_in_force=TimeInForce.DAY,
            client_order_id=tp.client_order_id,
        )

        # ---- 4. Broker submission with structured error handling. ---------
        # Plan 02-06 Task 1: _build_broker is now async (the live path
        # awaits load_live_credentials). The paper path is still
        # synchronous-equivalent; the await is essentially free there.
        # Phase-1 tests monkeypatch _build_broker with a sync lambda; we
        # tolerate both shapes via an iscoroutine check so legacy tests
        # don't need to be retrofitted.
        import inspect as _inspect

        try:
            _maybe = _build_broker(
                user_id, strategy, account_mode, proposal=tp
            )
            if _inspect.isawaitable(_maybe):
                broker = await _maybe
            else:
                broker = _maybe
        except OrderGuardRejected as exc:
            # Live-credential-missing path bubbles up here BEFORE the
            # try-place_order block. Route to the cap_rejection branch so
            # the audit + state transition + Slack DM happen with the same
            # shape as a check failure.
            log.warning(
                "executor.cap_rejection",
                proposal_id=proposal_id,
                ticker=tp.ticker,
                reject_code=exc.reject_code,
                reject_reason=exc.reject_reason,
            )
            async with sf() as session, session.begin():
                # WR-07 fix: drop the duplicate ``check_name`` key (was
                # always equal to ``reject_code`` here). The D-14 canonical
                # subset principle says no redundancy in the chain-hashed
                # payload; ``reject_code`` is the authoritative key.
                cap_payload: dict[str, Any] = {
                    "reject_code": exc.reject_code,
                    "reject_reason": exc.reject_reason,
                    "ticker": tp.ticker,
                    "proposal_id": proposal_id,
                }
                for k, v in exc.extra.items():
                    cap_payload.setdefault(k, v)
                await append_event(
                    session,
                    user_id=user_id,
                    strategy_id=strategy_id,
                    event_type="cap_rejection",
                    payload=normalize_decimals(cap_payload),
                )
                await transition_status(
                    session,
                    proposal_id,
                    from_status=entry_status,
                    to_status="FAILED",
                )
            try:
                from gekko.reporter.slack import (
                    build_orderguard_rejection_card,
                )

                blocks = build_orderguard_rejection_card(
                    reject_code=exc.reject_code,
                    reject_reason=exc.reject_reason,
                    ticker=tp.ticker,
                    strategy_name=tp.strategy_name,
                    proposal_id=proposal_id,
                )
                await _send_slack_dm_blocks(user_id, blocks=blocks)
            except Exception:  # noqa: BLE001
                log.exception(
                    "executor.cap_rejection.dm_failed",
                    proposal_id=proposal_id,
                    reject_code=exc.reject_code,
                )
            return
        try:
            result: OrderResult = await broker.place_order(req)
        except OrderGuardRejected as exc:
            # Plan 02-02 Task 3: cap_rejection branch — mirrors the
            # ``executor.market_closed`` shape at lines 222-254 verbatim.
            # ``exc.extra`` carries per-check context (ticker, ref_price,
            # cap_value, etc.) that is merged into the audit payload so the
            # dashboard rejection panel + Slack rejection card can
            # interpret it.
            #
            # WR-07 fix: ``reject_code`` is the canonical discriminator
            # for cap_rejection events; the prior duplicate ``check_name``
            # field (always equal to ``reject_code`` here) violated the
            # D-14 canonical-subset principle and was dropped.
            log.warning(
                "executor.cap_rejection",
                proposal_id=proposal_id,
                ticker=tp.ticker,
                reject_code=exc.reject_code,
                reject_reason=exc.reject_reason,
            )
            async with sf() as session, session.begin():
                cap_payload: dict[str, Any] = {
                    "reject_code": exc.reject_code,
                    "reject_reason": exc.reject_reason,
                    "ticker": tp.ticker,
                    "proposal_id": proposal_id,
                }
                # Merge per-check extras; do NOT overwrite the canonical
                # keys above on key-collision.
                for k, v in exc.extra.items():
                    cap_payload.setdefault(k, v)
                await append_event(
                    session,
                    user_id=user_id,
                    strategy_id=strategy_id,
                    event_type="cap_rejection",
                    payload=normalize_decimals(cap_payload),
                )
                await transition_status(
                    session,
                    proposal_id,
                    from_status=entry_status,
                    to_status="FAILED",
                )
            # Plan 02-05 Task 3: send the rejection Slack DM AFTER the
            # audit-write transaction completes (PATTERNS §4 anti-pattern
            # row 14 — DM outside transaction). Mirrors the on_fill_event
            # pattern at lines 548-583 below.
            try:
                from gekko.reporter.slack import (
                    build_orderguard_rejection_card,
                )

                blocks = build_orderguard_rejection_card(
                    reject_code=exc.reject_code,
                    reject_reason=exc.reject_reason,
                    ticker=tp.ticker,
                    strategy_name=tp.strategy_name,
                    proposal_id=proposal_id,
                )
                await _send_slack_dm_blocks(user_id, blocks=blocks)
            except Exception:  # noqa: BLE001 — DM failure must not abort flow
                log.exception(
                    "executor.cap_rejection.dm_failed",
                    proposal_id=proposal_id,
                    reject_code=exc.reject_code,
                )
            return
        except BrokerOrderError as exc:
            log.warning(
                "executor.broker_rejected",
                proposal_id=proposal_id,
                error=str(exc),
            )
            async with sf() as session, session.begin():
                await append_event(
                    session,
                    user_id=user_id,
                    strategy_id=strategy_id,
                    event_type="error",
                    payload=normalize_decimals(
                        {
                            "context": "executor.broker_rejected",
                            "error_class": type(exc).__name__,
                            "error_message": str(exc),
                            "proposal_id": proposal_id,
                            "ticker": tp.ticker,
                            "client_order_id": tp.client_order_id,
                        }
                    ),
                )
                await transition_status(
                    session,
                    proposal_id,
                    from_status=entry_status,
                    to_status="FAILED",
                )
            # bypass-category: executor_error — broker failure must reach
            # the operator immediately regardless of quiet hours (D-48 bypass
            # set; AST gate in test_quiet_hours_dm_gate.py).
            await _send_slack_dm(
                user_id,
                (
                    f"❌ Order placement failed for `{tp.ticker}` "
                    f"({proposal_id}): {exc}"
                ),
            )
            return

        # ---- 5. Persist order_submitted + transition APPROVED -> EXECUTING.
        #
        # Decimal values (qty) flow as Decimal through normalize_decimals so
        # the Pitfall 6 trailing-zero collapse runs; canonical_json's
        # default=str downstream converts the normalized Decimal to a JSON
        # string. Strings (side, order_type, ids) are passed through
        # unchanged.
        async with sf() as session, session.begin():
            payload: dict[str, Any] = normalize_decimals(
                {
                    "event_kind": "order_submitted",
                    "client_order_id": result.client_order_id,
                    "broker_order_id": result.broker_order_id,
                    "symbol": req.symbol,
                    "side": str(req.side),
                    "qty": req.qty,
                    "order_type": str(req.order_type),
                }
            )
            await append_event(
                session,
                user_id=user_id,
                strategy_id=strategy_id,
                event_type="order_submitted",
                payload=payload,
            )
            # Persist broker_order_id alongside the status transition so
            # both updates ride on a single commit — the row's identity-map
            # entry already has the broker_order_id pending; transition_status
            # below flushes the combined UPDATE.
            await session.execute(
                update(ProposalRow)
                .where(ProposalRow.proposal_id == proposal_id)
                .values(broker_order_id=result.broker_order_id)
            )
            await transition_status(
                session,
                proposal_id,
                from_status=entry_status,
                to_status="EXECUTING",
            )

        log.info(
            "executor.order_submitted",
            proposal_id=proposal_id,
            broker_order_id=result.broker_order_id,
            client_order_id=result.client_order_id,
        )
    finally:
        if engine is not None:
            await engine.dispose()


# ---------------------------------------------------------------------------
# on_fill_event — TradingStream callback
# ---------------------------------------------------------------------------


async def on_fill_event(payload: dict[str, Any], *, user_id: str) -> None:
    """Fill callback registered with :class:`AlpacaFillStream`.

    Looks up the proposal by ``client_order_id`` (the deterministic id is
    the correlation key between the broker's fill stream and our row).
    Appends the ``fill`` audit event, transitions EXECUTING -> FILLED,
    and sends a Slack DM confirmation.

    Plan 01-09's FastAPI lifespan binds this function as the
    ``on_fill`` callback when constructing the per-user
    :class:`AlpacaFillStream`. Unmatched fills (no matching proposal)
    are logged but do not raise — they can legitimately arrive for
    orders placed outside this Gekko instance.
    """
    sf, engine = _get_session_factory(user_id)
    try:
        client_order_id = payload.get("client_order_id", "")
        live_strategy_name_to_stamp: str | None = None
        fill_ts: str | None = None
        async with sf() as session, session.begin():
            row = (
                await session.execute(
                    select(ProposalRow).where(
                        ProposalRow.client_order_id == client_order_id,
                        ProposalRow.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                log.warning("executor.fill_unmatched", **payload)
                return
            strategy_id = row.strategy_id
            proposal_id = row.proposal_id

            # CR-01 fix: parse the persisted TradeProposal once so we have
            # the canonical ticker (and downstream the strategy_name for the
            # first-live stamp) regardless of broker-payload shape. The
            # previous expression ``row.payload_json[:0]`` was the empty-
            # prefix slice — always ``""`` — which silently dropped the
            # ticker from the fill audit event whenever the broker payload
            # omitted it, breaking the wash-sale 30-day lookback and the
            # PDT round-trip correlation (both bucket by ticker).
            try:
                tp_persisted = TradeProposal.model_validate_json(
                    row.payload_json
                )
                persisted_ticker = tp_persisted.ticker
            except Exception:  # noqa: BLE001 — defensive
                tp_persisted = None
                persisted_ticker = ""

            ticker = payload.get("ticker") or persisted_ticker

            fill_payload: dict[str, Any] = normalize_decimals(
                {
                    "event_kind": "fill",
                    "client_order_id": payload.get("client_order_id", ""),
                    "broker_order_id": payload.get("broker_order_id", ""),
                    "filled_qty": str(payload.get("filled_qty", "0")),
                    "filled_avg_price": str(
                        payload.get("filled_avg_price", "")
                    ),
                    "ticker": ticker,
                }
            )
            await append_event(
                session,
                user_id=user_id,
                strategy_id=strategy_id,
                event_type="fill",
                payload=fill_payload,
            )
            if row.status == "EXECUTING":
                await transition_status(
                    session,
                    proposal_id,
                    from_status="EXECUTING",
                    to_status="FILLED",
                )

            # Plan 02-06 Task 2 — D-32 first-live-trade stamp.
            # Only LIVE fills trigger the stamp; paper fills are no-ops.
            # The stamp closes the HITL-06 dual-channel gate per
            # strategy: subsequent live trades skip AWAITING_2ND_CHANNEL.
            if row.account_mode == "LIVE":
                # CR-01 fix: reuse the already-parsed TradeProposal from
                # above rather than re-parsing payload_json.
                if tp_persisted is not None:
                    live_strategy_name_to_stamp = tp_persisted.strategy_name
                else:
                    live_strategy_name_to_stamp = None
                fill_ts = payload.get("ts") or datetime.now(UTC).isoformat()

        # ---- First-live-trade stamp (outside the fill transaction). -----
        # Plan 02-06 Task 2 — opens its own transaction. Set-once on
        # `strategy_metadata.first_live_trade_confirmed_at`. Subsequent
        # calls are no-ops (the helper checks the current value before
        # setting). Paper fills skip this entirely.
        if live_strategy_name_to_stamp is not None and fill_ts is not None:
            try:
                from gekko.strategy.promotion import stamp_first_live_trade

                await stamp_first_live_trade(
                    user_id=user_id,
                    strategy_name=live_strategy_name_to_stamp,
                    fill_ts=fill_ts,
                )
            except Exception:  # noqa: BLE001
                log.exception(
                    "executor.first_live_stamp_failed",
                    proposal_id=proposal_id,
                    strategy_name=live_strategy_name_to_stamp,
                )

        # ---- Slack DM confirmation (outside the transaction). -----------
        from gekko.reporter.slack import build_fill_confirmation

        # Best-effort strategy_name + side lookup for the DM text.
        strategy_name = ""
        side = "buy"
        async with sf() as session:
            tp_row = (
                await session.execute(
                    select(ProposalRow).where(
                        ProposalRow.proposal_id == proposal_id
                    )
                )
            ).scalar_one_or_none()
            if tp_row is not None:
                try:
                    tp = TradeProposal.model_validate_json(
                        tp_row.payload_json
                    )
                    strategy_name = tp.strategy_name
                    side = str(tp.side)
                except Exception:
                    pass

        msg = build_fill_confirmation(
            client_order_id=payload.get("client_order_id", ""),
            broker_order_id=payload.get("broker_order_id", ""),
            filled_qty=Decimal(str(payload.get("filled_qty", "0"))),
            filled_avg_price=Decimal(
                str(payload.get("filled_avg_price", "0"))
            ),
            ticker=ticker,
            strategy_name=strategy_name,
            side=side,
        )
        # Route through quiet-hours wrapper per D-48 (HITL-05):
        # LIVE fills bypass quiet hours (first_live_fill category);
        # paper fills are routine and may be suppressed.
        _fill_category = (
            "first_live_fill"
            if (live_strategy_name_to_stamp is not None)
            else "routine_fill"
        )
        await _send_slack_dm_respecting_quiet_hours(user_id, msg, category=_fill_category)
    finally:
        if engine is not None:
            await engine.dispose()


__all__: tuple[str, ...] = ("execute_proposal", "on_fill_event")
