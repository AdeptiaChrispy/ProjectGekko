---
phase: 02-orderguard-real-money-alpaca-live-safety-floor
reviewed: 2026-06-17T00:00:00Z
depth: standard
files_reviewed: 35
files_reviewed_list:
  - src/gekko/execution/orderguard.py
  - src/gekko/execution/kill_switch.py
  - src/gekko/execution/checks/__init__.py
  - src/gekko/execution/checks/_universe.py
  - src/gekko/execution/checks/_hard_caps.py
  - src/gekko/execution/checks/_qty_price.py
  - src/gekko/execution/checks/_paper_live.py
  - src/gekko/execution/checks/_kill_switch.py
  - src/gekko/execution/checks/_market_hours.py
  - src/gekko/execution/checks/_pdt.py
  - src/gekko/execution/checks/_t1.py
  - src/gekko/execution/checks/_wash_sale.py
  - src/gekko/brokers/_retry.py
  - src/gekko/research/allowlist.py
  - src/gekko/research/__init__.py
  - src/gekko/strategy/promotion.py
  - src/gekko/strategy/__init__.py
  - src/gekko/vault/credentials.py
  - src/gekko/agent/proposal_writer.py
  - src/gekko/agent/decision.py
  - src/gekko/agent/tools/propose_trade.py
  - src/gekko/approval/proposals.py
  - src/gekko/approval/slack_handler.py
  - src/gekko/execution/executor.py
  - src/gekko/brokers/alpaca.py
  - src/gekko/brokers/base.py
  - src/gekko/dashboard/app.py
  - src/gekko/dashboard/routes.py
  - src/gekko/db/models.py
  - src/gekko/schemas/proposal.py
  - src/gekko/reporter/slack.py
  - src/gekko/reporter/templates.py
  - src/gekko/slack/commands.py
  - src/gekko/cli.py
  - src/gekko/core/errors.py
findings:
  critical: 2
  blocker: 2
  warning: 7
  info: 2
  total: 11
status: issues_found
---

# Phase 2: Code Review Report

**Reviewed:** 2026-06-17
**Depth:** standard
**Files Reviewed:** 35
**Status:** issues_found

## Summary

The Phase-2 safety floor is largely sound on the load-bearing invariants. **EXEC-03/Knight Capital prevention holds** — `AlpacaBroker.place_order`, `cancel_order`, and `cancel_all_open_orders` carry zero decorators; tenacity is wired only on GET methods. **BLOCKER #5/TOCTOU closure holds** — `account_mode` is stamped by `ProposalWriter`, persisted to the column, and `_approve_workflow` + `_build_broker` read it without re-deriving from strategy state. **`_allow_live`/BLOCKER #4** is gated correctly inside `_build_broker`. **SQLCipher credential storage**: `vault/credentials.py` is explicit that blobs are plaintext and `BrokerCredential.__repr__` correctly excludes them. **HITL-06 dual-channel** is wired end-to-end with idempotent `/live-confirm/{id}`. **Kill switch DB-first ordering (D-37)** is correct.

However, there are two BLOCKER-tier findings:

1. The new Phase-2 audit event types — `credentials_added`, `live_mode_promoted`, `live_mode_demoted`, `first_live_trade_confirmed` — are NOT in the `_EVENT_TYPES` tuple. `vault/credentials.py` and `strategy/promotion.py` work around this by writing the events with `event_type="error"`, polluting the error bucket and breaking invariant #13 (each audit event "is written via append_event" — these are mis-typed). Operators filtering on `event_type='error'` will see promote/demote/credential-add noise; downstream consumers of `credentials_added` etc. cannot find them.

2. `executor.on_fill_event` writes a bogus `ticker` value to the audit log when the broker payload omits `ticker`. The expression `payload.get("ticker") or row.payload_json[:0] or ""` always evaluates to `""` on the fallback branch (because `row.payload_json[:0]` is the empty-prefix slice — always `""`). The intent was clearly to recover the ticker from the persisted TradeProposal payload. Net effect: live-trade fills with a missing payload-side ticker land in the audit log with `ticker=""`, which silently breaks the wash-sale 30-day lookback for those tickers and the PDT round-trip detector's same-ticker correlation.

Two CRITICAL findings:
- `build_first_live_card` is defined and exported but never called. The Slack approve handler for the first-live path posts a plain text DM with a raw URL instead of the rich Block Kit card. Operators will see a degraded UX on the load-bearing HITL-06 surface.
- The Slack first-live DM in `_approve_workflow` bypasses the `_send_slack_dm` identity-split seam (invariant #8). It works today only because the cross-user check immediately upstream forces `slack_user_id == settings.slack_user_id`, but the pattern is the exact regression class quick-260612-nlv was created to prevent.

Seven WARNINGs cover Slack-CONFIRM-token drift, missing operator DM on `executor.market_closed`, hash-chain pollution of `error`-typed events, and several smaller maintainability concerns.

## Critical Issues

### CR-01: `on_fill_event` writes empty `ticker` to the fill audit event (broken fallback expression)

**File:** `src/gekko/execution/executor.py:683`

**Issue:** The line

```python
ticker = payload.get("ticker") or row.payload_json[:0] or ""
```

uses `row.payload_json[:0]` as a fallback. `str[:0]` is the empty-prefix slice — always `""`, which is falsy. So when the broker fill payload does NOT carry `ticker`, the audit-log fill event gets `ticker=""` rather than the ticker from the persisted TradeProposal. Downstream consequences:

* `flag_wash_sale` walks fill events looking for same-ticker matches over a 30-day window; fills with `ticker=""` are correlated as the same "" ticker and the matcher silently misses real same-ticker prior fills.
* `check_pdt._count_round_trips` and `_would_be_round_trip` bucket fills by `(date_str, ticker)`. With `ticker=""` an actual same-ticker round-trip becomes a phantom `("YYYY-MM-DD", "")` bucket that does NOT trigger the local PDT guard.
* Live-trading audit log: the canonical record of what fired loses the ticker for any broker payload shape that omits it.

The intent was almost certainly to parse `row.payload_json` as a TradeProposal and pull `tp.ticker`. The TradeProposal is already parsed a few lines below (line 718 `TradeProposal.model_validate_json(row.payload_json)`) — that work can be hoisted, or done once at the top of the function.

**Fix:**

```python
# Parse the TradeProposal once at the top of the inner txn so we have the
# canonical ticker (and side, qty) regardless of broker-payload shape.
try:
    tp_persisted = TradeProposal.model_validate_json(row.payload_json)
    persisted_ticker = tp_persisted.ticker
except Exception:  # noqa: BLE001 — defensive
    persisted_ticker = ""

ticker = payload.get("ticker") or persisted_ticker
```

This also lets you reuse `tp_persisted` for the `live_strategy_name_to_stamp` block below instead of re-parsing.

---

### CR-02: First-live Slack DM uses plain text + bypasses `_send_slack_dm` identity-split seam

**File:** `src/gekko/approval/slack_handler.py:227-244`

**Issue:** Two related defects on the HITL-06 first-live path:

1. **Direct `client.chat_postMessage` call.** Per phase-context invariant #8, every new Slack DM path added in Phase 2 MUST go through the `_send_slack_dm(gekko_user_id, ...)` seam in `gekko.execution.executor`. The approve handler instead calls `client.chat_postMessage(channel=slack_user_id, text=...)` directly. It "works" today only because the cross-user check at line 141 just forced `slack_user_id == settings.slack_user_id`, but it's the exact pattern class quick task 260612-nlv fixed for `executor._send_slack_dm`. The seam exists so that there is ONE choke point that does the gekko_user_id → slack_user_id translation. Adding new direct DM call sites turns that into N choke points that must be audited individually.

2. **Plain text instead of `build_first_live_card`.** `gekko.reporter.slack.build_first_live_card(tp, dashboard_url)` is defined and exported (lines 522-611 of `reporter/slack.py`) but never called anywhere in `src/`. The approve handler sends a one-line plain text DM with the dashboard URL. The Block Kit card was designed (UI-SPEC §3a) with: red header, strategy/ticker/action/notional context, escaped rationale section, explicit "Slack approval alone is NOT enough" warning, a URL button, and the REG-01 disclosure footer. None of that surfaces today.

**Fix:** Build the Block Kit card and route it through the seam:

```python
if is_live_first:
    dashboard_url = getattr(
        settings, "dashboard_url", "http://localhost:8000"
    )
    # Re-parse the TP from the locked row so we can render the rich card.
    from gekko.schemas.proposal import TradeProposal as _TP
    try:
        _tp_card = _TP.model_validate_json(row.payload_json)
    except Exception:  # noqa: BLE001
        _tp_card = None
    if _tp_card is not None:
        from gekko.reporter.slack import build_first_live_card
        from gekko.execution.executor import _send_slack_dm_blocks

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
        # Defensive fallback: still route through the seam.
        from gekko.execution.executor import _send_slack_dm
        await _send_slack_dm(
            gekko_user_id,
            (
                f":warning: FIRST live trade for `{strategy_name_snapshot}`. "
                f"Confirm at {dashboard_url}/live-confirm/{decision_id}"
            ),
        )
    return
```

The cross-user "not the owner" DM at line 148 and the "Proposal not found" DM at line 168, and the "Approved …" DM at line 253 are also direct `client.chat_postMessage` calls and inherit the same identity-split risk class — those should be moved through the seam too in the same change (Phase-1 carry-forward, but the new Phase-2 dual-channel branch is the load-bearing one).

---

## Blocker Issues

### BL-01: Phase-2 audit events written with `event_type="error"` (D-14 vocabulary not extended)

**Files:**
- `src/gekko/db/models.py:71-81` (the `_EVENT_TYPES` tuple)
- `src/gekko/vault/credentials.py:124-137` (writes `credentials_added` as `error`)
- `src/gekko/strategy/promotion.py:103-115`, `152-163`, `216-228` (writes promote/demote/first-live-stamp as `error`)

**Issue:** Per phase-context invariant #13, the Phase-2 plan promises new audit `event_type`s for `credentials_added`, `live_mode_promoted`, `live_mode_demoted`, `first_live_trade_confirmed`, and (by inference) `cap_rejection`. The `_EVENT_TYPES` tuple in `db/models.py` was extended for `kill_switch` and `cap_rejection`, but **NOT** for the four credential/promotion events. Both `vault/credentials.py` and `strategy/promotion.py` work around the missing slot by writing the event as `event_type="error"` and stashing a `"context": "credentials.added" / "strategy.promoted_to_live" / "strategy.demoted_from_live" / "strategy.first_live_trade_stamped"` discriminator in the payload.

Consequences:

* The audit-event vocabulary lies about its own contents. A "Show all errors" query on the audit log surfaces routine credential additions and promotion clicks as errors. An "audit operator promoted strategy X" forensic query has to know to grep for the `context` substring inside payload_json rather than filtering on `event_type`.
* The plan's success criterion "every new event_type is written via `append_event`" is structurally unmet — the new types do not exist.
* The vault module's docstring explicitly says: "Per D-14 the only fitting event_type is ``error`` ... extending the tuple is out of scope here." That decision should be re-litigated: D-14 is the authoritative vocabulary, and shipping live trading on a tuple that papered over a known-incomplete event vocabulary makes the hash-chain payload less useful when it matters most.
* Bonus: the `_EVENT_TYPES` CheckConstraint will reject any future code that tries to write the correct event_type — so the workaround is sticky.

**Fix:** Extend `_EVENT_TYPES` and the corresponding Alembic CheckConstraint to include the four new types, then update both call sites to use the proper names:

```python
# db/models.py
_EVENT_TYPES: tuple[str, ...] = (
    "decision",
    "proposal",
    "approval",
    "rejection",
    "order_submitted",
    "fill",
    "kill_switch",
    "cap_rejection",
    "credentials_added",
    "live_mode_promoted",
    "live_mode_demoted",
    "first_live_trade_confirmed",
    "error",
)
```

This requires an Alembic migration to drop+recreate `ck_event_type` (SQLite CHECK constraints aren't ALTERable). Then in `vault/credentials.py`:

```python
await append_event(
    session,
    user_id=user_id,
    strategy_id=None,
    event_type="credentials_added",  # was "error"
    payload=normalize_decimals(
        {"broker": "alpaca", "kind": "alpaca_live", "has_key": True}
    ),
)
```

And similarly for `promote_strategy_to_live` / `demote_strategy_from_live` / `stamp_first_live_trade`.

---

### BL-02: `_execute_kill` audit event omits `tally` from `kill` action; `unkill` writes no operator-DM audit event

**File:** `src/gekko/execution/kill_switch.py:192-205, 324-346`

**Issue:** Two related audit-chain integrity gaps on the kill-switch write path:

1. The "kill" action audit event (step 1, line 192-205) intentionally writes BEFORE the cancel sweep so the row commits early — that ordering is correct. But the `kill_complete` event (step 3, line 263-279) is the one that carries the `tally`. If the cancel sweep raises an uncaught exception between step 1 and step 3 (e.g., an asyncio cancellation, or a SQLAlchemy operational error against the second `session.begin()`), the chain is left with a `kill` event but no `kill_complete` — and the tally is silently lost. The outer `try/finally` only disposes the engine; it does not write a salvage `kill_complete` event. The 5s SLA budget says "in-flight cancels keep running but we report them as pending" — but only IF the function reaches step 3.

2. `_execute_unkill` sends a Slack DM (lines 351-355) but writes NO audit event recording that the DM was attempted/succeeded. Compare to `_execute_kill` which has the `kill_complete` event. If the unkill DM fails, the log warning fires inside `_send_slack_dm`'s `_dm_kill_summary` wrapper for the kill path (line 411-418), but the unkill path has no `try/except` around `_send_slack_dm` at all — a DM failure during unkill (e.g., Slack outage) would raise out of `_execute_unkill` after the DB commit, leaving the caller's `try/except Exception` in the CLI/dashboard background wrapper to swallow it with no audit trace.

The kill switch is the safety-floor recovery surface. Its forensic story is load-bearing.

**Fix:** Wrap the cancel sweep in a `try/except Exception` that always writes the `kill_complete` event with a `"error"` field in the tally, and harden the unkill DM:

```python
# kill_switch.py — _execute_kill, around line 207-289
try:
    broker = _build_kill_broker(user_id)
    tally: dict[str, Any] = {"cancelled": 0, "pending": 0, "failed": 0, "total": 0}
    try:
        open_orders = await broker.get_orders_open()
        tally["total"] = len(open_orders)
    except Exception as e:  # noqa: BLE001
        tally["error"] = f"fetch_open: {e!s}"
        open_orders = []
    # ... existing cancel-gather block ...
except Exception as e:  # noqa: BLE001
    tally.setdefault("error", f"sweep: {e!s}")
finally:
    ts_end = datetime.now(UTC).isoformat()
    tally["ts_start"] = ts_start
    tally["ts_end"] = ts_end
    async with sf() as session, session.begin():
        await append_event(
            session,
            user_id=user_id,
            strategy_id=None,
            event_type="kill_switch",
            payload=normalize_decimals(
                {
                    "action": "kill_complete",
                    "source": source,
                    "reason": reason,
                    "ts_start": ts_start,
                    "ts_end": ts_end,
                    "tally": dict(tally),
                }
            ),
        )
    try:
        await _dm_kill_summary(user_id, tally)
    except Exception:  # noqa: BLE001
        log.exception("kill_switch.dm_outside_failed", user_id=user_id)
```

For unkill, wrap the DM:

```python
try:
    await _send_slack_dm(user_id, "✅ Kill cleared — …")
except Exception:  # noqa: BLE001 — DM failure must not abort unkill
    log.exception("kill_switch.unkill_dm_failed", user_id=user_id)
```

---

## Warnings

### WR-01: Slack `/gekko unkill` token mismatch with spec (`CONFIRM` vs `UNKILL`)

**File:** `src/gekko/slack/commands.py:307-311`

**Issue:** Phase context invariant #6 specifies: "Two-step Slack confirm (`/gekko kill CONFIRM` / `/gekko unkill UNKILL`) — literal token required as second argument." The implementation uses `CONFIRM` for BOTH kill and unkill (see `_handle_unkill_command` line 309 checking `args[0].strip().upper() != "CONFIRM"`). The CLI does use distinct tokens (`KILL` and `UNKILL` — `cli.py:585, 624`). The Slack surface is the operator's primary kill channel; copy drift between surface and spec/CLI hurts muscle memory under stress.

**Fix:** If the spec is authoritative, change the Slack unkill to require literal `UNKILL`:

```python
# slack/commands.py around line 309
if args[0].strip().upper() != "UNKILL":
    await respond(_UNKILL_MISMATCH_TEXT)
    return
```

…and update `_UNKILL_WARN_TEXT` / `_UNKILL_MISMATCH_TEXT` copy accordingly. If the code is authoritative (CONFIRM symmetric across kill/unkill) then update the phase context. Either way, pick one.

---

### WR-02: `executor.market_closed` writes audit event but never DMs the operator

**File:** `src/gekko/execution/executor.py:369-402`

**Issue:** When the market closes between executor's check and broker submission, the code writes an `error` audit event and transitions the proposal `APPROVED → FAILED`, but never sends a Slack DM to the operator. Compare to `BrokerOrderError` (line 583) and `OrderGuardRejected` (line 480, 546) which both DM. The operator who just clicked Approve has no visible signal that the order silently went to FAILED — they will check Slack for a fill confirmation that never arrives.

**Fix:**

```python
# executor.py around line 402 (just before the return)
try:
    await _send_slack_dm(
        user_id,
        (
            f"Order for `{tp.ticker}` deferred — NYSE not in regular "
            "trading hours. Proposal moved to FAILED. (P7 will add "
            "scheduled retry.)"
        ),
    )
except Exception:  # noqa: BLE001
    log.exception(
        "executor.market_closed.dm_failed",
        proposal_id=proposal_id,
    )
return
```

---

### WR-03: `_load_strategy_for_executor` synthesizes overly-permissive caps when payload_json is empty

**File:** `src/gekko/execution/executor.py:251-301`

**Issue:** The synthesized fallback Strategy (used when the strategy row has empty `payload_json`) sets:

* `max_position_pct = Decimal("0.20")` — 20% of equity
* `max_daily_loss_usd = Decimal("999999")` — effectively no daily-loss cap
* `max_trades_per_day = 999` — effectively unbounded
* `max_sector_exposure_pct = Decimal("1")` — 100% sector

…all while the Decision agent's actual proposal was authored against the REAL strategy hard caps. On the LIVE path this means: if the strategy row somehow lacks payload_json (Phase-1 test seed pattern; a botched migration; a future regression that nulls the column), OrderGuard's hard-caps check uses these wide-open synthetic caps and lets a live trade through that violates the operator's actual stated caps.

The docstring justifies this as Phase-1 test compatibility, but the same code path is hit on the live broker. A failed `Strategy.model_validate_json` exception is silently swallowed (`except Exception: pass` at line 281-282) which masks the real issue (corrupt payload) instead of fail-closed.

**Fix:** Fail closed on LIVE; degrade-open only on PAPER. Or always fail closed and update the Phase-1 test seeds to populate `payload_json`.

```python
def _load_strategy_for_executor(...) -> Strategy:
    if strategy_row is not None and strategy_row.payload_json:
        try:
            return Strategy.model_validate_json(strategy_row.payload_json)
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "executor.strategy_payload_parse_failed",
                strategy_id=getattr(strategy_row, "strategy_id", None),
                error=str(exc),
            )
            if tp.account_mode == "LIVE":
                # Fail closed on the live path — never synthesize permissive caps.
                raise

    if tp.account_mode == "LIVE":
        msg = (
            f"Cannot execute LIVE proposal {tp.decision_id} against synthesized "
            f"strategy — refusing to substitute permissive caps for real money."
        )
        raise ValueError(msg)
    # PAPER fallback retains existing synth path.
```

---

### WR-04: `cancel_order` not decorated, but no docstring contract that it's intentional

**File:** `src/gekko/brokers/alpaca.py:283-290`

**Issue:** Per RESEARCH §6 Open Question #1, `cancel_order` AND `cancel_all_open_orders` are both intentionally undecorated. `cancel_all_open_orders` carries a 6-line docstring explaining this verbatim. `cancel_order` does NOT — it just says "P1 keeps this minimal — rate-limit hardening and retry policy land in Phase 2's OrderGuard per SKELETON.md". A future Phase-3 contributor adding tenacity to broker GETs could plausibly read this as "cancel_order is just not done yet" and add a retry decorator, breaking the kill-switch's failure-tolerance contract.

**Fix:** Match `cancel_all_open_orders`'s docstring shape — add an explicit "MUST NOT be decorated" note pointing at RESEARCH §6.

```python
async def cancel_order(self, broker_order_id: str) -> bool:
    """Cancel an open order. Returns True on success.

    Per RESEARCH §6 Open Question #1: this method is INTENTIONALLY NOT
    decorated with ``@retry_on_rate_limit`` (same rationale as
    ``cancel_all_open_orders``). A 429 retry storm during a kill is the
    worst possible failure mode — the kill switch's ``asyncio.gather`` +
    4s timeout is the failure-tolerant scaffold. Tenacity here would
    convert a 429 into ~5 minutes of retries during a cancel sweep.

    The AST gate in ``tests/unit/test_alpaca_retry.py`` enforces zero
    decorators on this method.
    """
```

---

### WR-05: `OrderGuard.cancel_order` passthrough also undecorated but undocumented

**File:** `src/gekko/execution/orderguard.py:151-152`

**Issue:** `OrderGuard.cancel_order` is a pure passthrough; the docstring at lines 41-45 says GETs and `cancel_order` "pass through unchanged" but doesn't reiterate that `cancel_order` MUST stay undecorated at the OrderGuard layer too. The Knight Capital pitfall lives on the wrapped broker side, but adding a retry decorator on the OrderGuard wrap would equally break kill timing.

**Fix:** One-line docstring on `cancel_order` matching the `cancel_all_open_orders` comment at line 161-163.

---

### WR-06: `slack_handler._approve_workflow` carries direct `client.chat_postMessage` calls beyond the first-live DM

**File:** `src/gekko/approval/slack_handler.py:148-152, 168-172, 236-244, 253-256, 306-310, 317-321, 325-328`

**Issue:** Beyond CR-02's first-live DM, this module sprays direct `client.chat_postMessage(channel=slack_user_id, …)` calls across the cross-user-refused branch, proposal-not-found branch, approved-confirmation, etc. None route through `_send_slack_dm`. While the cross-user guard means `slack_user_id == settings.slack_user_id` when these fire (so the bug class is latent, not active), the surface area for a future regression is large.

**Fix:** Route every Slack DM in approve / reject workflows through `_send_slack_dm(gekko_user_id, …)`. The Phase-1 carry-forward implementation already standardized on this seam for the executor; the approval module is the last hold-out.

---

### WR-07: `cap_rejection` payload's `check_name` field duplicates `reject_code`

**File:** `src/gekko/execution/executor.py:446-454, 506-516`

**Issue:** Both `OrderGuardRejected` handler branches build the cap_rejection payload with both `reject_code=exc.reject_code` and `check_name=exc.reject_code` set to the same value. Then the loop merges `exc.extra` with `setdefault` — if `exc.extra` happens to carry its own `check_name`, it's silently ignored.

This is more of a smell than a bug — but the audit log will carry two keys that always agree, which violates the D-14 canonical-subset principle (no redundancy in the chain-hashed payload). Future consumers will not know which to trust.

**Fix:** Drop `check_name` (or drop `reject_code`) and document one of them as canonical:

```python
cap_payload: dict[str, Any] = {
    "reject_code": exc.reject_code,
    "reject_reason": exc.reject_reason,
    "ticker": tp.ticker,
    "proposal_id": proposal_id,
}
# Merge per-check extras; canonical keys above are not overwritten.
for k, v in exc.extra.items():
    cap_payload.setdefault(k, v)
```

---

## Info

### IN-01: Sector-exposure check loops `get_asset` per position (no batching)

**File:** `src/gekko/execution/checks/_hard_caps.py:295-303`

**Issue:** `_check_sector_exposure` calls `_resolve_sector(broker, sym)` once per position, each issuing a separate `client.get_asset` HTTP call wrapped in `asyncio.to_thread`. With 50 open positions and a paper account on a slow link, this is 50 sequential blocking calls. Out of v1 perf scope, but flagged because the `cap_rejection` path runs synchronously before broker submission — the user-facing approve-to-fill latency directly absorbs this cost.

**Fix:** Phase-3 — batch resolution via `get_all_assets` once, build a `{symbol: sector}` map. Out of scope here.

---

### IN-02: `_check_daily_loss` walks events without a row cap (works today; forward concern)

**File:** `src/gekko/execution/checks/_hard_caps.py:135-196`

**Issue:** `_check_daily_loss` selects ALL `event_type='fill'` rows for today UTC and walks them in Python to sum `realized_pnl_usd`. No `LIMIT` clause. The docstring acknowledges that current Phase-1 fills don't carry `realized_pnl_usd` (so the loop is a no-op today). Once Phase-3 lands cost-basis math, a high-volume strategy day could put hundreds of rows in this scan; out of v1 perf scope but worth noting since the scan runs on every `place_order`.

**Fix:** Add a `LIMIT 1000` on the select (covers any realistic daily fill volume comfortably) and consider moving the realized P&L aggregation into a SQL `SUM(...)` once the column exists.

---

## Structural Findings (fallow)

No structural-findings block was provided with this review. Cross-module structural signals worth surfacing from the read:

* `gekko.reporter.slack.build_first_live_card` — defined and exported, no callers in `src/` (see CR-02).
* `gekko.research.__init__` — empty docstring-only file; the documented `WEB_ALLOWLIST` re-export does NOT actually happen at the package level (callers must import from `gekko.research.allowlist`). Consider an `__all__` + re-export there or update the docstring claim.
* The four `_get_session_factory` shims in `executor.py`, `checks/_kill_switch.py`, `checks/_hard_caps.py`, `checks/_pdt.py`, `checks/_wash_sale.py`, `vault/credentials.py`, `strategy/promotion.py`, `kill_switch.py`, `approval/slack_handler.py` are verbatim duplicates. PATTERNS §3c explicitly calls this out as intended per-module isolation. The byte-level duplication is fine; the doc-level "verbatim copy of" comments are accurate. No action required, but worth noting that any future change to the engine-lifecycle contract must touch nine sites.

## Narrative Findings (AI reviewer)

The findings above (CR-01 through IN-02) are the narrative findings.

---

_Reviewed: 2026-06-17_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
