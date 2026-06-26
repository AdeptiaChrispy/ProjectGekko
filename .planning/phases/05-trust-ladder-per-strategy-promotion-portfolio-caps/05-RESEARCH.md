# Phase 5: Trust Ladder (Per-Strategy Promotion & Portfolio Caps) - Research

**Researched:** 2026-06-26
**Domain:** Autonomous-execution gating, deterministic portfolio-cap enforcement, anomaly auto-demotion — the highest-stakes (real-money + autonomy) surface in the project
**Confidence:** HIGH (grounded in the actual Phase 1–4 codebase; every integration point read directly)

## Summary

Phase 5 adds a second, orthogonal trust axis (`propose-only` → `auto-within-caps`) on top of the existing paper/live ladder, plus portfolio-level caps, a separate capital-ceiling rung, and anomaly auto-demotion. The CONTEXT.md already locks every product decision (D-T01..D-T18b); this research fills the six implementation gaps flagged as "Claude's Discretion" and grounds each in the existing code that must be extended — **never duplicated**.

The phase is overwhelmingly an exercise in **extending five established patterns**, not inventing new ones: (1) the `StrategyMetadata` + Alembic-migration + `_EVENT_TYPES` CHECK-extension pattern (Phase 2/3/4), (2) the deterministic OrderGuard check pipeline that runs on every order before broker submission (Phase 2), (3) the `strategy/promotion.py` promote/demote helper shape (session-factory shim, audit event, no LLM import), (4) the quiet-hours-respecting vs bypass-category Slack DM split (Phase 3/4), and (5) the AST/grep safety-invariant gate (Phase 2 `place_order` zero-decorator gate, Phase 4 D-05 Haiku gate). There are **no new external packages** — Phase 5 is pure internal feature work on the existing stack.

**Primary recommendation:** Put trust state on new `StrategyMetadata` columns and portfolio caps on new `User` columns via one Alembic migration `0007`; implement the clean-streak counter as a deterministic backward scan of the append-only `events` log; add two new OrderGuard checks (`check_portfolio_caps`, `check_capital_ceiling`) stacking after `check_hard_caps`; branch on trust level in `trigger_strategy_run` after `write_proposal` to route auto strategies through `approve_proposal` → `execute_proposal` (which re-runs OrderGuard as the last line); run the anomaly evaluator both post-fill and on a scheduler tick; and lock the "auto-execute impossible unless criteria met" invariant with an AST gate modeled on the Phase-2 `place_order` zero-decorator gate.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Trust-level state (read/write) | Database/Storage (`StrategyMetadata`) | API (promotion helpers) | Per-(user, strategy) home already exists for the live ladder; trust is the same shape |
| Clean-approval streak computation | API/Backend (deterministic helper over `events`) | Database | Reads the append-only audit log; no LLM, no UI logic |
| Promotion gate decision (eligible?) | API/Backend (`strategy/trust.py`) | Frontend (renders eligibility) | Server is the authority (D-T18b); UI is an affordance only |
| Portfolio caps + capital ceiling enforcement | API/Backend (OrderGuard checks) | — | Deterministic, runs on EVERY order before broker (D-T08); LLM cannot reason past it |
| Auto-execute branch | API/Backend (`agent/runtime.py`) | — | Decides HITL DM vs direct execute after proposal is written |
| Anomaly drawdown evaluation | API/Backend (evaluator) | Scheduler (tick) + Executor (post-fill) | Deterministic Decimal math over positions + start-of-day value |
| Auto-exec informational DM | API/Backend (executor DM seam) | Slack | Reuses quiet-hours-respecting path (D-T18) |
| Anomaly-demotion urgent DM | API/Backend (executor DM seam) | Slack | Reuses bypass-category path (D-T13) |
| Trust badges / promote / demote / scale UI | Frontend Server (Jinja2 + HTMX) | — | Per 05-UI-SPEC.md; extends existing dashboard pages |
| Portfolio-cap config | Frontend Server (Settings form) + DB (`User`) | — | User-level config alongside cost ceiling + quiet hours |

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Promotion gate (TRUST-01 / SC-1, SC-5)**
- **D-T01:** Promotion to `auto-within-caps` requires **10 clean successful HITL approvals** for the strategy (count threshold). Tuned for swing-horizon (~1–3 trades/day) ≈ 1–2 weeks of clean operation.
- **D-T02:** The 10 approvals must be a **clean streak with zero OrderGuard cap-breaches** (`cap_rejection` audit events) in the qualifying window — any cap breach resets/blocks. Ties the gate to observable audit events we already emit.
- **D-T03:** The track record is **per-mode**, and `auto-within-caps` is allowed on **paper AND live independently**. Auto on paper is the natural safe way to validate autonomy. Going live still requires the existing **Phase-2 live promotion + first-live dual-channel gate** — i.e. **live + auto stacks both gates** (the scariest combo inherits every guard).
- **D-T04:** Promote/demote surface = **dashboard + CLI, NO Slack promote command** — matches Phase-2 D-31 (promotion is a deliberate sit-down action, not a phone tap). Demotion is **one-click** and takes effect on the **next decision cycle** (SC-1).
- **D-T05:** **Material edits reset trust.** Editing `watchlist` or `hard_caps` (a new snapshot version per D-05) drops the strategy back to `propose-only` and **restarts the 10-approval streak** — trust is earned per-configuration. Thesis-only / cosmetic edits do NOT reset.
- **D-T18b (SC-5):** Attempting to enable auto on a strategy that hasn't met the criteria is **blocked with a clear explanation** (which criterion failed, how far along the streak is) — never a silent failure.

**Portfolio-level caps (TRUST-02 / SC-2)**
- **D-T06:** Ship **all four** portfolio caps, aggregating across all of this user's strategies: **(1) max total exposure** (% of account equity across all open positions), **(2) max sector concentration** (aggregate sector exposure across strategies), **(3) max correlated-strategy exposure**, **(4) max total daily loss** (USD, portfolio-wide circuit breaker stacking on per-strategy `max_daily_loss_usd`).
- **D-T07:** **Correlated-strategy exposure = same-ticker overlap across strategies** — cap the combined per-ticker exposure when multiple strategies hold the same ticker. Cheap, deterministic, uses positions/watchlists we already have. No correlation engine.
- **D-T08:** Portfolio caps apply to **ALL orders (HITL + auto)**, enforced in the deterministic OrderGuard layer that already runs on every order. No path where manual approval can exceed the portfolio limit.
- **D-T09:** Portfolio caps are **user-level config in dashboard Settings** (single-tenant per D-18), alongside the Phase-4 cost ceiling + Phase-3 quiet-hours. Ship conservative **defaults** (exact numbers = planner/researcher discretion); runtime-editable.

**Capital scaling (TRUST-03 / SC-3)**
- **D-T14:** Capital scaling shape = **arbitrary per-strategy USD capital ceiling**; any **increase requires a fresh confirmation step + audit record**; **lowering is free** (always safe to de-risk). No artificial fixed rungs.
- **D-T15:** Represented as a **new per-strategy absolute USD capital-ceiling field**, enforced in **OrderGuard**: caps the strategy's **total deployed capital** (sum of open positions + this order) to the ceiling, **stacking with `max_position_pct`** and the portfolio caps.
- **D-T16:** First-promotion starting ceiling default = **$1,000**.
- **D-T17:** Capital scaling is a **separate ladder from trust** — scaling up requires its own confirmation + a **capital-limit audit event**, but does **NOT** reset the `propose-only` streak or auto status.

**Anomaly auto-demotion (TRUST-04 / SC-4)**
- **D-T10:** Trigger metric = **single-day drawdown** (today's realized + unrealized loss for the strategy vs its start-of-day value, as a %).
- **D-T11:** Threshold is **configurable per-strategy, default 10% single-day**. It is a **separate, earlier trip than the per-strategy `max_daily_loss_usd` hard cap** — anomaly **removes autonomy** before the hard cap **halts trading**.
- **D-T12:** On fire: **cancel this strategy's pending auto-orders** (open broker orders + PENDING auto-proposals) **+ demote to `propose-only`**. The strategy **keeps running research** — it just needs human approval again. Surgical, not a full halt; does not cascade to other strategies.
- **D-T13:** The anomaly-demotion Slack DM **bypasses quiet hours** — operator-safety-critical, same tier as kill-switch / cap-rejection / first-live (reuse the Phase-3 bypass-category path).

**Auto-execution review surface (SC-2)**
- **D-T18:** Each auto-execution sends a **real-time informational Slack DM (no approve/reject buttons** — ticker/side/size/rationale) **AND** appears in the daily P&L digest. The informational DM **respects quiet hours**.

### Claude's Discretion
- **State representation** — where `trust_level`, capital ceiling, anomaly threshold live (strong candidate: new `StrategyMetadata` columns + Alembic migration).
- **Clean-approval counting mechanism** — how the 10-approval streak + "no cap-breach in window" is computed from the append-only `events` log; where the window resets (on demotion, on material edit per D-T05).
- **Exact default numbers** for the four portfolio caps.
- **Where the auto-execute branch lives** — likely after `write_proposal` in `trigger_strategy_run`.
- **Anomaly evaluation cadence** — post-fill, scheduler tick, or both.
- **New audit `event_type` values** — e.g. `trust_promoted` / `trust_demoted` / `anomaly_demotion` / `capital_scaled` / `auto_execution`.
- **A safety-invariant AST/test gate** — "auto-execute is impossible unless promotion criteria are met."

### Deferred Ideas (OUT OF SCOPE)
- Market-data correlation engine (use same-ticker-overlap per D-T07).
- Email digests (Phase 6; Phase 5 uses the existing Slack daily P&L digest).
- Portfolio-wide anomaly cascade (D-T12 keeps demotion surgical).
- Fixed/multiplier capital rungs (D-T14 uses arbitrary ceiling + confirm-on-increase).
</user_constraints>

<phase_requirements>
## Phase Requirements

REQUIREMENTS.md has no TRUST-* detail; the authoritative source is ROADMAP.md §"Phase 5" success criteria (SC-1..SC-5). The six TRUST-IDs map to those criteria.

| ID | Description (from ROADMAP success criteria) | Research Support |
|----|---------------------------------------------|------------------|
| TRUST-01 | SC-1: View per-strategy trust level; promote via explicit confirm; one-click demote effective next cycle | `StrategyMetadata.trust_level` column; `strategy/trust.py` promote/demote helpers mirroring `promotion.py`; dashboard routes mirroring `promote-to-live`; CLI parity. Streak-counter helper (see RQ-1) feeds eligibility |
| TRUST-02 | SC-2: Auto-within-caps executes without HITL but recorded + surfaced; portfolio caps reject what per-strategy caps allow | Auto-branch in `trigger_strategy_run` (RQ-3); `check_portfolio_caps` OrderGuard check (RQ-2); `auto_execution` event + informational DM + digest line (RQ-4/Surface 4) |
| TRUST-03 | SC-3: Capital scaling is a separate rung; increase needs fresh confirm; new limit in audit | `StrategyMetadata.capital_ceiling_usd` column; `check_capital_ceiling` OrderGuard check; `capital_scaled` event; dedicated `/capital` page (D-T17 independent of trust) |
| TRUST-04 | SC-4: Drawdown > threshold → auto-demote + cancel pending auto-orders + DM | Anomaly evaluator (RQ-5); `anomaly_demotion` event; bypass-category DM (D-T13); reuse `cancel_order` / `cancel_all_open_orders` passthroughs on OrderGuard |
| TRUST-05 | (ROADMAP requirement id; success-criteria coverage spans SC-1..SC-5 — confirm exact mapping with planner) | Covered across the above; **see Open Question #1 — TRUST-05/06 have no distinct success-criteria text in ROADMAP** |
| TRUST-06 | (same — no distinct SC text) | Likely the safety-invariant gate + audit-trail completeness; the AST gate (RQ-6) + new event types address it |
</phase_requirements>

## Standard Stack

No new external packages. Phase 5 is internal feature work on the locked Phase 1–4 stack.

### Core (already installed — versions verified against `pyproject.toml` + installed env)
| Library | Version (pinned) | Purpose | Why Standard |
|---------|------------------|---------|--------------|
| `claude-agent-sdk` | `>=0.2.93,<0.3` (0.2.93 installed) `[VERIFIED: import + pyproject]` | Orchestration (only touched by the auto-branch insertion point, which sits AFTER the LLM boundary) | Phase 4 substrate; the auto-branch decision is deterministic Python, not an LLM call |
| Python | 3.12 `[CITED: CLAUDE.md]` | Runtime | Project standard |
| `alpaca-py` | `>=0.42,<0.50` `[VERIFIED: pyproject]` | `get_positions`, `get_account`, `get_orders_open`, `cancel_order` for portfolio aggregation + anomaly cancellation | Already the broker; the data needed for portfolio caps is already exposed |
| SQLAlchemy 2.x + Alembic | (installed) `[VERIFIED: migrations dir]` | New `StrategyMetadata` + `User` columns; migration `0007` | Phases 2/3/4 each added one migration this way |
| `apscheduler` | `>=3.10,<4` (**3.x, NOT 4.x**) `[VERIFIED: pyproject]` | Anomaly scheduler-tick evaluator (IntervalTrigger) | Existing scheduler (`scheduler/jobs.py`) is APScheduler **3.x** AsyncIOScheduler + SQLAlchemyJobStore |
| `pandas_market_calendars` | (installed) `[VERIFIED: daily_pnl.py import]` | NYSE schedule gate (anomaly tick should skip closed days, mirroring daily digest) | Already used by `daily_pnl.py` |

> **Correction to CLAUDE.md:** the idealized stack table claims "APScheduler 4.x". The actual pin is `>=3.10,<4` and the code uses the 3.x API (`AsyncIOScheduler`, `CronTrigger`, `IntervalTrigger` from `apscheduler.triggers.*`, `SQLAlchemyJobStore`). Plan against **3.x**. `[VERIFIED: pyproject + scheduler/jobs.py]`

### Supporting (internal modules to extend)
| Module | Purpose | What Phase 5 adds |
|--------|---------|-------------------|
| `gekko/strategy/promotion.py` | Live-ladder promote/demote template | NEW sibling `gekko/strategy/trust.py` with `promote_strategy_to_auto` / `demote_strategy_from_auto` / `set_capital_ceiling` / `load_strategy_metadata` (extend existing loader) |
| `gekko/execution/checks/_hard_caps.py` | Per-strategy cap pattern | NEW `_portfolio_caps.py` + `_capital_ceiling.py`, re-exported from `checks/__init__.py` |
| `gekko/execution/orderguard.py` | The 8-check pipeline | Insert `check_portfolio_caps` + `check_capital_ceiling` after `check_hard_caps` |
| `gekko/agent/runtime.py` | `trigger_strategy_run` | Branch on trust level after `write_proposal` |
| `gekko/execution/executor.py` | `execute_proposal`, `on_fill_event`, DM seams | Auto-exec informational DM; post-fill anomaly hook; reuse DM bypass-category routing |
| `gekko/reporter/daily_pnl.py` | Digest aggregation | Fold in `auto_execution` count + `anomaly_demotion` summary line |
| `gekko/dashboard/routes.py` | Promote-to-live route pattern | NEW trust promote/demote/capital routes + extended `/settings` for portfolio caps |
| `gekko/scheduler/jobs.py` | Scheduler registration | Register the anomaly-evaluator IntervalTrigger |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| New `StrategyMetadata` columns | New `trust_metadata` table | Extra table + FK; `StrategyMetadata` is already the per-(user, strategy) home and the live-ladder precedent — reuse it |
| Streak via backward scan of `events` | Materialized counter column updated on each approval | Counter risks drift vs the append-only audit truth; the audit log IS the source of truth (D-14). Scan is correct-by-construction. A counter is a Phase-7 perf optimization if ever needed |
| OrderGuard check insertion | New separate "portfolio guard" wrapper | OrderGuard already runs on every order (D-T08); a second wrapper would create a path that bypasses it. Stack inside the existing pipeline |
| Portfolio caps as `Strategy` schema fields | `User` columns | Portfolio caps are user-level (D-T09); `Strategy` is frozen-shape per-strategy. `User` is the established home for user-level config (cost ceiling, quiet hours) |

**Installation:** none. (`uv sync` already satisfies all dependencies.)

**Version verification performed:**
```
python -c "import claude_agent_sdk; print(claude_agent_sdk.__version__)"  →  0.2.93   [VERIFIED]
pyproject.toml: claude-agent-sdk>=0.2.93,<0.3 ; alpaca-py>=0.42,<0.50 ; apscheduler>=3.10,<4   [VERIFIED]
```

## Package Legitimacy Audit

No external packages are installed by this phase. All dependencies are already present and pinned in `pyproject.toml` from Phases 1–4. The Package Legitimacy Gate is **not applicable** (zero new installs).

**Packages removed due to [SLOP] verdict:** none.
**Packages flagged as suspicious [SUS]:** none.

## Architecture Patterns

### System Architecture Diagram

```
                          ┌─────────────────────────────────────────────┐
   schedule / manual ───► │  trigger_strategy_run (agent/runtime.py)     │
                          │   quiet-hours gate → cost-ceiling gate       │
                          │   → Researcher query() → Decision query()    │
                          │   → write_proposal  ──► PENDING Proposal row │
                          └───────────────┬─────────────────────────────┘
                                          │  *** NEW Phase-5 branch ***
                          ┌───────────────▼─────────────────────────────┐
                          │  load trust_level for (user, strategy, mode) │
                          │  (gekko/strategy/trust.py)                   │
                          └───────┬──────────────────────┬───────────────┘
            propose-only          │                      │  auto-within-caps
            ┌─────────────────────▼──┐         ┌─────────▼───────────────────────┐
            │  post HITL Slack card  │         │ approve_proposal (PENDING→APPROVED)│
            │  (existing path)       │         │  + auto_execution audit event      │
            └────────────────────────┘         │ → execute_proposal(proposal_id)    │
                                                └─────────┬──────────────────────────┘
                                                          │
                          ┌───────────────────────────────▼──────────────────────────┐
                          │ execute_proposal (executor.py) — UNCHANGED last line:      │
                          │  market-hours guard → _build_broker → OrderGuard.place_order│
                          └───────────────────────────────┬──────────────────────────┘
                                                          │  *** OrderGuard pipeline ***
            ┌─────────────────────────────────────────────▼────────────────────────────┐
            │ kill → paper_live → universe → check_hard_caps                              │
            │   → ** check_portfolio_caps ** → ** check_capital_ceiling **   (NEW)        │
            │   → qty_price → pdt → t1 → market_hours → broker.place_order                │
            │ any failure → OrderGuardRejected → cap_rejection audit event (resets streak)│
            └─────────────────────────────────────────────┬────────────────────────────┘
                                                          │ fill stream
                          ┌───────────────────────────────▼──────────────────────────┐
                          │ on_fill_event (executor.py)                                │
                          │  fill event → FILLED → first-live stamp                     │
                          │  → ** auto-exec informational DM (D-T18, quiet-hours OK) ** │
                          │  → ** anomaly evaluator (post-fill) **    (NEW)             │
                          └───────────────────────────────┬──────────────────────────┘
                                                          │  drawdown ≥ threshold?
                          ┌───────────────────────────────▼──────────────────────────┐
                          │ anomaly_demote (NEW):                                       │
                          │  demote_strategy_from_auto + cancel pending auto-orders     │
                          │  + anomaly_demotion event + ** urgent DM (bypass QH) **     │
                          └────────────────────────────────────────────────────────────┘

  Parallel: scheduler IntervalTrigger tick (NYSE-gated) → anomaly evaluator (same code path)
  Streak counter: backward scan of append-only `events` (approval / cap_rejection / trust_demoted / material-edit boundary)
```

### Recommended Project Structure (new files only)
```
src/gekko/
├── strategy/
│   └── trust.py                     # promote/demote/scale helpers + load_strategy_metadata extension
│                                    #   (mirror promotion.py: session-factory shim, audit events, NO claude_agent_sdk import)
├── strategy/
│   └── streak.py                    # compute_clean_streak(user_id, strategy_name, mode) → StreakResult
│                                    #   deterministic scan of events; the eligibility authority
├── execution/checks/
│   ├── _portfolio_caps.py           # check_portfolio_caps (4 aggregate caps across all strategies)
│   └── _capital_ceiling.py          # check_capital_ceiling (per-strategy total-deployed-USD cap)
├── anomaly/
│   └── evaluator.py                 # evaluate_drawdown(user_id, strategy_name) → maybe demote+cancel+DM
migrations/versions/
└── 0007_p5_trust_ladder.py          # StrategyMetadata + User columns + _EVENT_TYPES CHECK extension
src/gekko/dashboard/templates/       # per 05-UI-SPEC: trust badges, promote/blocked/capital modals, portfolio-caps fieldset
tests/unit/
└── test_trust_safety_invariants.py  # AST gate: auto-execute impossible unless criteria met
```

### Pattern 1: Deterministic guard stacks inside the OrderGuard pipeline (D-T08)
**What:** New portfolio + capital checks are added to `OrderGuard.place_order` AFTER `check_hard_caps`, raising `OrderGuardRejected` on breach — identical shape to the existing four hard caps.
**When to use:** Any cap that must apply to ALL orders (HITL + auto) and that the LLM must never reason past.
**Example:**
```python
# Source: existing src/gekko/execution/orderguard.py:218-223 (insertion point)
        await check_hard_caps(
            req=req, strategy=self._strategy, broker=self._wrapped, user_id=self._user_id,
        )
        # *** NEW Phase 5 — stack portfolio + capital caps on the per-strategy caps ***
        await check_portfolio_caps(
            req=req, strategy=self._strategy, broker=self._wrapped, user_id=self._user_id,
        )
        await check_capital_ceiling(
            req=req, strategy=self._strategy, broker=self._wrapped, user_id=self._user_id,
        )
        if self._proposal is not None:
            await check_qty_price_sanity(...)
```
> **Decision needed (planner):** the OrderGuard reject ordering is "deterministic for tests" (hard-caps short-circuits position→daily_loss→trades→sector). Place portfolio caps AFTER hard caps so a per-strategy breach still reports first; place capital ceiling after portfolio (most-specific-last is a free choice — document whichever you pick). Each new check raises `OrderGuardRejected` with a unique `reject_code` (e.g. `portfolio_total_exposure`, `portfolio_sector_concentration`, `portfolio_correlated_ticker`, `portfolio_daily_loss`, `capital_ceiling`).

### Pattern 2: Promote/demote helper mirrors `strategy/promotion.py` exactly
**What:** A new `gekko/strategy/trust.py` with the same skeleton as `promotion.py`: module-local `_get_session_factory` shim, `async with sf() as session, session.begin():`, UPSERT the `StrategyMetadata` row, `append_event(...)`, `finally: engine.dispose()`, NO `claude_agent_sdk` import.
**Example:**
```python
# Source: pattern from src/gekko/strategy/promotion.py:69-124 (promote_strategy_to_live)
async def promote_strategy_to_auto(*, user_id: str, strategy_name: str) -> None:
    sf, engine = _get_session_factory(user_id)
    try:
        now_iso = datetime.now(UTC).isoformat()
        async with sf() as session, session.begin():
            md = await session.get(StrategyMetadata, (user_id, strategy_name))
            # ... set md.trust_level = "auto-within-caps", md.trust_promoted_at = now_iso ...
            await append_event(session, user_id=user_id, strategy_id=None,
                               event_type="trust_promoted",
                               payload=normalize_decimals({"strategy_name": strategy_name, ...}))
    finally:
        if engine is not None:
            await engine.dispose()
```
> Note: `append_event` calls in `promotion.py` pass `strategy_id=None` (these are keyed by `strategy_name` in the payload, not the snapshot FK). Follow that convention for trust events too — the streak scanner keys on `strategy_name` in the payload.

### Pattern 3: Auto-branch after `write_proposal` (RQ-3)
**What:** After `write_proposal` returns the PENDING proposal in `trigger_strategy_run`, load the strategy's trust level for the proposal's mode. If `auto-within-caps`: call `approve_proposal` (PENDING→APPROVED + `approval` event) then `execute_proposal(proposal_id, user_id)`. If `propose-only`: existing HITL path (post Slack card — done by the calling surface, not `trigger_strategy_run` itself; see Open Question #2).
**Why `execute_proposal` is the correct re-check point:** `execute_proposal` (executor.py:426) ALWAYS constructs the broker via `_build_broker` → `OrderGuard`, and `OrderGuard.place_order` runs the full check pipeline as its last line before `broker.place_order`. **Confirmed by reading the code:** there is no path in `execute_proposal` that reaches the broker without going through OrderGuard. The auto path therefore inherits portfolio caps + capital ceiling for free (D-T08 satisfied structurally).
```python
# Insertion: src/gekko/agent/runtime.py after the write_proposal block (~line 883)
# trust check is deterministic Python — NOT an LLM call. Sits below the SDK boundary.
trust = await load_trust_level(user_id=user_id, strategy_name=strategy_name,
                               account_mode=proposal.account_mode)   # only TradeProposal has account_mode
if isinstance(proposal, TradeProposal) and trust == "auto-within-caps":
    async with session_factory() as s, s.begin():
        await approve_proposal(s, proposal.proposal_id, actor="auto-execute",
                               extra_payload={"execution_path": "auto"})
        await append_event(s, user_id=user_id, strategy_id=strategy_db_id,
                           event_type="auto_execution",
                           payload=normalize_decimals({"proposal_id": proposal.proposal_id, ...}))
    await execute_proposal(proposal.proposal_id, user_id)   # OrderGuard re-checks as last line
```
> **Critical subtlety (live + auto, D-T03):** for a LIVE strategy, the FIRST live trade still requires the Phase-2 dual-channel gate. The auto-branch MUST NOT skip that gate — check `StrategyMetadata.first_live_trade_confirmed_at`. If a LIVE auto strategy has `first_live_trade_confirmed_at IS NULL`, the auto path must route to `AWAITING_2ND_CHANNEL` (HITL dual-channel), NOT direct execute. This is the "live + auto stacks both gates" requirement. **The planner must add an explicit task + test for this interaction.**

### Pattern 4: Clean-streak computation by backward scan of `events` (RQ-1)
**What:** `compute_clean_streak(user_id, strategy_name, mode)` reads the append-only `events` table for this user, ordered by `id DESC`, and walks until it finds the window boundary. It counts `approval` events for this strategy/mode; a `cap_rejection` for this strategy inside the window blocks/zeroes the streak; the window boundary is the most recent `trust_demoted` OR `anomaly_demotion` OR material-edit reset for this strategy.
**Mechanics (grounded in the audit schema):**
- `approval` events are written by `approve_proposal` (proposals.py:205) with payload `{proposal_id, actor, slack_action_id}`. **Gap:** the approval payload does NOT currently carry `strategy_name` or `account_mode`. The streak scanner needs to attribute each approval to a strategy + mode. **Two options (planner choice):**
  - (a) Join through the `Proposal` row: the `approval` event carries `proposal_id`; look up `Proposal.strategy_id` → `Strategy.strategy_name` and `Proposal.account_mode`. Robust but N+1 reads.
  - (b) **Recommended:** extend `approve_proposal`'s `extra_payload` to include `strategy_name` + `account_mode` at write time (the auto-branch and HITL approve handlers both have these in scope). Then the scanner reads them directly from the event payload. Cleaner, self-contained, and it's the same "enrich the audit payload" move CR-02 made for fill events (executor.py:866).
- `cap_rejection` events (executor.py:601/666) carry `ticker` + `reject_code` but **also lack `strategy_name`** in the canonical payload — though `exc.extra` from the hard-cap checks does include context. The cap-rejection→streak link needs `strategy_name`; add it to the cap_rejection payload the same way (the executor has `tp.strategy_name` in scope at both raise sites).
- **Window reset on material edit (D-T05):** a material edit creates a new `Strategy` snapshot version (schema D-05). The reset boundary can be detected by comparing the latest `Strategy.version`/`created_at` against the `trust_promoted_at` or the streak window start. **Recommended:** when the strategy save route detects a material edit (watchlist or hard_caps changed), it calls `demote_strategy_from_auto` (if auto) AND writes a `trust_demoted` event with `reason="material_edit"` — making the reset a first-class boundary the scanner already understands. This avoids the scanner needing to diff snapshot payloads.
**Return shape:**
```python
@dataclass
class StreakResult:
    clean_count: int            # approvals since the last boundary, this strategy+mode
    threshold: int = 10         # D-T01
    eligible: bool              # clean_count >= threshold and not blocked
    block_reason: str | None    # "insufficient_streak" | "cap_breach" | "material_edit_reset" (drives Surface 5 copy)
    last_breach_date: str | None
    last_reset_date: str | None
```
> The 05-UI-SPEC Surface 5 (blocked-promotion modal) consumes `clean_count`, `block_reason`, `breach_date`, `edit_date` verbatim — `StreakResult` is designed to feed it directly.

### Pattern 5: Anomaly evaluator — Decimal-exact single-day drawdown (RQ-5)
**What:** `evaluate_drawdown(user_id, strategy_name)` computes today's drawdown % for the strategy = `(start_of_day_value - current_value) / start_of_day_value` where value = realized P&L today + current unrealized (mark-to-market of open positions for this strategy's tickers). If `drawdown_pct >= md.anomaly_threshold_pct` AND `md.trust_level == "auto-within-caps"`: demote + cancel pending auto-orders + `anomaly_demotion` event + bypass-category DM.
**Start-of-day value:** the cleanest deterministic source is the broker account equity at first evaluation of the day, OR — for a per-strategy figure — sum of position cost-basis + realized P&L. Given the swing-horizon scope and that `get_positions()` returns `market_value` and `cost_basis` per position (used already in `_check_sector_exposure`, _hard_caps.py:354), a pragmatic per-strategy start-of-day value = Σ(cost_basis of this strategy's tickers held at open) and current value = Σ(market_value). **Recommendation:** persist a per-strategy start-of-day snapshot (one row/event at market open via the scheduler) so the denominator is stable across the day; reading live equity gives a moving denominator. **This is the single biggest design choice in the anomaly feature — flag for discuss-phase / planner.** See Open Question #3.
**Cadence (D-T10 discretion):** run **both** — (a) post-fill in `on_fill_event` (catches a sudden loss the instant it realizes) and (b) on a scheduler IntervalTrigger (catches unrealized drawdown on open positions when no fill is happening). Both call the same `evaluate_drawdown`. The evaluator is idempotent (a strategy already `propose-only` is a no-op — mirror `stamp_first_live_trade`'s set-once guard).
**Cancellation (D-T12):** cancel open broker orders for this strategy via `OrderGuard.get_orders_open()` + `cancel_order(broker_order_id)` (both pass-through on OrderGuard, orderguard.py:151-175), and transition this strategy's PENDING auto-proposals to a terminal state (REJECTED, with reason `anomaly_demotion` — note: there is currently no `PENDING→` "cancelled-by-anomaly" edge; `PENDING→REJECTED` exists. **Planner: decide whether to reuse REJECTED or add a state.** Reusing REJECTED + a distinguishing audit event is the lower-risk choice).

### Anti-Patterns to Avoid
- **Computing the streak from a counter column instead of the audit log.** The append-only `events` log is the source of truth (D-14). A counter drifts; the scan cannot lie. (Perf is a non-issue at swing-horizon volume — the existing `_check_daily_loss` already scans up to 1000 event rows on every order.)
- **Re-deriving trust level at execute-time from current strategy state.** Mirror the BLOCKER #5 TOCTOU lesson (executor.py:120): the `account_mode` is locked on the proposal row at build time, never re-read. Trust level should be evaluated ONCE at the auto-branch (proposal-build time); demotion taking effect "next decision cycle" (SC-1) is correct because the next cycle re-reads trust.
- **Adding a path that reaches the broker without OrderGuard.** D-T08 forbids it. The auto-branch must go through `execute_proposal` (which builds OrderGuard), never call `broker.place_order` directly. The Phase-2 grep gate already asserts `place_order` is undecorated; add a Phase-5 AST gate asserting the auto-branch routes through `execute_proposal` (RQ-6).
- **Letting the anomaly DM respect quiet hours, or the auto-exec DM bypass them.** They are inverted: anomaly = bypass (D-T13), auto-exec FYI = respect (D-T18). The category strings are the switch (executor.py:249 `_BYPASS_CATEGORIES`).
- **Skipping the live+auto dual-channel gate.** See Pattern 3 critical subtlety.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Cap enforcement on auto orders | A separate "auto-order validator" | The existing OrderGuard pipeline via `execute_proposal` | One enforcement point; D-T08 ("portfolio is portfolio") demands no second path |
| Promote/demote persistence | New table + bespoke CRUD | `StrategyMetadata` columns + `trust.py` mirroring `promotion.py` | The live ladder already solved this exact shape |
| Audit event for trust/anomaly/capital | `event_type="error"` + a `context` discriminator | New `_EVENT_TYPES` values via Alembic CHECK extension | BL-01 (Phase 2) explicitly fixed the error-bucket-pollution anti-pattern; new types are the established move |
| Quiet-hours-aware vs urgent DM | New Slack-send code | `_send_slack_dm_respecting_quiet_hours` + category routing | The bypass-set + routine-set split is built and tested |
| Daily-loss / streak event scanning | New SQL aggregation engine | The `select(Event).where(...)` loop pattern from `_hard_caps.py` / `daily_pnl.py` | Same shape, Decimal-exact, already the house style |
| Decimal money math | float arithmetic | `Decimal` + `normalize_decimals` everywhere | Project-wide invariant; floats in cap math = real-money bug |
| Scheduler tick | A custom asyncio loop | APScheduler 3.x `IntervalTrigger` registered in `scheduler/jobs.py` | The job store persists across restarts; an ad-hoc loop doesn't |

**Key insight:** Phase 5 is a *recombination* phase. Almost every primitive it needs already exists and is tested; the risk is creating a parallel path that bypasses an existing guard. Every new capability should plug into an existing seam (OrderGuard pipeline, DM category router, audit-event vocabulary, `StrategyMetadata`, scheduler job store), not stand beside it.

## Runtime State Inventory

> Phase 5 is **not** a rename/refactor phase — it is greenfield feature work that adds columns, checks, and routes. A full Runtime State Inventory is not applicable. However, two state-migration concerns arise from the new schema:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | Existing `strategy_metadata` rows (from Phase 2 live ladder) need the new trust/capital/anomaly columns backfilled with safe defaults (`trust_level='propose-only'`, `capital_ceiling_usd=NULL→$1000 at read or server_default`, `anomaly_threshold_pct=NULL→10% at read`) | Alembic `0007` `add_column` with `server_default` (mirror 0005's `daily_cost_ceiling_usd server_default='5.00'`) |
| Stored data | Existing `users` rows need the four portfolio-cap columns backfilled with conservative defaults | Alembic `0007` `add_column` with `server_default` per the defaults table below |
| Live service config | None — no external service stores trust state | None — verified: trust/caps live entirely in the per-user SQLCipher DB |
| OS-registered state | The anomaly scheduler tick is a NEW APScheduler job; on first `serve` after deploy it registers into the existing `apscheduler_jobs` table. No OS-level registration | Register in `scheduler/jobs.py` lifespan, same as the daily-P&L cron |
| Secrets/env vars | None — no new secrets | None |
| Build artifacts | None | None |

## Common Pitfalls

### Pitfall 1: Streak counts approvals that can't be attributed to a strategy/mode
**What goes wrong:** The `approval` audit event payload lacks `strategy_name` and `account_mode`, so a naive scanner counts every approval globally and over-counts for multi-strategy users, or under-counts after a per-mode split.
**Why it happens:** Phase 1's `approve_proposal` only recorded `{proposal_id, actor, slack_action_id}`.
**How to avoid:** Enrich the `approval` (and `cap_rejection`) event payloads with `strategy_name` + `account_mode` at write time (Pattern 4 option b), exactly as CR-02 enriched fill events. Add a Wave-0 test that the scanner correctly partitions by strategy+mode.
**Warning signs:** A strategy with 10 total HITL approvals across two strategies shows eligible when only 6 were for THIS strategy.

### Pitfall 2: Auto-execute silently bypasses the live first-trade dual-channel gate
**What goes wrong:** A LIVE strategy promoted to auto places its first real-money order with zero human confirmation, defeating the Phase-2 HITL-06 gate.
**Why it happens:** The auto-branch routes straight to `execute_proposal`, skipping the `AWAITING_2ND_CHANNEL` diversion the Slack approve handler performs.
**How to avoid:** In the auto-branch, if `account_mode == "LIVE"` and `first_live_trade_confirmed_at IS NULL`, route to the dual-channel HITL path instead of direct execute (Pattern 3 subtlety). Lock with a test asserting "first live auto trade requires dual-channel."
**Warning signs:** A live strategy's first auto fill has no `first_live_trade_confirmed` event preceding it.

### Pitfall 3: Anomaly drawdown denominator drifts (moving start-of-day value)
**What goes wrong:** If "start-of-day value" is read live each evaluation, the denominator changes through the day and the % is wrong/unstable; a strategy could oscillate around the threshold.
**Why it happens:** No persisted open-of-day snapshot.
**How to avoid:** Snapshot per-strategy start-of-day value once at market open (scheduler job writes an event/row), then read it for all intraday evaluations. See Open Question #3.
**Warning signs:** Drawdown % jumps around between two ticks with no fills.

### Pitfall 4: Portfolio sector/correlated-ticker aggregation issues N×M broker calls
**What goes wrong:** Aggregating sector exposure across all strategies, resolving each position's sector via `get_asset`, on every order, blows up latency (the existing `_check_sector_exposure` already warns at >25 positions, _hard_caps.py:333).
**Why it happens:** Per-position `get_asset` loop, now multiplied by cross-strategy aggregation.
**How to avoid:** Portfolio caps aggregate over the **single** `get_positions()` call (positions are account-wide, not per-strategy, in Alpaca — the account holds the net position). For same-ticker-overlap (D-T07), the "across strategies" dimension is about **which strategies declare the ticker in their watchlist**, not separate broker positions — Alpaca nets positions per account. **Important architectural clarification for the planner:** a single Alpaca account holds ONE position per ticker regardless of how many strategies "own" it. "Combined per-ticker exposure" (D-T07) is therefore the account's single position in that ticker measured against the cap — the cross-strategy framing is about attribution, not separate holdings. Confirm this interpretation in discuss-phase (Open Question #4). Cache sector lookups within one check invocation; do not re-resolve per strategy.
**Warning signs:** Approve-to-fill latency climbs sharply once a second auto strategy is active.

### Pitfall 5: New `OrderGuardRejected` reject_codes break the streak in surprising ways
**What goes wrong:** A portfolio-cap rejection writes a `cap_rejection` event (good — D-T02 wants cap breaches to reset the streak), but the operator's mental model is "my per-strategy caps were fine." The blocked-promotion explanation must distinguish per-strategy vs portfolio breaches.
**How to avoid:** Carry `reject_code` into the `cap_rejection` payload (already done, executor.py:593) and let the streak scanner + Surface 5 copy surface which cap tripped.
**Warning signs:** Operators confused why a strategy's streak reset when its own caps were never hit.

## Code Examples

### New OrderGuard check (portfolio total exposure) — mirrors `_check_position_pct`
```python
# Source pattern: src/gekko/execution/checks/_hard_caps.py:81-125
async def _check_total_exposure(*, req, broker, user_id) -> None:
    account = await broker.get_account()
    equity = Decimal(str(account.get("equity") or account.get("portfolio_value") or "0"))
    if equity <= Decimal("0"):
        return
    caps = await _load_portfolio_caps(user_id)          # reads User row
    if caps.max_total_exposure_pct is None:
        return                                          # blank = disabled (UI-SPEC)
    positions = await broker.get_positions()
    total_mv = sum((Decimal(str(p.get("market_value") or "0")) for p in positions), Decimal("0"))
    ref_price = _ref_price_for(req, await _maybe_quote(broker, req))
    proposed = req.qty * ref_price if ref_price > 0 else Decimal("0")
    pct_after = (total_mv + proposed) / equity
    if pct_after > caps.max_total_exposure_pct:
        raise OrderGuardRejected(
            "portfolio_total_exposure",
            f"total exposure after order {pct_after*100:.4f}% exceeds cap "
            f"{caps.max_total_exposure_pct*100:.4f}%",
            extra={"ticker": req.symbol, "pct_after": str(pct_after),
                   "cap": str(caps.max_total_exposure_pct)},
        )
```

### Alembic migration skeleton (mirror 0005)
```python
# Source pattern: migrations/versions/0005_p4_cost_ceiling.py
def upgrade() -> None:
    with op.batch_alter_table("strategy_metadata") as bop:
        bop.add_column(sa.Column("trust_level", sa.String(), nullable=False,
                                 server_default="propose-only"))
        bop.add_column(sa.Column("trust_promoted_at", sa.String(), nullable=True))
        bop.add_column(sa.Column("capital_ceiling_usd", sa.String(), nullable=True,
                                 server_default="1000.00"))            # D-T16
        bop.add_column(sa.Column("anomaly_threshold_pct", sa.String(), nullable=True,
                                 server_default="0.10"))               # D-T11
        # OPTIONAL: a trust_level CHECK constraint ('propose-only','auto-within-caps')
    with op.batch_alter_table("users") as bop:
        bop.add_column(sa.Column("max_total_exposure_pct", sa.String(), nullable=True,
                                 server_default="0.50"))
        bop.add_column(sa.Column("max_sector_concentration_pct", sa.String(), nullable=True,
                                 server_default="0.30"))
        bop.add_column(sa.Column("max_correlated_ticker_pct", sa.String(), nullable=True,
                                 server_default="0.15"))
        bop.add_column(sa.Column("max_total_daily_loss_usd", sa.String(), nullable=True,
                                 server_default="200.00"))
    with op.batch_alter_table("events") as bop:
        bop.drop_constraint("ck_event_type", type_="check")
        bop.create_check_constraint("ck_event_type",
            _in_check("event_type", _FROZEN_EVENT_TYPES_POST))   # PRE + new 5 types
```
> Money columns are TEXT (project money-as-TEXT convention; `daily_cost_ceiling_usd` is TEXT, models.py:212). Percentages stored as TEXT Decimal fractions ("0.50" = 50%), matching `HardCaps.max_position_pct` which is a fraction.

### New event types for `_EVENT_TYPES` (models.py + migration 0007)
```python
# Append to _EVENT_TYPES in src/gekko/db/models.py and to _FROZEN_EVENT_TYPES_POST in 0007
    "trust_promoted",     # promote to auto-within-caps
    "trust_demoted",      # demote (operator one-click OR material-edit reset, with reason)
    "anomaly_demotion",   # drawdown-triggered auto-demotion
    "capital_scaled",     # capital ceiling changed (old→new in payload)
    "auto_execution",     # an auto-within-caps proposal was auto-approved+executed
```

### Anomaly evaluator idempotent demote (mirror set-once guard)
```python
# Source pattern: src/gekko/strategy/promotion.py:176-217 (stamp_first_live_trade set-once)
async def evaluate_drawdown(*, user_id, strategy_name, broker) -> bool:
    md = await load_strategy_metadata(user_id=user_id, strategy_name=strategy_name)
    if md is None or md.trust_level != "auto-within-caps":
        return False                                    # idempotent: nothing to demote
    dd = await _compute_single_day_drawdown_pct(user_id, strategy_name, broker)
    threshold = Decimal(md.anomaly_threshold_pct or "0.10")
    if dd < threshold:
        return False
    await _cancel_pending_auto_orders(user_id, strategy_name, broker)   # broker + PENDING proposals
    await demote_strategy_from_auto(user_id=user_id, strategy_name=strategy_name,
                                    reason="anomaly", drawdown_pct=dd)   # writes anomaly_demotion event
    await _send_anomaly_dm(user_id, strategy_name, dd, threshold)       # category bypasses quiet hours
    return True
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Audit events shoehorned as `event_type="error"` + `context` | Dedicated `_EVENT_TYPES` values via Alembic CHECK extension | Phase 2 (BL-01) | New trust/anomaly/capital events MUST be first-class types, not error-bucket |
| `claude-code-sdk` | `claude-agent-sdk` (0.2.93) | Pre-Phase-1 | Already correct; no action |
| `ib_insync` / `alpaca-trade-api` | `ib_async` / `alpaca-py` | Pre-project | N/A this phase (Alpaca only) |
| Re-deriving execute-time state from live strategy | State locked on proposal row at build time (account_mode) | Phase 2 (BLOCKER #5) | Trust level should also be evaluated once at proposal/auto-branch time, not re-read at execute |

**Deprecated/outdated:**
- CLAUDE.md's "APScheduler 4.x" claim — the project actually uses **3.x** (`>=3.10,<4`). Plan against 3.x APIs.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Portfolio caps belong on `User` columns (not a new table) | Stack / Alternatives | Low — D-T09 says user-level config; matches cost-ceiling precedent |
| A2 | A single Alpaca account nets one position per ticker, so "same-ticker overlap across strategies" measures the account's single position, not summed separate holdings | Pitfall 4 / Open Q #4 | **Medium-High** — if wrong, the correlated-ticker cap math is wrong. Confirm in discuss-phase |
| A3 | Reusing `PENDING→REJECTED` (with `anomaly_demotion` audit event) for cancelled auto-proposals is acceptable vs adding a new state | Pattern 5 | Low-Medium — semantics slightly off (REJECTED implies human reject); planner decides |
| A4 | Per-strategy start-of-day value should be a persisted open snapshot, not live equity | Pitfall 3 / Open Q #3 | **High** — wrong choice makes the anomaly trigger noisy or wrong; flag for discuss-phase |
| A5 | TRUST-05 / TRUST-06 have no distinct ROADMAP success-criteria text and map to the gate + audit-completeness | phase_requirements / Open Q #1 | Medium — planner must confirm exact requirement intent |
| A6 | Default portfolio-cap numbers (50/30/15/$200) from 05-UI-SPEC are the right conservative defaults | Validation / migration | Low — UI-SPEC already prescribes these; runtime-editable |
| A7 | The streak should partition per-mode (paper vs live separately) per D-T03 | Pattern 4 | Low — explicitly locked in D-T03 |

## Open Questions (RESOLVED)

1. **TRUST-05 and TRUST-06 requirement intent.**
   - What we know: ROADMAP lists TRUST-01..06 as Phase 5 requirements but spells out only SC-1..SC-5; REQUIREMENTS.md has no TRUST-* rows.
   - What's unclear: which concrete behaviors TRUST-05/06 denote (likely: the safety-invariant gate, and audit-trail completeness for auto-execution).
   - Recommendation: planner confirms the mapping during PLAN; treat the AST safety gate (RQ-6) and the five new audit event types as covering TRUST-05/06 until told otherwise.
   - **RESOLVED (Plan 02):** TRUST-05/06 map to the AST/behavioral safety-invariant gate plus audit-trail completeness for auto-execution. The promotion/trust helpers and the safety-invariant tests (`test_trust_safety_invariants.py`) carry these; the five new audit event types cover the trail. Locked as the working interpretation across the plan set.

2. **Who posts the HITL Slack card for `propose-only` after `trigger_strategy_run`?**
   - What we know: `trigger_strategy_run` returns the PENDING proposal dict; the Slack/CLI/dashboard surfaces post the card (the orchestrator doesn't). The auto-branch, by contrast, must execute *inside* the run (no caller round-trip).
   - What's unclear: whether the auto-branch belongs inside `trigger_strategy_run` (so scheduled cycles auto-execute) or in each calling surface.
   - Recommendation: put the auto-branch **inside** `trigger_strategy_run` (scheduled cycles are the primary auto trigger; a caller-side branch would miss them). Return `outcome="auto_executed"` in the run summary so surfaces render the right thing.
   - **RESOLVED (Plan 05):** the auto-branch sits **inside** `trigger_strategy_run` after `write_proposal`, below the SDK boundary; the run summary carries `outcome` (`auto_executed` / `awaiting_2nd_channel` / `proposed`). propose-only still returns the PENDING proposal for the calling surface to post the HITL card.

3. **Start-of-day value source for single-day drawdown (D-T10).**
   - What we know: `get_positions()` exposes `market_value` + `cost_basis`; `get_account()` exposes `equity`. Realized P&L today is derivable from fill events (daily_pnl.py already does this).
   - What's unclear: whether to snapshot per-strategy open value once at market open vs compute live each tick; and whether "value" is per-strategy cost-basis or account equity attributed to the strategy.
   - Recommendation: persist a per-strategy open snapshot via a market-open scheduler job; flag to discuss-phase as the load-bearing anomaly design choice.
   - **RESOLVED (Plan 04, operator-reviewable assumption):** persist a per-strategy start-of-day snapshot via a market-open scheduler job; all intraday evaluations read this STABLE denominator (avoids the moving-denominator oscillation in Pitfall 3). This remains surfaced for operator confirmation as the single biggest anomaly design choice — the resolution records the chosen default, not a final operator sign-off.

4. **"Combined per-ticker exposure across strategies" (D-T07) given Alpaca position netting.**
   - What we know: a single Alpaca account holds one net position per ticker.
   - What's unclear: whether D-T07 intends to cap the account's single position in a ticker (against `max_correlated_ticker_pct`) — the only thing actually measurable — or some notional attribution across strategies.
   - Recommendation: implement the measurable interpretation (cap the account's single per-ticker position) and confirm with the operator in discuss-phase.
   - **RESOLVED (Plan 03, operator-reviewable assumption):** implement the measurable interpretation — cap the account's single net per-ticker position against `max_correlated_ticker_pct` (Alpaca holds one net position per ticker). Surfaced for operator confirmation; the resolution records the chosen default, not a final operator sign-off.

5. **Anomaly cancellation state for PENDING auto-proposals.**
   - What we know: `PENDING→REJECTED` and `PENDING→EXPIRED` edges exist; no "cancelled-by-anomaly" edge.
   - Recommendation: reuse `REJECTED` + `anomaly_demotion` event, or add a state if semantics matter. Planner decides; document in PLAN.
   - **RESOLVED (Plan 04):** reuse the existing `PENDING→REJECTED` edge plus a distinguishing `anomaly_demotion` audit event (lower-risk than adding a new state — RESEARCH A3). No new state machine edge added.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `claude-agent-sdk` | runtime (auto-branch sits below it) | ✓ | 0.2.93 | — |
| `alpaca-py` | portfolio aggregation, cancellation | ✓ | 0.42–0.50 pin | — |
| `apscheduler` (3.x) | anomaly scheduler tick | ✓ | 3.10–3.x | post-fill-only evaluation (degraded — misses unrealized drift) |
| `pandas_market_calendars` | NYSE gate for the anomaly tick | ✓ | installed | — |
| Alembic + SQLAlchemy 2.x | migration 0007 | ✓ | installed | — |
| `.venv/Scripts/python.exe` for tests | running pytest (MEMORY: full suite hangs at exit, exit 124 ≠ failure) | ✓ | 3.12 | — |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** anomaly scheduler tick — if APScheduler registration is deferred, post-fill evaluation alone still satisfies SC-4 for realized losses (recommend shipping both).

## Validation Architecture

> Nyquist validation is ENABLED. The highest-risk invariants below MUST be locked by tests/gates.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | `pytest` + `pytest-asyncio` `[VERIFIED: existing tests/unit]` |
| Config file | `pyproject.toml` / `pytest.ini` (existing) |
| Quick run command | `.venv/Scripts/python.exe -m pytest tests/unit/test_trust_*.py -x` (MEMORY: use venv python) |
| Full suite command | `.venv/Scripts/python.exe -m pytest tests/unit -x` (MEMORY: full suite hangs at exit; exit 124 ≠ failure; 3 pre-existing .env unit failures to deselect) |

### Highest-Risk Invariants → Validation Strategy
| Invariant | Risk | Validation Strategy |
|-----------|------|---------------------|
| **Auto-execute is impossible unless promotion criteria are met** | A bug grants autonomy to an unqualified strategy → unmonitored real-money trades | **AST gate** (model on Phase-2 `test_orderguard_place_order_ast_zero_decorators` + Phase-4 `test_decision_never_haiku_model`): assert the auto-branch in `runtime.py` is guarded by a trust-level check AND that no module outside `strategy/trust.py` flips `trust_level` to `auto-within-caps`. Plus a **behavioral test**: ineligible strategy + forged promote POST → server rejects (D-T18b) |
| **Portfolio caps reject what per-strategy caps allow** | Two strategies independently within their own caps breach the aggregate | **Property/behavioral test**: seed positions s.t. each per-strategy cap passes but the portfolio total/correlated/sector cap is exceeded → assert `OrderGuardRejected("portfolio_*")` |
| **Anomaly demotes BEFORE the hard cap halts (ordering)** | If the hard cap fires first, autonomy isn't removed gracefully | **Behavioral test**: drawdown between `anomaly_threshold_pct` and `max_daily_loss_usd` → assert demotion fires and `max_daily_loss` does NOT (the anomaly trip is the earlier rung, D-T11) |
| **Live + auto stacks the first-live dual-channel gate** | First live auto trade skips human confirm | **Behavioral test**: LIVE auto strategy, `first_live_trade_confirmed_at IS NULL` → assert proposal routes to `AWAITING_2ND_CHANNEL`, not direct execute |
| **Streak resets on cap breach + material edit; partitions per strategy+mode** | Wrong eligibility | **Unit tests** on `compute_clean_streak`: cap_rejection mid-window zeroes; material-edit boundary resets; cross-strategy approvals don't bleed; paper vs live counted separately |
| **Anomaly DM bypasses quiet hours; auto-exec DM respects them** | Operator missed a safety event, or spammed at night | **Unit tests** on DM category routing: `anomaly_demotion`→bypass set; `auto_execution`→routine set (suppressed in-window) |
| **OrderGuard re-checks caps at execute time on the auto path** | Auto path bypasses caps | **Behavioral test**: auto proposal that breaches a cap → `cap_rejection` event + status FAILED (proves `execute_proposal`→OrderGuard ran) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TRUST-01 | promote/demote + streak eligibility | unit | `pytest tests/unit/test_trust_streak.py -x` | ❌ Wave 0 |
| TRUST-01 | blocked-promotion explains (SC-5) | unit/route | `pytest tests/unit/test_trust_routes.py -x` | ❌ Wave 0 |
| TRUST-02 | portfolio caps reject cross-strategy breach | unit | `pytest tests/unit/test_portfolio_caps.py -x` | ❌ Wave 0 |
| TRUST-02 | auto path re-runs OrderGuard | unit | `pytest tests/unit/test_auto_execute.py -x` | ❌ Wave 0 |
| TRUST-03 | capital ceiling enforced + increase needs confirm | unit | `pytest tests/unit/test_capital_ceiling.py -x` | ❌ Wave 0 |
| TRUST-04 | drawdown demote + cancel + DM | unit | `pytest tests/unit/test_anomaly.py -x` | ❌ Wave 0 |
| TRUST-05/06 | safety-invariant AST gate | ast | `pytest tests/unit/test_trust_safety_invariants.py -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/unit/test_trust_*.py tests/unit/test_portfolio_caps.py tests/unit/test_anomaly.py -x`
- **Per wave merge:** full unit suite (`tests/unit`)
- **Phase gate:** full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/unit/test_trust_streak.py` — covers TRUST-01 streak counting, per-mode, reset boundaries
- [ ] `tests/unit/test_trust_routes.py` — promote/demote/capital dashboard routes + blocked explanation (SC-5)
- [ ] `tests/unit/test_portfolio_caps.py` — covers TRUST-02 four aggregate caps
- [ ] `tests/unit/test_capital_ceiling.py` — covers TRUST-03
- [ ] `tests/unit/test_anomaly.py` — covers TRUST-04 (drawdown math, demote, cancel, DM category)
- [ ] `tests/unit/test_auto_execute.py` — auto-branch routing + OrderGuard re-check + live-dual-channel stacking
- [ ] `tests/unit/test_trust_safety_invariants.py` — AST gate (TRUST-05/06)
- [ ] Migration test: `0007` upgrade/downgrade round-trips + backfill defaults (mirror existing migration tests)
- [ ] Streak attribution fixtures: enrich `approval`/`cap_rejection` payloads with strategy_name+mode in shared fixtures

*(Existing infrastructure covers DB session factories, broker MagicMocks, and OrderGuard test helpers — reuse `_make_strategy` / `_make_order_request` from `test_orderguard.py`.)*

## Security Domain

> `security_enforcement` is enabled (absent = enabled). Phase 5 directly handles autonomous real-money execution authority — the most security-sensitive surface in the project.

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V1 Architecture / Trust Boundaries | yes | The auto-branch sits BELOW the LLM boundary (deterministic Python); LLM never decides trust level. Reuse the Phase-4 research/decision isolation — trust check reads DB, not model output |
| V4 Access Control | yes | Every trust/capital route is `Depends(require_session)` (Phase-3 DASH-04); promote/demote re-verify server-side (D-T18b) — UI is affordance only. Cross-user defense: all queries filter by `user_id` (D-21) |
| V5 Input Validation | yes | Typed-strategy-name confirm on promote + capital-increase (forge-resistant); portfolio-cap form validates 0–100% / non-negative USD before storage (mirror `settings_post` ceiling Decimal validation) |
| V6 Cryptography | yes (inherited) | No new crypto; per-user SQLCipher DB protects trust state at rest (D-19) |
| V7 Error Handling / Logging | yes | Five new audit event types; never `event_type="error"` for trust events (BL-01); DM failures swallowed, never abort state transitions |
| V11 Business Logic | yes | The core of the phase: promotion gate, anomaly demotion, cap stacking are all business-logic invariants locked by the AST + behavioral gates above |

### Known Threat Patterns for this stack
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Forged promote POST grants autonomy without met criteria | Elevation of Privilege | Server re-checks `compute_clean_streak` eligibility on POST (D-T18b); returns blocked explanation, never promotes |
| Auto path bypasses OrderGuard / portfolio caps | Tampering | Auto-branch routes through `execute_proposal`→OrderGuard (D-T08); AST gate forbids direct `broker.place_order` |
| Live + auto skips first-live dual-channel gate | Elevation of Privilege | `first_live_trade_confirmed_at` check routes first live auto trade to `AWAITING_2ND_CHANNEL` |
| Runaway auto-loop drains the account | Denial of Service / financial | Anomaly demotion (earlier rung) + per-strategy `max_daily_loss` + portfolio `max_total_daily_loss` circuit breaker + kill switch (all stack) |
| Streak inflation via cross-strategy approval bleed | Tampering | Streak partitions by strategy_name + account_mode from enriched audit payloads |
| Trust state tampering via direct DB edit | Tampering | SQLCipher at-rest encryption (D-19); audit hash-chain (D-16) makes silent edits detectable |
| TOCTOU between eligibility check and promotion | Race | Evaluate trust ONCE at the auto-branch (proposal-build time); demotion effective next cycle (SC-1) — mirrors BLOCKER #5 account_mode locking |

## Sources

### Primary (HIGH confidence — read directly this session)
- `src/gekko/db/models.py` — `StrategyMetadata`, `User`, `Event`, `_EVENT_TYPES`, `_PROPOSAL_STATUSES`
- `src/gekko/strategy/promotion.py` — promote/demote/stamp helper template
- `src/gekko/execution/orderguard.py` — `place_order` pipeline + insertion point + passthrough cancel methods
- `src/gekko/execution/checks/_hard_caps.py` — per-strategy cap pattern, Decimal math, event-scan pattern
- `src/gekko/execution/checks/__init__.py` — check re-export pattern
- `src/gekko/agent/runtime.py` — `trigger_strategy_run`, write_proposal block, gate-before-LLM idiom
- `src/gekko/execution/executor.py` — `execute_proposal`, `on_fill_event`, DM seams + bypass categories
- `src/gekko/approval/proposals.py` — state machine, `approve_proposal`, transitions
- `src/gekko/approval/actions.py` — operator-edit cap gate (Knight-Capital invariant)
- `src/gekko/reporter/daily_pnl.py` — digest aggregation + DM bypass/routine routing
- `src/gekko/dashboard/routes.py` — `promote_to_live`, `settings_get/post`, dual-channel routes
- `src/gekko/schemas/strategy.py` — `HardCaps`, `Strategy` frozen shape + forward-compat note
- `src/gekko/scheduler/jobs.py` — APScheduler 3.x scheduler + job store
- `migrations/versions/0005_p4_cost_ceiling.py` — Alembic + CHECK-extension pattern
- `tests/unit/test_orderguard.py` — grep/AST gate patterns (zero-decorator, no-SDK-import)
- `tests/unit/test_decision_prompt_isolation.py` — directory-wide AST-walk gate pattern (Phase-4 D-05)
- `.planning/ROADMAP.md` §Phase 5 — authoritative SC-1..SC-5
- `.planning/phases/05-.../05-CONTEXT.md` — locked decisions D-T01..D-T18b
- `.planning/phases/05-.../05-UI-SPEC.md` — UI contract, default cap numbers, surfaces

### Secondary (MEDIUM confidence)
- `CLAUDE.md` stack table (note: APScheduler version claim corrected against pyproject)

### Tertiary (LOW confidence)
- None — all findings grounded in code read this session.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — verified against installed env + pyproject (one CLAUDE.md correction: APScheduler 3.x not 4.x)
- Architecture / integration points: HIGH — every seam read directly; auto-branch re-check via `execute_proposal`→OrderGuard confirmed in source
- Streak / state representation: HIGH on shape, MEDIUM on audit-payload enrichment (requires adding strategy_name+mode to approval/cap_rejection events)
- Anomaly drawdown design: MEDIUM — start-of-day value source is a genuine design choice (Open Q #3); flagged for discuss-phase
- Correlated-ticker semantics under Alpaca position netting: MEDIUM-HIGH risk (Open Q #4 / A2) — confirm interpretation before implementing
- Pitfalls / security: HIGH — derived from documented Phase 2/4 invariants (BLOCKER #5, BL-01, AST gates)

**Research date:** 2026-06-26
**Valid until:** 2026-07-26 (stable internal codebase; re-verify if Phases 2–4 modules change before planning)
