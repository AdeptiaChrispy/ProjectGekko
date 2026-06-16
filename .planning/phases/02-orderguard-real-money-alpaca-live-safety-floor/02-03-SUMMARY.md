---
phase: 02-orderguard-real-money-alpaca-live-safety-floor
plan: 03
subsystem: execution, brokerage, audit, regulatory

tags: [orderguard, tenacity, retry-decorator, pdt, t1-settlement, wash-sale, knight-capital, ast-gate, regulatory-block, flag-only]

# Dependency graph
requires:
  - phase: 02-orderguard-real-money-alpaca-live-safety-floor
    plan: 02
    provides: |
      - OrderGuard(Brokerage) decorator class with 6 BLOCK checks landed
      - Per-check module pattern under src/gekko/execution/checks/_*.py
      - _get_session_factory test seam (PATTERNS §3c) for DB-touching checks
      - OrderGuard.place_order zero-decorator AST gate (extended by 02-03 to also cover AlpacaBroker.cancel_order)
      - cap_rejection branch in execute_proposal with locked 11-entry reject_code vocabulary
provides:
  - "src/gekko/brokers/_retry.py — tenacity retry_on_rate_limit decorator factory for HTTP 429 (EXEC-08)"
  - "_is_rate_limit predicate — APIError.status_code == 429 primary + text-match fallback (rate limit / too many requests / ' 429')"
  - "AlpacaBroker GET methods (get_account, get_positions, get_quote, get_order_by_client_order_id) decorated with @retry_on_rate_limit"
  - "AlpacaBroker.place_order + cancel_order remain zero-decorator (EXEC-03 / Pitfall 4 / Knight Capital invariant — RESEARCH §6 Open Question #1 explicit decision for cancel_order)"
  - "OrderGuard.place_order remains zero-decorator (defense in depth)"
  - "src/gekko/execution/checks/_pdt.py — two-source Pattern Day Trader BLOCK (broker primary + local 5-business-day round-trip walk via pandas_market_calendars; EXEC-11)"
  - "src/gekko/execution/checks/_t1.py — T+1 settlement BLOCK on cash accounts using non_marginable_buying_power; SELL exempt; margin (shorting_enabled=True) exempt (EXEC-11)"
  - "src/gekko/execution/checks/_wash_sale.py — flag_wash_sale(req, user_id) -> dict | None; 30-day same-ticker lookback; NEVER raises; FLAG-only contract (EXEC-09)"
  - "ProposalWriter._write_trade stamps wash_sale_flag onto TradeProposal at proposal-build time (D-28)"
  - "OrderGuard.place_order chain extended: shared broker.get_account() call feeds check_pdt + check_t1_settlement (RESEARCH §1 — avoids duplicate broker HTTP)"
  - "Reject-code vocabulary extended (forward-additive): pdt_rule, pdt_rule_local, t1_settlement (3 new entries; brings locked total to 14)"
  - "AST-walk gate (WARNING #2 fix): tests/unit/test_alpaca_retry.py parses source tree, asserts AlpacaBroker.place_order/cancel_order AND OrderGuard.place_order all have decorator_list==[]; AlpacaBroker GETs have decorator_list containing 'retry_on_rate_limit'"
  - "Runtime __wrapped__ introspection assertions for all 4 GETs + 3 POST/cancel sites"
affects: [02-05-kill-switch, 02-06-live-credentials-and-dual-channel, 02-07-promote-paper-to-live-end-to-end]

# Tech tracking
tech-stack:
  added:
    - "(none — tenacity 9.1.4 was installed in Plan 02-01 behind operator-verified PyPI legitimacy gate; this plan only consumes it)"
  patterns:
    - "tenacity decorator-factory pattern with retry_if_exception predicate. retry_on_rate_limit is a single module-level decorator-factory exposed by gekko.brokers._retry; applied via @retry_on_rate_limit on the AlpacaBroker GET methods. Reraise=True surfaces the underlying APIError after attempts exhaust (NOT the tenacity RetryError wrapper)."
    - "AST-walk gate over multiple class+method targets (WARNING #2 fix). Replaces the brittle text-grep approach used in earlier Phase-1 grep gates — ast.parse + ast.walk inspects the decorator_list of named class methods directly. Catches comment-out tricks, whitespace games, # noqa markers that text-grep missed."
    - "Two-source defense-in-depth for regulatory checks. check_pdt consults both the broker's pattern_day_trader/daytrade_count flags AND a local 5-business-day round-trip walk over the events table. Either source can independently trigger BLOCK. RESEARCH §4 verbatim pattern — broker is the source-of-record, local audit-log is the source-of-truth."
    - "Business-day arithmetic via pandas_market_calendars. _business_day_window_start uses NYSE calendar to compute the rolling 5-business-day window (handles weekends + holidays naturally). Re-uses Phase-1's existing dependency (no new packages)."
    - "FLAG-only contract enforced by try/except wrapper. flag_wash_sale wraps its entire body in `try: ... except Exception: log + return None`. Tripwire test_wash_sale_never_raises_on_db_failure asserts a broken session factory does NOT propagate. PATTERNS §4 anti-pattern row 12 (wash-sale BLOCK)."
    - "ProposalWriter merge-then-attach pattern. flag_wash_sale is called AFTER client_order_id is computed but BEFORE the proposal-row INSERT, so the attached flag rides into payload_json + the proposal audit-event payload in the same transaction (D-15 — full structured rationale)."
    - "Shared broker.get_account() call across check_pdt + check_t1_settlement. OrderGuard.place_order fetches the account once and passes it to both checks (RESEARCH §1 — avoids duplicate broker HTTP traffic). The @retry_on_rate_limit decorator on AlpacaBroker.get_account provides the 429-handling layer."

key-files:
  created:
    - "src/gekko/brokers/_retry.py - tenacity retry_on_rate_limit factory + _is_rate_limit predicate (91 lines)"
    - "src/gekko/execution/checks/_pdt.py - check_pdt two-source Pattern Day Trader BLOCK (262 lines)"
    - "src/gekko/execution/checks/_t1.py - check_t1_settlement T+1 unsettled-cash BLOCK (135 lines)"
    - "src/gekko/execution/checks/_wash_sale.py - flag_wash_sale 30-day FLAG helper (157 lines)"
  modified:
    - "src/gekko/brokers/alpaca.py - imported retry_on_rate_limit; decorated get_account / get_positions / get_quote / get_order_by_client_order_id"
    - "src/gekko/execution/checks/__init__.py - re-exports for check_pdt + check_t1_settlement + flag_wash_sale; module-docstring updated"
    - "src/gekko/execution/orderguard.py - place_order chain extended: shared get_account call + check_pdt + check_t1_settlement inserted AFTER qty_price_sanity and BEFORE market_hours"
    - "src/gekko/agent/proposal_writer.py - _write_trade calls flag_wash_sale AFTER client_order_id computation; result attached via tp.model_copy(update={wash_sale_flag: ...})"
    - "tests/unit/test_alpaca_retry.py - WAVE-0 stub replaced with 15 real assertions (AST gates + runtime __wrapped__ checks + orderguard module no-tenacity-import gate)"
    - "tests/unit/test_rate_limit_backoff.py - WAVE-0 stub replaced with 15 real assertions (_is_rate_limit predicate + retry behavior)"
    - "tests/unit/test_pdt_t1_detection.py - WAVE-0 stub replaced with 18 real assertions (6 PDT broker-source + 4 PDT local-source + 7 T+1 + 1 imports smoke)"
    - "tests/unit/test_wash_sale.py - WAVE-0 stub replaced with 8 real assertions (window matching + tripwire never-raises + signature contract)"
    - "tests/unit/test_wash_sale_flag.py - WAVE-0 stub replaced with 7 real assertions (dict shape lock + ProposalWriter wiring + OrderGuard invariant)"

key-decisions:
  - "Module path is src/gekko/brokers/_retry.py, NOT src/gekko/execution/backoff.py. The plan frontmatter `files_modified` field is authoritative (orchestrator prompt had a stale path suggestion). _retry.py lives under brokers/ because the retry decorator is broker-specific — it's tightly coupled to the alpaca-py APIError shape via _is_rate_limit. A future IBKR/Schwab broker would either reuse this module (if the exception shape is compatible) or add its own _retry.py."
  - "_is_rate_limit text-match is intentionally LOOSE (' 429' substring matches '4290' / '42910'). RESEARCH §6 documents the trade-off: spurious retries on a transient 429-lookalike body are cheaper than missing a real 429. Test test_is_rate_limit_text_match_is_substring_loose locks in the documented behavior so a future tightening (e.g., to a regex with word boundary) is a deliberate change with a test failure."
  - "AlpacaBroker.cancel_order stays zero-decorator (RESEARCH §6 Open Question #1 explicit decision). A 429 retry storm during a kill is the worst possible failure mode; plan 02-05's kill switch relies on cancel failing fast within the 4s asyncio.wait_for timeout. Tenacity would convert that to ~5 minutes of retries."
  - "AST-walk gate is the canonical EXEC-03 enforcement after WARNING #2 (text-grep was brittle to comment placement, # noqa markers, and decorator-factory reassignment). tests/unit/test_alpaca_retry.py parses src/gekko/brokers/alpaca.py and src/gekko/execution/orderguard.py via ast.parse(Path(...).read_text()) and walks ClassDef → FunctionDef nodes."
  - "AST positive control on get_account confirms the inspection is reading the real tree. If a future refactor accidentally removed @retry_on_rate_limit from get_account, the positive control would fail — proving the entire AST gate isn't silently passing on a stub. Belt-and-braces."
  - "OrderGuard's place_order shares ONE broker.get_account() call between check_pdt + check_t1_settlement. RESEARCH §1 implicit recommendation; avoids duplicate broker HTTP. The @retry_on_rate_limit decorator on get_account provides 429-handling for the shared fetch."
  - "PDT detection uses TWO independent sources (defense in depth per RESEARCH §4). Source 1 (broker primary): pattern_day_trader bool + daytrade_count int + equity. Source 2 (local audit-log): pandas_market_calendars-based 5-business-day window walk + _count_round_trips bucket-by-(date,ticker) BUY+SELL detection + _would_be_round_trip predicate for the incoming order. Either source can independently raise OrderGuardRejected with reject_code='pdt_rule' or 'pdt_rule_local'."
  - "Equity >= $25K short-circuits BOTH PDT sources (the rule does not apply above the threshold). Local-source walk is SKIPPED to save the DB hit when equity is above threshold."
  - "T+1 uses non_marginable_buying_power as the settled-cash proxy (RESEARCH §4 Alpaca-verified field). On margin accounts shorting_enabled=True bypasses entirely (the broker advances credit). SELL is exempt (no proceeds being spent). MARKET orders fetch ref_price from broker.get_quote (or the account dict's cached last_quote_ask if present)."
  - "Wash-sale FLAG dict shape is LOCKED per RESEARCH §5: 7 keys exactly (would_be_wash_sale, lookback_event_id, lookback_date, ticker, lookback_qty, lookback_side, note). test_wash_sale_flag_dict_shape asserts set(flag.keys()) == expected_keys so any future drift is caught."
  - "ProposalWriter calls flag_wash_sale AFTER the client_order_id is computed but BEFORE the proposal-row INSERT (same transaction as the row insert + audit events). This preserves the existing watchlist-violation rejection-path semantics (caller's transaction commits or rolls back atomically with the audit error event)."
  - "OrderGuard does NOT import or call flag_wash_sale (EXEC-09 / D-29 invariant). Two tripwires: (1) AST walk over orderguard.py asserts no Name node references 'flag_wash_sale' and no ImportFrom imports it; (2) end-to-end test_orderguard_place_order_does_not_block_on_wash_sale runs a TradeProposal carrying wash_sale_flag={...} through OrderGuard.place_order and asserts success (the wrapped broker's place_order is awaited)."
  - "[Rule 1 - Bug] Auto-fixed test pollution in test_rate_limit_backoff.py. Phase-1's tests/unit/test_alpaca_place_order.py mutates the alpaca-py APIError.status_code property AT THE CLASS LEVEL via `type(api_err).status_code = property(lambda self: 422)` — this permanently rebinds the property on the parent for the rest of the pytest session. _make_api_error in test_rate_limit_backoff.py now builds a fresh per-call subclass with its own status_code property that shadows the parent. Out-of-scope to fix Phase-1's pollution at its source (unrelated tests)."

patterns-established:
  - "Tenacity retry-decorator factory pattern with predicate-driven retry. retry_on_rate_limit is a module-level singleton that consumers apply via @retry_on_rate_limit on async methods. _is_rate_limit predicate isolates the matching logic so policy changes (e.g., adding 503 retry) require only a predicate edit, not a full decorator rebuild."
  - "AST-walk gate over named class methods. _find_class + _find_method helpers in test_alpaca_retry.py make the gate readable: tree = ast.parse(...) → cls = _find_class(tree, 'AlpacaBroker') → method = _find_method(cls, 'place_order') → assert method.decorator_list == []. A future plan adding a new broker can copy this gate verbatim with one class-name change."
  - "Two-source regulatory-check pattern. Broker primary + local audit-log defense, both writing to the SAME reject_code namespace (different codes — pdt_rule vs pdt_rule_local). Plans 02-05 (kill-switch read/write split) + 02-06 (live credentials) can reuse this shape."
  - "FLAG-only contract enforced via outer exception guard. flag_wash_sale's body is wrapped in try/except so a broken DB / malformed payload / etc. returns None instead of raising. Tripwire test directly tests this — patches the session factory to raise and asserts no exception propagates."
  - "Shared-account-fetch pattern in OrderGuard. Multi-check pipelines that all need broker.get_account() should share ONE call. RESEARCH §1 explicit recommendation."

requirements-completed:
  - "EXEC-03 (Knight Capital prevention strengthened) — AST-walk gate (WARNING #2 fix) now asserts both AlpacaBroker.place_order AND OrderGuard.place_order AND AlpacaBroker.cancel_order have empty decorator_list. Runtime __wrapped__ introspection adds belt-and-braces."
  - "EXEC-08 (broker rate-limit backoff) — full implementation. tenacity retry_on_rate_limit applied to all 4 AlpacaBroker GET methods. Stop after 6 attempts; wait_random_exponential(min=1, max=60) with jitter; retry_if_exception(_is_rate_limit); reraise=True."
  - "EXEC-09 (wash-sale FLAG) — full implementation. flag_wash_sale 30-day same-ticker lookback. NEVER raises (FLAG-only contract). ProposalWriter stamps the flag onto TradeProposal.wash_sale_flag at proposal-build time."
  - "EXEC-11 (PDT + T+1 BLOCK) — full implementation. check_pdt two-source defense (broker primary + local audit-log). check_t1_settlement on cash accounts. Both wired into OrderGuard.place_order."

# Metrics
duration: ~1h 45m
completed: 2026-06-16
---

# Phase 02 Plan 03: PDT + T+1 BLOCK + Wash-Sale FLAG + Tenacity GETs Summary

**Wave-3 of Phase 2: completes the OrderGuard BLOCK/FLAG matrix and ships the broker rate-limit backoff layer. Three new check modules (`_pdt.py`, `_t1.py`, `_wash_sale.py`), one new broker resilience module (`_retry.py`), AST-walk gate over AlpacaBroker + OrderGuard for the EXEC-03 / Knight Capital invariant (WARNING #2 fix), and ProposalWriter wiring that stamps `wash_sale_flag` onto every TradeProposal at build time (D-28).**

## Performance

- **Duration:** ~1h 45m (single executor session — no crashes)
- **Started:** 2026-06-16T22:30Z (approx)
- **Completed:** 2026-06-16T00:15Z (approx)
- **Tasks:** 3 plan tasks + 1 follow-up test-isolation fix
- **Files modified:** 11 (4 new sources + 1 alpaca.py extension + 1 checks/__init__.py extension + 1 orderguard.py extension + 1 proposal_writer.py extension + 5 test files turned from Wave-0 stubs into real assertions)
- **Commits:** 4 (07ddb07, 850addd, 7c5f351, 17831e7)
- **Tests added:** 63 (15 + 15 + 18 + 8 + 7 = real assertions across the 5 test files)
- **Full unit suite:** 522 passed, 6 skipped, 4 pre-existing deselect (env-pollution failures from Plan 02-01/02-02), 0 failed
- **Full integration suite:** 37 passed, 9 skipped (cassette-only / manual paths)

## Accomplishments

- **Tenacity retry-decorator layer shipped (EXEC-08).** `src/gekko/brokers/_retry.py` exports `retry_on_rate_limit` (tenacity `retry(...)` factory) parameterized for HTTP 429 with `wait_random_exponential(min=1, max=60)` + `stop_after_attempt(6)` + `retry_if_exception(_is_rate_limit)` + `before_sleep_log` + `reraise=True`. `_is_rate_limit` predicate: primary check `APIError.status_code == 429`; defense-in-depth text-match for `"rate limit"` / `"too many requests"` / `" 429"` substrings.
- **AlpacaBroker GETs decorated.** `get_account`, `get_positions`, `get_quote`, `get_order_by_client_order_id` all carry `@retry_on_rate_limit`. Verified at the AST level (decorator name appears in `decorator_list`) AND at runtime (`hasattr(method, "__wrapped__") is True`).
- **AlpacaBroker.place_order + cancel_order stay zero-decorator.** AST gate (WARNING #2 canonical fix) parses the source tree and asserts `decorator_list == []`. Runtime introspection adds belt-and-braces (`not hasattr(method, "__wrapped__")`).
- **OrderGuard.place_order stays zero-decorator.** Defense in depth — even though Plan 02-02 already shipped this invariant, the AST gate is extended in this plan to also walk the OrderGuard module.
- **PDT BLOCK shipped (EXEC-11).** Two-source detection in `src/gekko/execution/checks/_pdt.py`:
  - **Source 1 (broker primary):** `account.pattern_day_trader == True AND daytrade_count >= 3 AND equity < $25K` → `OrderGuardRejected("pdt_rule")`. String `daytrade_count` coerced via `int(...)`.
  - **Source 2 (local defense in depth):** walks `events.fill` rows over a rolling 5-business-day window via `pandas_market_calendars.get_calendar('NYSE')`. `_count_round_trips` buckets fills by `(date, ticker)` and counts buckets where both `buy` and `sell` were filled. `_would_be_round_trip(req, fills)` checks today's opposite-side same-ticker fills. When count >= 3 AND equity < $25K AND incoming order completes a 4th round-trip → `OrderGuardRejected("pdt_rule_local")`.
  - Equity >= $25K short-circuits both sources (rule doesn't apply).
- **T+1 settlement BLOCK shipped (EXEC-11).** `src/gekko/execution/checks/_t1.py`:
  - Cash account (`shorting_enabled == False`) + BUY side + `qty * ref_price > non_marginable_buying_power` → `OrderGuardRejected("t1_settlement")`.
  - SELL is exempt (no proceeds being spent).
  - Margin account (`shorting_enabled == True`) is exempt (broker advances credit).
  - `ref_price` selection mirrors `check_qty_price_sanity`: LIMIT uses `limit_price`, STOP uses `stop_price`, MARKET fetches via `broker.get_quote(symbol).ask_price` (or `ap` for forward-compat) — and accepts a pre-cached `account["last_quote_ask"]` for tests.
- **Wash-sale FLAG shipped (EXEC-09).** `src/gekko/execution/checks/_wash_sale.py`:
  - `flag_wash_sale(req, user_id) -> dict | None`. NEVER raises (entire body wrapped in `try/except Exception: log + return None`).
  - 30-day calendar lookback over `events.fill` rows for the same ticker. Bounded scan: `ORDER BY id DESC LIMIT 100`.
  - Returned dict shape locked per RESEARCH §5: `{would_be_wash_sale, lookback_event_id, lookback_date, ticker, lookback_qty, lookback_side, note}`.
- **ProposalWriter wires wash_sale_flag (D-28).** `src/gekko/agent/proposal_writer.py::_write_trade` calls `flag_wash_sale(...)` AFTER `client_order_id` is computed but BEFORE the proposal-row INSERT. Result attached via `tp.model_copy(update={"wash_sale_flag": ...})`. Persisted in `Proposal.payload_json` AND in the audit `proposal` event payload (D-15 — full structured rationale).
- **OrderGuard.place_order chain extended.** Order is now: `kill_switch → paper_live_pairing → universe → hard_caps → qty_price_sanity → check_pdt → check_t1_settlement → market_hours → broker.place_order`. The PDT + T+1 checks share ONE `await self._wrapped.get_account()` call (RESEARCH §1 — avoids duplicate broker HTTP traffic; the `@retry_on_rate_limit` decorator on `AlpacaBroker.get_account` provides the 429-handling layer).
- **OrderGuard does NOT block on wash-sale.** Two tripwires verify the EXEC-09 / D-29 invariant:
  - `test_orderguard_does_not_import_flag_wash_sale` AST-walks `orderguard.py` and asserts no `Name` / `ImportFrom` references `flag_wash_sale`.
  - `test_orderguard_place_order_does_not_block_on_wash_sale` runs a `TradeProposal` carrying `wash_sale_flag={"would_be_wash_sale": True, ...}` through `OrderGuard.place_order` and asserts `wrapped.place_order.assert_awaited_once()`.
- **Reject-code vocabulary extended forward-additively.** Plan 02-02 locked 11 entries; Plan 02-03 adds 3 more: `pdt_rule`, `pdt_rule_local`, `t1_settlement`. Total locked = 14. All three new codes route through the existing `cap_rejection` audit-event branch in `execute_proposal` (no executor changes needed — Plan 02-02 wired the branch generically).
- **Test pollution auto-fixed.** Plan 02-01's `tests/unit/test_alpaca_place_order.py` mutates `APIError.status_code` AT THE CLASS LEVEL via `type(api_err).status_code = property(...)`, which permanently rebinds the property for the pytest session. New `_make_api_error` in `test_rate_limit_backoff.py` builds a fresh per-call subclass with its own `status_code` property that shadows the parent. Documented as [Rule 1 - Bug] in deviations.

## Task Commits

| # | Task | Type | Hash | Notes |
|---|------|------|------|-------|
| 1 | tenacity retry_on_rate_limit + AlpacaBroker GETs + AST gate | `feat(02-03-1)` | `07ddb07` | _retry.py + alpaca.py GETs decorated + test_alpaca_retry.py + test_rate_limit_backoff.py |
| 2 | PDT + T+1 BLOCK checks | `feat(02-03-2)` | `850addd` | _pdt.py + _t1.py + _wash_sale.py (FLAG helper for Task 3) + checks/__init__.py + orderguard.py + test_pdt_t1_detection.py |
| 3 | Wash-sale FLAG + ProposalWriter wiring | `feat(02-03-3)` | `7c5f351` | proposal_writer.py extension + test_wash_sale.py + test_wash_sale_flag.py |
| - | APIError test isolation fix | `fix(02-03-1)` | `17831e7` | _make_api_error subclass shadows parent property; out-of-scope fix per Rule 1 |

## Tenacity decorator configuration (LOCKED)

```python
retry_on_rate_limit = retry(
    wait=wait_random_exponential(min=1, max=60),  # exponential 1s–60s + jitter
    stop=stop_after_attempt(6),                    # 6 total = 1 initial + 5 retries
    retry=retry_if_exception(_is_rate_limit),      # only fires on 429
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,                                  # raise underlying APIError, not RetryError wrapper
)
```

Worst-case retry window: ~5 minutes (6 attempts × 60s max wait + jitter). Outside that envelope, the underlying `APIError(429)` surfaces and the caller decides whether to fail closed.

## AST-walk gate (WARNING #2 fix) — implementation locked

Two helper functions in `tests/unit/test_alpaca_retry.py` (both are module-private):

- `_find_class(tree, class_name) -> ast.ClassDef` — walks top-level body
- `_find_method(cls, method_name) -> ast.FunctionDef | ast.AsyncFunctionDef` — walks class body
- `_decorator_source_names(node) -> list[str]` — extracts decorator names from Name / Attribute / Call nodes

Three zero-decorator assertions:

```python
# AlpacaBroker.place_order
src = Path("src/gekko/brokers/alpaca.py").read_text()
tree = ast.parse(src)
cls = _find_class(tree, "AlpacaBroker")
method = _find_method(cls, "place_order")
assert method.decorator_list == []  # EXEC-03 / Knight Capital

# AlpacaBroker.cancel_order — same gate
method = _find_method(cls, "cancel_order")
assert method.decorator_list == []  # RESEARCH §6 Open Question #1

# OrderGuard.place_order — same gate, different file
src = Path("src/gekko/execution/orderguard.py").read_text()
tree = ast.parse(src)
cls = _find_class(tree, "OrderGuard")
method = _find_method(cls, "place_order")
assert method.decorator_list == []  # defense in depth
```

Four positive-control assertions (one per decorated GET) confirm the AST inspection is reading the real tree:

```python
method = _find_method(cls, "get_account")
names = _decorator_source_names(method)
assert "retry_on_rate_limit" in names
```

## PDT detection — two-source field map

| Source | Field(s) | Threshold | Reject code |
|--------|----------|-----------|-------------|
| Broker primary | `account.pattern_day_trader: bool` + `daytrade_count: str` + `equity: str` | `pattern_day_trader==True AND int(daytrade_count) >= 3 AND Decimal(equity) < $25K` | `pdt_rule` |
| Local audit-log | `events.fill` payload `ticker` + `side` + `_event_ts` over rolling 5-business-day window (NYSE calendar) | Bucket-count `(date, ticker)` pairs with both buy AND sell fills `>= 3` AND `equity < $25K` AND incoming order completes a same-day opposite-side fill | `pdt_rule_local` |

## T+1 detection — field map

| Discriminator | Field | Action |
|---------------|-------|--------|
| Cash vs margin | `account.shorting_enabled: bool` (True == margin) | Margin → exempt |
| Side | `req.side` | SELL → exempt |
| Settled cash baseline | `account.non_marginable_buying_power: str` | Decimal coerced |
| Reference price | LIMIT → `req.limit_price`; STOP → `req.stop_price`; MARKET → `broker.get_quote(symbol).ask_price` (or `account.last_quote_ask` cache) | First non-None wins |
| Block trigger | `qty * ref_price > non_marginable_buying_power` | `OrderGuardRejected("t1_settlement")` |

## Wash-sale FLAG dict shape (LOCKED per RESEARCH §5)

```python
{
    "would_be_wash_sale": True,        # bool — always True when flag is returned
    "lookback_event_id": <int>,        # events.id PK of the matched fill
    "lookback_date": "<iso-8601>",     # row.ts of the matched fill
    "ticker": "<UPPERCASE>",           # req.symbol.upper()
    "lookback_qty": "<str>",           # filled_qty as string
    "lookback_side": "buy" | "sell",   # side from the matched fill payload
    "note": "<human-readable>",        # standard IRC §1091 disclaimer
}
```

`flag_wash_sale` returns `None` when no same-ticker fill exists within 30 calendar days. Plan 02-05/02-06 will render the `note` as a warning line in the HITL Block Kit card per D-28.

## Reject-code vocabulary (locked — 14 entries after this plan)

Forward-additive to Plan 02-02's 11-entry list. Plans 02-05 + 02-06 + 02-07 add nothing new.

| Reject code | Check module | Plan | Cause |
|-------------|--------------|------|-------|
| `universe` | `_universe.py` | 02-02 | `req.symbol not in strategy.watchlist` |
| `hard_cap_position_pct` | `_hard_caps.py` | 02-02 | position size > max_position_pct |
| `hard_cap_daily_loss` | `_hard_caps.py` | 02-02 | today's loss >= max_daily_loss_usd |
| `hard_cap_trades_per_day` | `_hard_caps.py` | 02-02 | today's submitted orders >= max_trades_per_day |
| `hard_cap_sector_exposure` | `_hard_caps.py` | 02-02 | sector exposure after order > max_sector_exposure_pct |
| `qty_price_drift` | `_qty_price.py` | 02-02 | abs drift > 2% |
| `ref_price_missing` | `_qty_price.py` | 02-02 | no usable ref_price |
| `paper_live_mismatch_broker` | `_paper_live.py` | 02-02 | broker.is_paper vs strategy.mode disagree |
| `paper_live_mismatch_account` | `_paper_live.py` | 02-02 | account_mode vs strategy.mode disagree |
| `kill_active` | `_kill_switch.py` | 02-02 (read) / 02-05 (write) | users.kill_active=True |
| `market_closed` | `_market_hours.py` | 02-02 | is_market_open()==False |
| **`pdt_rule`** | **`_pdt.py`** | **02-03** | broker.pattern_day_trader=True + daytrade_count>=3 + equity<$25K |
| **`pdt_rule_local`** | **`_pdt.py`** | **02-03** | local 5-day rolling round-trip count >=3 + equity<$25K + would_be_round_trip |
| **`t1_settlement`** | **`_t1.py`** | **02-03** | BUY cost > non_marginable_buying_power on cash account |

## Architectural invariants verified by tests

- `AlpacaBroker.place_order.decorator_list == []` (AST) AND `not hasattr(.., "__wrapped__")` (runtime) — EXEC-03 / Knight Capital
- `AlpacaBroker.cancel_order.decorator_list == []` (AST) AND `not hasattr(.., "__wrapped__")` (runtime) — RESEARCH §6 Open Question #1
- `OrderGuard.place_order.decorator_list == []` (AST) AND `not hasattr(.., "__wrapped__")` (runtime) — defense in depth
- `AlpacaBroker.get_account / get_positions / get_quote / get_order_by_client_order_id` ALL have `decorator_list` containing `retry_on_rate_limit` AND `hasattr(.., "__wrapped__") is True` — EXEC-08
- `gekko.execution.orderguard` module AST walk finds NO import / Name / ImportFrom referencing `tenacity` / `retry_on_rate_limit` / `flag_wash_sale` — defense in depth
- `flag_wash_sale` signature return annotation contains both `dict` and `None` — FLAG-only contract (PATTERNS §4 row 12)
- `flag_wash_sale` does NOT raise on broken session factory / malformed payload_json — tripwire tests

## Verify Commands (Plan 02-03)

All commands ran successfully:

- `uv run pytest tests/unit/test_alpaca_retry.py tests/unit/test_rate_limit_backoff.py -q` → 30/30 pass
- `uv run pytest tests/unit/test_pdt_t1_detection.py -q` → 18/18 pass
- `uv run pytest tests/unit/test_wash_sale.py tests/unit/test_wash_sale_flag.py -q` → 15/15 pass
- `uv run pytest tests/unit/test_orderguard.py tests/unit/test_orderguard_paper_live.py tests/unit/test_proposal_writer.py tests/unit/test_executor.py -q` → 61/61 pass (no regression)
- `uv run pytest tests/unit --deselect <4 pre-existing env-pollution failures> -q` → 522 passed, 6 skipped, 0 failed
- `uv run pytest tests/integration -q` → 37 passed, 9 skipped, 0 failed (Phase-1 walking-skeleton 5-event chain still green; Plan 02-02 OrderGuard 6-check chain + 5 cap_rejection scenarios still green)
- `uv run python -c "import ast; ..."` AST gates → all 3 zero-decorator assertions + 4 positive-control assertions pass
- `uv run python -c "from gekko.execution.checks import check_pdt, check_t1_settlement, flag_wash_sale"` → OK
- `uv run python -c "from gekko.brokers.alpaca import AlpacaBroker; assert hasattr(AlpacaBroker.get_account, '__wrapped__'); assert not hasattr(AlpacaBroker.place_order, '__wrapped__'); assert not hasattr(AlpacaBroker.cancel_order, '__wrapped__')"` → OK
- Grep gate: `claude_agent_sdk` absent from all 4 new modules (`_retry.py`, `_pdt.py`, `_t1.py`, `_wash_sale.py`)

## Issues Encountered

### Pre-existing env-pollution failures (out of scope per execute-plan rules)

4 unit tests fail because pydantic-settings' env_file source reads the repository's `.env` file. Documented in Plan 02-01 + 02-02 SUMMARYs. Deselected via `--deselect` in CI invocations. Suggested follow-up: rename `.env` → `.env.demo`, or override `Settings.model_config = SettingsConfigDict(env_file=None)` via a conftest fixture.

### Test isolation bug auto-fixed (Rule 1)

Plan 02-01's `tests/unit/test_alpaca_place_order.py` mutates `APIError.status_code` at the CLASS LEVEL (`type(api_err).status_code = property(lambda self: 503)`). This rebinds the property on the parent for the rest of the pytest session, so any subsequent `APIError(...).status_code` returns 503 regardless of constructor args.

`tests/unit/test_rate_limit_backoff.py::test_is_rate_limit_fires_on_429_status_code` passed in isolation but failed in the alphabetic full suite. Fix: `_make_api_error` now builds a fresh per-call subclass of `APIError` with its own `status_code` property that shadows the parent. `isinstance(exc, APIError)` still passes so the `_is_rate_limit` predicate's first guard fires normally.

Committed as `fix(02-03-1)` (`17831e7`). Auto-fixing the root cause in Phase-1's test file would have been out of scope.

## Reminders Carried Forward (for downstream plans)

- **Plan 02-05** (kill switch write side) — adds `get_orders_open()` to `AlpacaBroker`. Per Plan 02-03 Task 1's AST gate, this new method MUST also carry `@retry_on_rate_limit` (read endpoint). The `test_alpaca_retry.py::test_alpaca_get_orders_open_*` placeholders should be enabled when 02-05 lands. `cancel_order` STAYS zero-decorator (locked by the AST gate).
- **Plan 02-05** also surfaces `cap_rejection` rejections to Slack via a UI rejection card. The 3 new reject codes from this plan (`pdt_rule`, `pdt_rule_local`, `t1_settlement`) flow through the same vocabulary and should appear in the Slack card rendering.
- **Plan 02-06** (live credentials) — `_build_broker` will extend with the live branch. Live account `get_account` calls also use `@retry_on_rate_limit` (the decorator is method-level, not instance-level, so it applies to live `AlpacaBroker` instances too).
- **Plan 02-07** (walking-skeleton cassette) — will need to record the get_account response shape including `pattern_day_trader`, `daytrade_count`, `equity`, `non_marginable_buying_power`, `shorting_enabled` fields so the PDT + T+1 checks pass on the happy path.

## Threat Flags

None new. The 6 STRIDE threats from the Plan 02-03 `<threat_model>` block (T-02-03-M-01 through T-02-03-O-01 + T-02-03-I-01 + T-02-03-R-01 + T-02-03-M-06) are all mitigated by behaviors verified in this plan's tests:

- T-02-03-M-01 — AlpacaBroker.place_order retry slip → AST gate + runtime introspection
- T-02-03-M-02 — OrderGuard.place_order retry slip → AST gate + AST-walk no-tenacity-import check on orderguard.py
- T-02-03-M-03 — cancel_order retry added in error → AST gate + runtime introspection
- T-02-03-M-04 — PDT undercount → two-source defense (broker + local audit-log)
- T-02-03-M-05 — T+1 violation on cash account → non_marginable_buying_power check + shorting_enabled discriminator
- T-02-03-R-01 — wash-sale repudiation → wash_sale_flag dict persisted in payload_json + audit event
- T-02-03-O-01 — rate-limit thrash → stop_after_attempt(6) bounded envelope (accepted)
- T-02-03-I-01 — wash_sale_flag PII leak → dict shape locked at 7 internal keys; structlog `_redact` processor catches anything else
- T-02-03-M-06 — wash-sale BLOCK creep → tripwire test_wash_sale_never_raises + AST walk on orderguard.py

## Self-Check: PASSED

Verified existence of all created files:
- `src/gekko/brokers/_retry.py` — FOUND
- `src/gekko/execution/checks/_pdt.py` — FOUND
- `src/gekko/execution/checks/_t1.py` — FOUND
- `src/gekko/execution/checks/_wash_sale.py` — FOUND

Verified all commits exist:
- `07ddb07 feat(02-03-1): tenacity retry_on_rate_limit on AlpacaBroker GETs (EXEC-08) + AST gate for place_order zero-decorator` — FOUND
- `850addd feat(02-03-2): PDT + T+1 BLOCK checks (EXEC-11) — Alpaca account fields + local events 5-day rolling` — FOUND
- `7c5f351 feat(02-03-3): wash-sale 30-day same-ticker FLAG (EXEC-09) — ProposalWriter stamps; OrderGuard does NOT block` — FOUND
- `17831e7 fix(02-03-1): isolate _make_api_error from Phase-1 APIError class pollution` — FOUND

---
*Phase: 02-orderguard-real-money-alpaca-live-safety-floor*
*Plan: 03*
*Completed: 2026-06-16*
