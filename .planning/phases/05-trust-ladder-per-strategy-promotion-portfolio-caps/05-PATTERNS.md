# Phase 5: Trust Ladder (Per-Strategy Promotion & Portfolio Caps) - Pattern Map

**Mapped:** 2026-06-26
**Files analyzed:** 14 new/modified
**Analogs found:** 14 / 14 (this is a pure recombination phase — every new file has an in-repo analog)

> Phase 5 invents no new patterns. Each new file mirrors an existing, tested sibling. The risk is creating a *parallel* path that bypasses an existing guard — so every "Analog" below is also the file the new code must structurally plug into, not stand beside.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/gekko/strategy/trust.py` (NEW) | service (state-transition helpers) | CRUD + event-driven (audit) | `src/gekko/strategy/promotion.py` | exact |
| `src/gekko/strategy/streak.py` (NEW) | service (read-only derivation) | batch/transform (audit-log scan) | `_check_daily_loss` in `src/gekko/execution/checks/_hard_caps.py` + `_aggregate_today_events` in `src/gekko/reporter/daily_pnl.py` | role-match |
| `src/gekko/execution/checks/_portfolio_caps.py` (NEW) | middleware (deterministic guard) | request-response (per-order) | `src/gekko/execution/checks/_hard_caps.py` | exact |
| `src/gekko/execution/checks/_capital_ceiling.py` (NEW) | middleware (deterministic guard) | request-response (per-order) | `_check_position_pct` in `src/gekko/execution/checks/_hard_caps.py` | exact |
| `src/gekko/execution/checks/__init__.py` (MODIFY) | config (barrel re-export) | n/a | `src/gekko/execution/checks/__init__.py` (itself) | exact |
| `src/gekko/execution/orderguard.py` (MODIFY) | middleware (pipeline) | request-response | `src/gekko/execution/orderguard.py` (itself, `place_order` pipeline) | exact |
| `src/gekko/anomaly/evaluator.py` (NEW) | service (drawdown evaluator + reflex) | event-driven + batch | `stamp_first_live_trade` (set-once) in `promotion.py` + `_check_daily_loss` scan | role-match |
| `src/gekko/agent/runtime.py` (MODIFY) | controller (orchestrator) | event-driven (auto-branch) | `trigger_strategy_run` write_proposal block (itself, ~line 873) | exact |
| `src/gekko/execution/executor.py` (MODIFY) | service (execute + DM seams) | streaming (fills) + request-response | `_send_slack_dm_respecting_quiet_hours` + `on_fill_event` (itself) | exact |
| `src/gekko/db/models.py` (MODIFY) | model (ORM + vocab) | n/a | `StrategyMetadata` / `User` / `_EVENT_TYPES` (itself) | exact |
| `migrations/versions/0007_p5_trust_ladder.py` (NEW) | migration | n/a | `migrations/versions/0005_p4_cost_ceiling.py` | exact |
| `src/gekko/dashboard/routes.py` (MODIFY) | route (HTTP + HTMX) | request-response | `promote_to_live` + `settings_post` (itself) | exact |
| `src/gekko/reporter/daily_pnl.py` (MODIFY) | service (digest aggregation) | batch/transform | `_aggregate_today_events` + `_build_digest_blocks` (itself) | exact |
| `src/gekko/scheduler/jobs.py` (MODIFY) | config (scheduler registration) | event-driven (tick) | existing daily-P&L cron registration (itself) | role-match |
| `tests/unit/test_trust_safety_invariants.py` (NEW) | test (AST gate) | n/a | `test_orderguard_place_order_ast_zero_decorators` in `tests/unit/test_orderguard.py` | exact |

## Pattern Assignments

### `src/gekko/strategy/trust.py` (service, CRUD + audit) — NEW

**Analog:** `src/gekko/strategy/promotion.py` (mirror exactly: session-factory shim, UPSERT, `append_event`, `finally: engine.dispose()`, NO `claude_agent_sdk` import).

**Imports pattern** (`promotion.py` lines 29-45):
```python
from __future__ import annotations
from datetime import UTC, datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.config import get_settings
from gekko.db.engine import get_async_engine
from gekko.db.models import StrategyMetadata
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.logging_config import get_logger
from gekko.vault.passphrase import get_passphrase as _get_passphrase
```

**Session-factory shim** (`promotion.py` lines 53-61) — copy verbatim into `trust.py`:
```python
def _get_session_factory(user_id: str) -> tuple[AsyncSessionLocal, AsyncEngine | None]:
    settings = get_settings()
    engine = get_async_engine(settings.db_path_for(user_id), _get_passphrase())
    return make_session_factory(engine), engine
```

**Core promote/demote pattern** (`promotion.py` lines 69-124, `promote_strategy_to_live`) — the template for `promote_strategy_to_auto` / `demote_strategy_from_auto` / `set_capital_ceiling`:
```python
async def promote_strategy_to_live(*, user_id: str, strategy_name: str) -> None:
    sf, engine = _get_session_factory(user_id)
    try:
        now_iso = datetime.now(UTC).isoformat()
        async with sf() as session, session.begin():
            existing = await session.get(StrategyMetadata, (user_id, strategy_name))
            if existing is None:
                session.add(StrategyMetadata(user_id=user_id, strategy_name=strategy_name, ...))
            else:
                existing.live_mode_eligible = True
                existing.live_promoted_at = now_iso
            await append_event(
                session, user_id=user_id, strategy_id=None,
                event_type="live_mode_promoted",       # → "trust_promoted" / "trust_demoted" / "capital_scaled"
                payload=normalize_decimals({"strategy_name": strategy_name, "live_promoted_at": now_iso}),
            )
        log.info("strategy.promoted_to_live", user_id=user_id, strategy_name=strategy_name)
    finally:
        if engine is not None:
            await engine.dispose()
```

**Set-once idempotency pattern** (`promotion.py` lines 176-217, `stamp_first_live_trade`) — reuse for the anomaly demote idempotent guard (a strategy already `propose-only` is a no-op): conditional SET on a NULL/current-value check, return early when already in the target state.

**Loader pattern** (`promotion.py` lines 244-262, `load_strategy_metadata`) — extend the existing loader (it already returns the full `StrategyMetadata` row, which after migration 0007 carries the new trust/capital/anomaly columns). The auto-branch and dashboard read trust through this.

**Critical conventions to copy:**
- `append_event(..., strategy_id=None, ...)` — trust events key on `strategy_name` *in the payload*, NOT the snapshot FK (matches `promotion.py` lines 105-116). The streak scanner reads `strategy_name` from the payload.
- NO `claude_agent_sdk` import — enforced by the existing Phase 1/2 grep gate that covers this module family.
- Add new public names to `__all__` (`promotion.py` lines 265-270).

---

### `src/gekko/strategy/streak.py` (service, audit-log scan) — NEW

**Analog:** `_check_daily_loss` in `src/gekko/execution/checks/_hard_caps.py` lines 135-224 (the `select(Event).where(...).limit(1000)` scan + Decimal aggregation loop) combined with `_aggregate_today_events` in `daily_pnl.py` lines 105-196 (the per-event-type branching + payload JSON parse).

**Event-scan pattern** (`_hard_caps.py` lines 163-209) — the house style for walking the append-only log:
```python
rows = (await session.execute(
    select(Event).where(
        Event.user_id == user_id,
        Event.event_type == "fill",        # → scan "approval" / "cap_rejection" / "trust_demoted" / "anomaly_demotion"
        Event.ts >= start_iso,
        Event.ts <= end_iso,
    ).limit(1000)
)).scalars().all()

cumulative = Decimal("0")
for row in rows:
    try:
        outer = json.loads(row.payload_json)
    except (json.JSONDecodeError, TypeError):
        continue
    payload = outer.get("payload", outer)        # canonical-subset wraps under "payload"
    val = payload.get("realized_pnl_usd")        # → read strategy_name / account_mode / reject_code
    ...
```

**Per-event-type branch pattern** (`daily_pnl.py` lines 156-195):
```python
for row in rows:
    outer = json.loads(row.payload_json); payload = outer.get("payload", outer)
    if row.event_type == "fill": ...
    elif row.event_type == "error": ...
    elif row.event_type == "cap_rejection": ...      # → count toward streak-block
```

**Implementation note from RESEARCH (Pattern 4 / Pitfall 1):** the `approval` and `cap_rejection` payloads do NOT currently carry `strategy_name` + `account_mode`. Enrich them at write time (the same "enrich the audit payload" move CR-02 made for fill events) so the scanner partitions by strategy+mode. Walk `id DESC` until the window boundary (most recent `trust_demoted` / `anomaly_demotion` / material-edit reset). Return a `StreakResult` dataclass (`clean_count`, `threshold=10`, `eligible`, `block_reason`, `last_breach_date`, `last_reset_date`) consumed verbatim by UI-SPEC Surface 5.

---

### `src/gekko/execution/checks/_portfolio_caps.py` (middleware, per-order guard) — NEW

**Analog:** `src/gekko/execution/checks/_hard_caps.py` — exact shape (Decimal-exact, `OrderGuardRejected` with unique `reject_code` + `extra` dict, `_ref_price_for` reuse, best-effort sector resolution via `_resolve_sector`, broker GET aggregation).

**Reject-raising pattern** (`_hard_caps.py` lines 110-125, `_check_position_pct`) — the template for each of the four portfolio caps:
```python
proposed_notional = req.qty * ref_price
actual_pct = proposed_notional / equity
cap = strategy.hard_caps.max_position_pct           # → user-level portfolio cap loaded from User row
if actual_pct > cap:
    raise OrderGuardRejected(
        "hard_cap_position_pct",                    # → "portfolio_total_exposure" / "portfolio_sector_concentration" /
                                                    #    "portfolio_correlated_ticker" / "portfolio_daily_loss"
        f"... {actual_pct * Decimal('100'):.4f}% exceeds ... {cap * Decimal('100'):.4f}%",
        extra={"ticker": req.symbol, "actual_pct": str(actual_pct), "cap": str(cap)},
    )
```

**Aggregation pattern** (`_hard_caps.py` lines 318-355, `_check_sector_exposure`) — `get_account()` for equity, `get_positions()` for current market value, sum into a `Decimal`, add proposed notional, compare to cap. Reuse `_resolve_sector` (lines 268-300) and the `>25 positions` perf canary.

**`_ref_price_for` helper** (`_hard_caps.py` lines 60-78) — copy/import for pricing the proposed order (LIMIT→limit_price, STOP→stop_price, MARKET→quote ask).

**Disabled-cap convention:** blank/NULL portfolio-cap column = disabled → `return` early (UI-SPEC). Mirrors the `equity <= 0` early-return guards throughout `_hard_caps.py`.

**Load portfolio caps from `User` row** via the same `_get_session_factory` shim (lines 49-57); the four cap columns land on `users` in migration 0007.

> **Alpaca position-netting clarification (RESEARCH Pitfall 4 / Open Q #4):** a single Alpaca account holds ONE net position per ticker. Portfolio caps aggregate over a single `get_positions()` call; "correlated-strategy / same-ticker overlap" measures the account's single per-ticker position against `max_correlated_ticker_pct`. Do NOT issue N×M broker calls per strategy. Cache sector lookups within one check invocation.

---

### `src/gekko/execution/checks/_capital_ceiling.py` (middleware, per-order guard) — NEW

**Analog:** `_check_position_pct` in `_hard_caps.py` lines 81-125 (same single-cap shape).

**Logic:** caps the strategy's *total deployed capital* (sum of open positions for this strategy's tickers + this order's notional) to the per-strategy `capital_ceiling_usd` (new `StrategyMetadata` column, server_default `"1000.00"` per D-T16). Stacks with `max_position_pct` and portfolio caps. Raise `OrderGuardRejected("capital_ceiling", ..., extra={...})`. Lowering the ceiling is always allowed (de-risk); only increases require confirmation (handled in `trust.py` / route, not here). NULL ceiling read at server_default.

---

### `src/gekko/execution/checks/__init__.py` (config, barrel) — MODIFY

**Analog:** itself (lines 39-59). Add two imports + two `__all__` entries, matching the existing one-module-per-check re-export convention:
```python
from gekko.execution.checks._portfolio_caps import check_portfolio_caps
from gekko.execution.checks._capital_ceiling import check_capital_ceiling
__all__ = (..., "check_portfolio_caps", "check_capital_ceiling")
```

---

### `src/gekko/execution/orderguard.py` (middleware, pipeline) — MODIFY

**Analog:** itself, `place_order` lines 181-241. Insert the two new checks **after** `check_hard_caps` (lines 218-223) and before `check_qty_price_sanity`:
```python
await check_hard_caps(req=req, strategy=self._strategy, broker=self._wrapped, user_id=self._user_id)
# *** NEW Phase 5 — stack portfolio + capital caps on the per-strategy caps ***
await check_portfolio_caps(req=req, strategy=self._strategy, broker=self._wrapped, user_id=self._user_id)
await check_capital_ceiling(req=req, strategy=self._strategy, broker=self._wrapped, user_id=self._user_id)
if self._proposal is not None:
    await check_qty_price_sanity(...)
```

**Hard invariants to preserve** (orderguard.py docstring lines 32-45 + AST gate):
- `place_order` stays **zero-decorator** (Knight-Capital; `test_orderguard_place_order_ast_zero_decorators`).
- NO `claude_agent_sdk` import in this module or any `checks/*.py`.
- Cancellation passthroughs already exist for the anomaly reflex: `cancel_order` (lines 151-164), `get_orders_open` (166-169), `cancel_all_open_orders` (171-175) — the anomaly evaluator reuses these; do not add retry/policy on them.

---

### `src/gekko/anomaly/evaluator.py` (service, drawdown reflex) — NEW

**Analog:** `stamp_first_live_trade` set-once guard in `promotion.py` lines 176-217 (idempotent no-op when already in target state) + `_check_daily_loss` scan in `_hard_caps.py` for realized-P&L derivation.

**Idempotent-demote skeleton** (RESEARCH Pattern 5; mirror set-once guard):
```python
async def evaluate_drawdown(*, user_id, strategy_name, broker) -> bool:
    md = await load_strategy_metadata(user_id=user_id, strategy_name=strategy_name)
    if md is None or md.trust_level != "auto-within-caps":
        return False                                          # idempotent no-op
    dd = await _compute_single_day_drawdown_pct(user_id, strategy_name, broker)
    threshold = Decimal(md.anomaly_threshold_pct or "0.10")   # D-T11 default
    if dd < threshold:
        return False
    await _cancel_pending_auto_orders(user_id, strategy_name, broker)   # OrderGuard.get_orders_open + cancel_order; PENDING→REJECTED
    await demote_strategy_from_auto(user_id=user_id, strategy_name=strategy_name, reason="anomaly", drawdown_pct=dd)
    await _send_anomaly_dm(user_id, strategy_name, dd, threshold)       # category bypasses quiet hours (D-T13)
    return True
```

**Drawdown math** uses `get_positions()` `market_value`/`cost_basis` (already read in `_check_sector_exposure`, `_hard_caps.py` line 354) — Decimal-exact. **Open design choice (RESEARCH Open Q #3):** persist a per-strategy start-of-day snapshot vs live equity denominator — planner must resolve.

**Cancellation:** broker side via `OrderGuard.get_orders_open()` + `cancel_order(broker_order_id)` passthroughs; PENDING auto-proposals → `REJECTED` (no "cancelled-by-anomaly" edge exists; reuse REJECTED + `anomaly_demotion` event — RESEARCH Open Q #5).

---

### `src/gekko/agent/runtime.py` (controller, auto-branch) — MODIFY

**Analog:** itself — the `write_proposal` block in `trigger_strategy_run` at lines 871-908. Insert the auto-branch **after** the `write_proposal` block (after line 900, before the `return` at 910). The trust check is deterministic Python, NOT an LLM call (sits below the SDK boundary).

**Existing structure to insert after** (runtime.py lines 871-883):
```python
async with session_factory() as session, session.begin():
    proposal: TradeProposal | NoActionProposal = await write_proposal(
        session, user_id=user_id, strategy=strategy, strategy_db_id=strategy_db_id, ...)
```

**Auto-branch pattern (RESEARCH Pattern 3):**
```python
trust = await load_trust_level(user_id=user_id, strategy_name=strategy_name, account_mode=proposal.account_mode)
if isinstance(proposal, TradeProposal) and trust == "auto-within-caps":
    # CRITICAL (D-T03): LIVE first trade still requires Phase-2 dual-channel gate.
    # If account_mode == "LIVE" and first_live_trade_confirmed_at IS NULL → route to
    # AWAITING_2ND_CHANNEL HITL, NOT direct execute.
    async with session_factory() as s, s.begin():
        await approve_proposal(s, proposal.proposal_id, actor="auto-execute",
                               extra_payload={"execution_path": "auto"})
        await append_event(s, user_id=user_id, strategy_id=strategy_db_id,
                           event_type="auto_execution", payload=normalize_decimals({...}))
    await execute_proposal(proposal.proposal_id, user_id)   # OrderGuard re-checks as last line
```

**Anti-patterns to honor (RESEARCH):** evaluate trust ONCE at proposal-build time (TOCTOU lesson, BLOCKER #5 — `account_mode` is locked on the proposal row and never re-read). NEVER call `broker.place_order` directly — always route through `execute_proposal` so OrderGuard re-checks caps as its last line (D-T08). Add `outcome="auto_executed"` to the run summary so surfaces render correctly.

---

### `src/gekko/execution/executor.py` (service, DM seams) — MODIFY

**Analog:** itself — `_send_slack_dm_respecting_quiet_hours` (lines 219-290) + `on_fill_event` (line 802) + the `_BYPASS_CATEGORIES` set (lines 249-255).

**Bypass-vs-respect category routing** (executor.py lines 249-255):
```python
_BYPASS_CATEGORIES = frozenset({
    "kill_active", "executor_error", "first_live_fill", "cost_alert",
    # *** NEW Phase 5: add "anomaly_demotion" here (D-T13 bypass quiet hours) ***
})
```
- **Anomaly-demotion DM (D-T13):** add a bypass category (operator-safety-critical, same tier as kill/cap_rejection/first-live).
- **Auto-execution informational DM (D-T18):** use a ROUTINE category (NOT in the bypass set) so it respects quiet hours. Inverting these two is the documented anti-pattern.

**Fill-category precedent** (executor.py lines 967-974) — the existing pattern for choosing a category at the `on_fill_event` DM send site; the post-fill anomaly hook + auto-exec informational DM slot in here. Reuse `_send_slack_dm_respecting_quiet_hours` directly; never call `chat.postMessage`.

---

### `src/gekko/db/models.py` (model, ORM + vocab) — MODIFY

**Analog:** itself — `StrategyMetadata` (lines 272-313), `User` (lines 148-220), `_EVENT_TYPES` (lines 101-122).

**New `StrategyMetadata` columns** (mirror the existing `Mapped[...] = mapped_column(...)` shape, lines 298-306):
```python
trust_level: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'propose-only'"))
trust_promoted_at: Mapped[str | None] = mapped_column(String, nullable=True)
capital_ceiling_usd: Mapped[str | None] = mapped_column(String, nullable=True)   # money-as-TEXT
anomaly_threshold_pct: Mapped[str | None] = mapped_column(String, nullable=True) # fraction-as-TEXT
```

**New `User` columns** (mirror the Phase-4 `daily_cost_ceiling_usd` TEXT pattern, line 212) — the four portfolio caps as TEXT (percent = fraction string "0.50", USD = "200.00").

**`_EVENT_TYPES` extension** (lines 101-122) — append the five new types, exactly as the Phase-4 block appended `llm_cost` / `suspicious_content`:
```python
    "trust_promoted", "trust_demoted", "anomaly_demotion", "capital_scaled", "auto_execution",
```
Add an explanatory comment block above (matching the BL-01 / Phase-3 / Phase-4 comment convention at lines 76-100). Money columns stay TEXT (money-as-TEXT convention).

---

### `migrations/versions/0007_p5_trust_ladder.py` (migration) — NEW

**Analog:** `migrations/versions/0005_p4_cost_ceiling.py` — exact template.

**Revision header** (0005 lines 40-43):
```python
revision: str = "0007_p5_trust_ladder"
down_revision: str | None = "0006_p4_cost_ceiling_repair"   # 0006 is the latest head, NOT 0005
```

**Frozen-local vocabulary** (0005 lines 50-77) — copy the full `_FROZEN_EVENT_TYPES_PRE` tuple (must match the *current* head = 0005's POST), then `_FROZEN_EVENT_TYPES_POST = _PRE + ("trust_promoted","trust_demoted","anomaly_demotion","capital_scaled","auto_execution")`. Vocabularies are duplicated locally (Plan 01-03 convention — migrations are frozen artifacts).

**`add_column` with server_default + CHECK extension** (0005 lines 89-113) — the batch_alter_table pattern (SQLite requires it):
```python
def upgrade() -> None:
    with op.batch_alter_table("strategy_metadata") as bop:
        bop.add_column(sa.Column("trust_level", sa.String(), nullable=False, server_default="propose-only"))
        bop.add_column(sa.Column("trust_promoted_at", sa.String(), nullable=True))
        bop.add_column(sa.Column("capital_ceiling_usd", sa.String(), nullable=True, server_default="1000.00"))
        bop.add_column(sa.Column("anomaly_threshold_pct", sa.String(), nullable=True, server_default="0.10"))
    with op.batch_alter_table("users") as bop:
        bop.add_column(sa.Column("max_total_exposure_pct", sa.String(), nullable=True, server_default="0.50"))
        bop.add_column(sa.Column("max_sector_concentration_pct", sa.String(), nullable=True, server_default="0.30"))
        bop.add_column(sa.Column("max_correlated_ticker_pct", sa.String(), nullable=True, server_default="0.15"))
        bop.add_column(sa.Column("max_total_daily_loss_usd", sa.String(), nullable=True, server_default="200.00"))
    with op.batch_alter_table("events") as bop:
        bop.drop_constraint("ck_event_type", type_="check")
        bop.create_check_constraint("ck_event_type", _in_check("event_type", _FROZEN_EVENT_TYPES_POST))
```
**`downgrade()`** reverses in opposite order (events CHECK first, then drop columns) — 0005 lines 121-134.

> Default cap numbers (50/30/15/$200) are from 05-UI-SPEC. Verify the down_revision chain head before writing (0006 is a repair of 0005 — confirm it is the current head).

---

### `src/gekko/dashboard/routes.py` (route, HTTP + HTMX) — MODIFY

**Analog:** `promote_to_live` (lines 1883-1923) for the typed-confirm promote/capital routes; `settings_post` (line 1120) for the portfolio-cap config form.

**Typed-confirm + server-side guard pattern** (routes.py lines 1883-1913):
```python
@router.post("/strategies/{name}/promote-to-live", response_class=HTMLResponse)
async def promote_to_live(request: Request, name: str,
                          strategy_name_confirm: str = Form(...),
                          user_id: str = Depends(require_session)) -> HTMLResponse:
    from gekko.strategy.promotion import promote_strategy_to_live
    if strategy_name_confirm.strip() != name:
        raise HTTPException(status_code=400, detail=f"Typed strategy name did not match {name!r}. Promotion aborted.")
    await promote_strategy_to_live(user_id=user_id, strategy_name=name)
    return HTMLResponse('<span class="chip-live">LIVE — eligible</span>')
```

**Trust-promote route MUST re-check eligibility server-side (D-T18b / SC-5):** before calling `promote_strategy_to_auto`, call `compute_clean_streak(...)` and return the blocked-explanation partial when `not eligible` — NEVER silent-fail. UI is affordance only (the route is the authority). All routes carry `Depends(require_session)` (router-level dependency, line 135) and filter by `user_id`.

**New routes needed:** `POST /strategies/{name}/promote-to-auto` (typed confirm + streak gate), `POST /strategies/{name}/demote-from-auto` (one-click), `POST /strategies/{name}/capital` (confirm-on-increase), and extend `settings_post` with the four portfolio-cap fields (Decimal validation 0–100% / non-negative USD, mirroring the existing ceiling validation in `settings_post`).

---

### `src/gekko/reporter/daily_pnl.py` (service, digest aggregation) — MODIFY

**Analog:** itself — `_aggregate_today_events` (lines 105-196) and `_build_digest_blocks` (line 234).

**Per-event-type aggregation** (daily_pnl.py lines 156-195) — add an `elif row.event_type == "auto_execution":` branch (count + per-strategy attribution) and an `elif row.event_type == "anomaly_demotion":` branch (summary line). The payload-parse + per-strategy dict accumulation pattern (lines 156-190) is the template. Then surface the new counts in `_build_digest_blocks`. This is the SC-2 review surface for auto-executed decisions.

---

### `src/gekko/scheduler/jobs.py` (config, scheduler) — MODIFY

**Analog:** itself — the existing daily-P&L cron registration. Register an APScheduler **3.x** `IntervalTrigger` (NOT 4.x — RESEARCH corrects CLAUDE.md) for the anomaly-evaluator tick, NYSE-gated via `pandas_market_calendars` (already imported by `daily_pnl.py`). The job persists in the existing `apscheduler_jobs` SQLAlchemyJobStore. Run both post-fill (in `on_fill_event`) AND on this tick (catches unrealized drift) — both call the same `evaluate_drawdown`.

---

### `tests/unit/test_trust_safety_invariants.py` (test, AST gate) — NEW

**Analog:** `test_orderguard_place_order_ast_zero_decorators` in `tests/unit/test_orderguard.py` lines 720-739 (directory-wide `ast.parse` + `ast.walk` gate pattern).

**AST-walk gate skeleton** (test_orderguard.py lines 720-739):
```python
def test_orderguard_place_order_ast_zero_decorators() -> None:
    import ast
    import gekko.execution.orderguard as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "place_order":
            found = True
            assert node.decorator_list == [], f"...{[ast.dump(d) for d in node.decorator_list]!r}"
    assert found, "place_order method not found in OrderGuard source"
```

**Phase-5 invariants to lock (RESEARCH Validation Architecture):**
1. The auto-branch in `runtime.py` is guarded by a trust-level check (AST: no `execute_proposal` call in the auto path that isn't preceded by a `trust == "auto-within-caps"` guard).
2. No module *outside* `strategy/trust.py` assigns `trust_level = "auto-within-caps"` (AST scan across `src/gekko/`).
3. Behavioral: ineligible strategy + forged promote POST → server rejects (D-T18b).
4. Behavioral: LIVE auto strategy with `first_live_trade_confirmed_at IS NULL` → routes to `AWAITING_2ND_CHANNEL`, not direct execute.

Run via `.venv/Scripts/python.exe -m pytest tests/unit/test_trust_*.py -x` (MEMORY: use venv python; full suite hangs at exit, exit 124 ≠ failure).

## Shared Patterns

### Deterministic guard inside the OrderGuard pipeline
**Source:** `src/gekko/execution/orderguard.py` lines 206-241 + `src/gekko/execution/checks/_hard_caps.py`
**Apply to:** `_portfolio_caps.py`, `_capital_ceiling.py`
All caps that must apply to ALL orders (HITL + auto, D-T08) raise `OrderGuardRejected(reject_code, msg, extra={...})` and run sequentially before `broker.place_order`. The LLM cannot reason past them. There must be NO second enforcement path — the auto-branch reaches the broker only via `execute_proposal` → OrderGuard.

### Per-user session-factory shim
**Source:** `src/gekko/strategy/promotion.py` lines 53-61 (identical copy in `_hard_caps.py` lines 49-57)
**Apply to:** `trust.py`, `streak.py`, `_portfolio_caps.py`, `_capital_ceiling.py`, `anomaly/evaluator.py`
```python
def _get_session_factory(user_id: str) -> tuple[AsyncSessionLocal, AsyncEngine | None]:
    settings = get_settings()
    engine = get_async_engine(settings.db_path_for(user_id), _get_passphrase())
    return make_session_factory(engine), engine
```
Always paired with `try: ... finally: if engine is not None: await engine.dispose()`.

### Append-only audit event with dedicated `_EVENT_TYPES` value
**Source:** `src/gekko/strategy/promotion.py` lines 105-116 + `src/gekko/db/models.py` lines 101-122
**Apply to:** `trust.py`, `anomaly/evaluator.py`, `runtime.py` (auto_execution), and migration 0007
Never use `event_type="error"` + a `context` discriminator (BL-01 anti-pattern). New types are first-class: extend `_EVENT_TYPES` in models.py AND the frozen-local tuple in migration 0007. Always `normalize_decimals(payload)`; key trust events by `strategy_name` in the payload with `strategy_id=None`.

### Quiet-hours bypass vs respect DM routing
**Source:** `src/gekko/execution/executor.py` lines 219-290 (`_send_slack_dm_respecting_quiet_hours`, `_BYPASS_CATEGORIES`)
**Apply to:** anomaly-demotion DM (bypass — D-T13), auto-execution informational DM (respect — D-T18)
The category string is the switch. Inverting them is the documented anti-pattern. Never call `chat.postMessage` directly — always go through this seam.

### Decimal-exact money math
**Source:** `_hard_caps.py` throughout + `gekko.audit.canonical.normalize_decimals`
**Apply to:** all new cap math, capital ceiling, drawdown %, portfolio aggregation
`Decimal` everywhere; money/percent stored as TEXT (money-as-TEXT convention; percent = fraction string). Floats in cap math = real-money bug.

### Typed-confirm + server-side authority for state transitions
**Source:** `src/gekko/dashboard/routes.py` lines 1883-1913 (`promote_to_live`) under `Depends(require_session)`
**Apply to:** trust-promote, capital-increase routes
Typed-name confirm guards the destructive action; the server re-verifies eligibility (`compute_clean_streak`) before promoting (D-T18b); UI is affordance only; every query filters by `user_id`.

## No Analog Found

None. Every new file maps to an existing, tested sibling in the codebase. This is a recombination phase.

## Metadata

**Analog search scope:** `src/gekko/strategy/`, `src/gekko/execution/`, `src/gekko/execution/checks/`, `src/gekko/agent/`, `src/gekko/db/`, `src/gekko/dashboard/`, `src/gekko/reporter/`, `src/gekko/scheduler/`, `migrations/versions/`, `tests/unit/`
**Files read directly:** `promotion.py`, `_hard_caps.py`, `orderguard.py`, `models.py`, `0005_p4_cost_ceiling.py`, `runtime.py` (auto-branch region), `executor.py` (DM seam region), `routes.py` (promote/settings region), `daily_pnl.py` (aggregation region), `checks/__init__.py`, `test_orderguard.py` (AST-gate region)
**Pattern extraction date:** 2026-06-26
