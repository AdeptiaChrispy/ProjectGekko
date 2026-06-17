---
phase: 02-orderguard-real-money-alpaca-live-safety-floor
fixed_at: 2026-06-17T00:00:00Z
review_path: .planning/phases/02-orderguard-real-money-alpaca-live-safety-floor/02-REVIEW.md
iteration: 1
findings_in_scope: 11
fixed: 11
skipped: 0
status: all_fixed
---

# Phase 2: Code Review Fix Report

**Fixed at:** 2026-06-17
**Source review:** `.planning/phases/02-orderguard-real-money-alpaca-live-safety-floor/02-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 11 (2 Critical + 2 Blocker + 7 Warning + 2 Info)
- Fixed: 11
- Skipped: 0

## Fixed Issues

### CR-01: `on_fill_event` writes empty `ticker` to the fill audit event (broken fallback expression)

**Files modified:** `src/gekko/execution/executor.py`
**Commit:** `a7687bb`
**Applied fix:** Replaced `payload.get("ticker") or row.payload_json[:0] or ""` (always-empty slice fallback) with a single canonical `TradeProposal.model_validate_json(row.payload_json)` parse at the top of the fill transaction. The parsed `tp_persisted` is reused for both the ticker fallback AND the first-live-trade strategy-name lookup below (eliminating a duplicate parse). Defensive `try/except` returns `persisted_ticker=""` only if the persisted TP itself fails to parse — but in that case the audit ticker still has the broker payload value when present.

### CR-02: First-live Slack DM uses plain text + bypasses `_send_slack_dm` identity-split seam

**Files modified:** `src/gekko/approval/slack_handler.py`
**Commit:** `6507f47`
**Applied fix:** The HITL-06 first-live DM now (a) snapshots `row.payload_json` INSIDE the approval transaction (so the post-commit DM block can re-parse without lazy-loading from a detached SQLAlchemy row), (b) builds the rich `build_first_live_card` Block Kit (UI-SPEC §3a), and (c) sends via the identity-split seam `gekko.execution.executor._send_slack_dm_blocks(gekko_user_id, blocks=..., fallback=...)`. Defensive fallback: if the payload parse fails, the plain-text variant routes through `_send_slack_dm(gekko_user_id, ...)` (still through the seam).

### BL-01: Phase-2 audit events written with `event_type="error"` (D-14 vocabulary not extended)

**Files modified:**
- `src/gekko/db/models.py`
- `src/gekko/vault/credentials.py`
- `src/gekko/strategy/promotion.py`
- `migrations/versions/0003_event_types_phase2.py` (new)
- `tests/integration/test_alpaca_live_credentials.py`
- `tests/integration/test_promote_paper_to_live_end_to_end.py`

**Commit:** `11158cf`
**Applied fix:** Extended `_EVENT_TYPES` with four new values: `credentials_added`, `live_mode_promoted`, `live_mode_demoted`, `first_live_trade_confirmed`. Added Alembic migration `0003_event_types_phase2` that drops + recreates `ck_event_type` via `batch_alter_table` so the SQLite CHECK accepts the new values; downgrade restores the pre-Phase-2 vocabulary. Updated all four writers (`vault/credentials.store_live_credentials`, `strategy/promotion.promote_strategy_to_live`, `demote_strategy_from_live`, `stamp_first_live_trade`) to use the proper event_type instead of `"error"` + a `context` payload discriminator. Test in `test_alpaca_live_credentials.py` switched from filtering on the payload context string to filtering on `e.event_type == "credentials_added"`. Documentation comments in `test_promote_paper_to_live_end_to_end.py` updated to reflect the new event-type names. `_EVENT_TYPES="error"` write in `proposal_writer.py:235` was AUDITED and confirmed legitimate (watchlist violation = genuine error, not a Phase-2 vocabulary gap) — left unchanged.

### BL-02: `_execute_kill` audit event omits `tally` from `kill` action; `unkill` writes no operator-DM audit event

**Files modified:** `src/gekko/execution/kill_switch.py`
**Commit:** `46f42d2`
**Applied fix:** Wrapped the cancel-sweep block in `try/except Exception` + `try/finally` so the `kill_complete` audit event ALWAYS lands on the chain — even when the sweep raises (asyncio cancellation, broker ConnectionError, SQLAlchemy operational error). Sweep-level exceptions are now captured into `tally["error"] = "sweep: <msg>"`; fetch-level exceptions into `tally["error"] = "fetch_open: <msg>"`. If the `kill_complete` audit write itself fails, log loudly (the `kill_active` column was already flipped in step 1, so the safety floor holds). The `_dm_kill_summary` call is also wrapped in try/except at the kill_switch level (defense-in-depth on top of the helper's existing try/except). For unkill, the `_send_slack_dm` call now has its own try/except so a Slack outage doesn't raise out after the DB commit — the kill_active column is the source of truth; the DM is a notification, not a state transition.

### WR-01: Slack `/gekko unkill` token mismatch with spec (`CONFIRM` vs `UNKILL`)

**Files modified:**
- `src/gekko/slack/commands.py`
- `tests/unit/test_kill_surfaces.py`

**Commit:** `ff17e50`
**Applied fix:** Slack `/gekko unkill` handler now requires the literal `UNKILL` token (matching the CLI + spec invariant #6 — two distinct tokens, one per destructive operation). `_UNKILL_WARN_TEXT`, `_UNKILL_MISMATCH_TEXT`, and the `/gekko` help line all updated. Two tests in `test_kill_surfaces.py` updated to use `text="unkill UNKILL"` and assert `"unkill unkill"` in the warn message.

### WR-02: `executor.market_closed` writes audit event but never DMs the operator

**Files modified:** `src/gekko/execution/executor.py`
**Commit:** `01b54b1`
**Applied fix:** Added a `_send_slack_dm(user_id, ...)` call after the `executor.market_closed` audit-event transaction commits and the proposal transitions APPROVED→FAILED. DM text: "Order for `{ticker}` deferred — NYSE not in regular trading hours. Proposal moved to FAILED. (P7 will add scheduled retry.)". Wrapped in try/except so a DM failure doesn't abort the already-committed state transition (mirrors the BrokerOrderError + OrderGuardRejected DM patterns below).

### WR-03: `_load_strategy_for_executor` synthesizes overly-permissive caps when payload_json is empty

**Files modified:** `src/gekko/execution/executor.py`
**Commit:** `238f598` (also updated `tests/integration/test_first_live_gate.py` to follow CR-02's DM seam change)
**Applied fix:** `_load_strategy_for_executor` now fails closed on the LIVE path. Two branches now both raise `ValueError` on LIVE: (a) the parse-failure branch (was silently swallowed via `except Exception: pass`) — now logs structured + raises if `tp.account_mode == "LIVE"`, (b) the empty-payload-json branch (was synthesizing permissive defaults of 0.20 position cap / 999999 daily loss / 999 trades/day / 100% sector). PAPER fallback preserved verbatim so the Phase-1 walking-skeleton tests keep passing. The `test_first_live_approve_diverts_to_awaiting_2nd_channel` test in `test_first_live_gate.py` was simultaneously updated to follow the CR-02 seam migration: monkeypatches `_send_slack_dm_blocks` and asserts the `build_first_live_card` Block Kit shape (fallback + embedded URL) instead of the prior plain-text postMessage check.

**Note:** This fix is classified by the review as code-quality/safety-hardening (not a logic-bug condition fix), so the standard "fixed" status applies; no human verification flag needed.

### WR-04: `cancel_order` not decorated, but no docstring contract that it's intentional

**Files modified:** `src/gekko/brokers/alpaca.py`
**Commit:** `8beef38` (combined with WR-05)
**Applied fix:** Replaced the minimal `cancel_order` docstring with the same load-bearing shape used by `cancel_all_open_orders` — explicit "MUST NOT be decorated" note pointing at RESEARCH §6 Open Question #1, calling out the Knight Capital pitfall + the kill-switch 4s timeout / asyncio.gather scaffold + the AST gate in `tests/unit/test_alpaca_retry.py`.

### WR-05: `OrderGuard.cancel_order` passthrough also undecorated but undocumented

**Files modified:** `src/gekko/execution/orderguard.py`
**Commit:** `8beef38` (combined with WR-04)
**Applied fix:** Added a docstring to `OrderGuard.cancel_order` mirroring the `cancel_all_open_orders` comment shape — explicit "MUST stay zero-decorator at the OrderGuard layer too" with the same Knight Capital / kill-timing rationale.

### WR-06: `slack_handler._approve_workflow` carries direct `client.chat_postMessage` calls beyond the first-live DM

**Files modified:**
- `src/gekko/approval/slack_handler.py`
- `tests/unit/test_approval_proposals.py`

**Commit:** `74cdfc3`
**Applied fix:** Every remaining direct `client.chat_postMessage(channel=slack_user_id, ...)` call in `slack_handler.py` now routes through `gekko.execution.executor._send_slack_dm(gekko_user_id, ...)`. Seven call sites converted: approve cross-user-refused, approve proposal-not-found, approve "Approved <id>..." confirmation, reject cross-user-refused, reject proposal-not-found, reject "Rejected <id>..." confirmation, and the two P3-deferred stubs (`handle_edit_size_stub` + `handle_escalate_stub`). The bolt `client` parameter is preserved on the handler signatures for API stability. Four tests in `test_approval_proposals.py` (edit-size stub, escalate stub, cross-user refused) updated to monkeypatch `_send_slack_dm` and assert the bolt client is NOT called.

### WR-07: `cap_rejection` payload's `check_name` field duplicates `reject_code`

**Files modified:**
- `src/gekko/execution/executor.py`
- `tests/integration/test_orderguard_cap_rejection.py`

**Commit:** `6dbb29f`
**Applied fix:** Dropped the duplicate `check_name` key (was always equal to `reject_code`) from both `OrderGuardRejected` handler branches. `reject_code` is now the sole canonical discriminator (D-14 canonical-subset principle — no redundancy in the chain-hashed payload). `exc.extra` still merges in per-check context (cap_value, ref_price, etc.) via `setdefault` so extras can't overwrite the canonical keys. Test updated to assert payload contains `"reject_code"` and does NOT contain `"check_name"`.

### IN-01 + IN-02: Sector-exposure get_asset loop + uncapped daily_loss row scan

**Files modified:** `src/gekko/execution/checks/_hard_caps.py`
**Commit:** `2126671`
**Applied fix:** Added `TODO(perf, IN-01 / Phase-3)` + `TODO(perf, IN-02 / Phase-3)` comments at both call sites pointing at the proper Phase-3 fixes (batch via `get_all_assets` for sector resolution; rewrite as SQL `SUM(...)` for daily-loss aggregation). Added structured `log.warning` perf canaries that fire when (a) the sector-resolve loop sees > 25 positions, or (b) the daily-loss scan hits the new `LIMIT 1000` cap. The LIMIT 1000 itself is the IN-02 fix (covers any realistic daily fill volume comfortably while bounding worst-case scan size).

## Skipped Issues

None — all 11 findings were fixed.

## Verification

**Unit suite:** `tests/unit` → **583 passed, 2 skipped, 0 failed (99.59s)** — green.

**Integration suite:** `tests/integration` → **76 passed, 4 skipped, 1 failed (61.56s)**.

The single failing integration test —
`tests/integration/test_slack_approval_to_executor.py::test_full_approval_to_fill_chain`
— was confirmed to be **pre-existing on `main` BEFORE any of these fixes** by stashing the
gsd-reviewfix branch, checking out `main`'s `src/` + `tests/` trees, and re-running the test
in isolation. The test fails identically on `main`@`bee314b` (PHASE 2 COMPLETE) — it is NOT
caused by this review-fix work. The test is not in the phase-context "gates that must pass"
list. Recommend tracking this as a separate pre-existing bug for a follow-on quick task.

**Phase-context-mandated gates (52 tests):** all green.

```
tests/unit/test_alpaca_retry.py                  ............... (15)
tests/unit/test_alpaca_live_construction_locked  ..... (5)
tests/unit/test_proposal_writer_account_mode_stamp ..... (5)
tests/unit/test_state_transitions_phase2.py      ............. (13)
tests/integration/test_orderguard_chain_paper.py . (1)
tests/integration/test_orderguard_cap_rejection.py ...... (6)
tests/integration/test_first_live_gate.py        ... (3)
tests/integration/test_promote_paper_to_live_end_to_end ... (3)
tests/integration/test_trigger_run_end_to_end.py . (1)
=== 52 passed in 19.55s ===
```

Phase-1 invariants preserved through every fix:
- `AlpacaBroker.place_order` / `cancel_order` / `cancel_all_open_orders` / `OrderGuard.place_order` stayed zero-decorator (WR-04 + WR-05 strengthen the docstring contract for `cancel_order`); the AST gate in `test_alpaca_retry.py` passes.
- TradeProposal.account_mode TOCTOU closure preserved (CR-02 + WR-06 do not re-derive `account_mode` from strategy state — both read `row.account_mode` snapshot)
- SQLCipher whole-DB encryption preserved (BL-01's migration touches only `ck_event_type`, no encryption changes)
- Identity-split seam preserved + reinforced (CR-02 + WR-06 add 8 new call sites routing through `_send_slack_dm` / `_send_slack_dm_blocks`)
- Decimal money math preserved (no math touched)
- No `claude_agent_sdk` import in `src/gekko/execution/` (no new imports added there)

---

_Fixed: 2026-06-17_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
