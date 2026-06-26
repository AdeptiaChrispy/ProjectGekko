# Phase 5: Trust Ladder (Per-Strategy Promotion & Portfolio Caps) - Context

**Gathered:** 2026-06-26
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 5 introduces a **second, orthogonal trust axis** on top of the existing
paper/live promotion ladder. After Phase 2, a strategy has a `mode`
(`paper`/`live`) plus `StrategyMetadata.live_mode_eligible` + first-live
dual-channel gate. Phase 5 adds the autonomy axis: `propose-only` →
`auto-within-caps`, per strategy.

A strategy in `auto-within-caps` executes within its hard caps **without HITL**,
but every auto-executed decision is still recorded with rationale and surfaced
to the operator. **Portfolio-level caps** stack on top of the existing
per-strategy `HardCaps`, aggregating across all of this user's strategies.
**Capital scaling** is its own separate promotion rung (its own confirmation).
**Anomaly detection** auto-demotes a strategy back to `propose-only` on a sudden
single-day drawdown.

This is flagged in ROADMAP.md and PROJECT.md as **the highest-stakes design
surface** — the real-money + autonomy interaction.

**In scope:**
- Per-strategy trust level (`propose-only` / `auto-within-caps`) — view, promote
  (explicit confirm), one-click demote (effective next cycle).
- Promotion gate: 10 clean HITL approvals, zero cap-breaches in the window;
  per-mode; material edits reset trust; blocked-with-explanation when criteria
  unmet (SC-5, not a silent failure).
- Auto-execution path: a promoted strategy auto-approves + executes within caps,
  no HITL, with rationale recorded + real-time informational DM + daily digest.
- Portfolio-level caps (aggregate across all strategies), deterministic in
  OrderGuard, applied to ALL orders.
- Capital scaling as a separate rung: per-strategy absolute USD capital ceiling,
  confirm-on-increase, new-limit audit event.
- Anomaly auto-demotion on single-day drawdown: demote + cancel pending
  auto-orders + urgent Slack DM.

**Out of scope (other phases):**
- Web dashboard multi-user auth, magic-link, audit browser (Phase 6).
- Daily/weekly **email** digests (Phase 6) — Phase 5 uses the existing Slack
  daily P&L digest as the auto-execution review surface.
- Additional brokers; ops/observability/supervision (Phases 7–9).
- A market-data correlation engine (correlated-strategy cap uses a pragmatic
  same-ticker-overlap definition, see D-T07).

</domain>

<decisions>
## Implementation Decisions

### Promotion gate (TRUST-01 / SC-1, SC-5)
- **D-T01:** Promotion to `auto-within-caps` requires **10 clean successful HITL
  approvals** for the strategy (count threshold). Tuned for swing-horizon
  (~1–3 trades/day) ≈ 1–2 weeks of clean operation.
- **D-T02:** The 10 approvals must be a **clean streak with zero OrderGuard
  cap-breaches** (`cap_rejection` audit events) in the qualifying window — any
  cap breach resets/blocks. Ties the gate to observable audit events we already
  emit.
- **D-T03:** The track record is **per-mode**, and `auto-within-caps` is allowed
  on **paper AND live independently**. Auto on paper is the natural safe way to
  validate autonomy. Going live still requires the existing **Phase-2 live
  promotion + first-live dual-channel gate** — i.e. **live + auto stacks both
  gates** (the scariest combo inherits every guard).
- **D-T04:** Promote/demote surface = **dashboard + CLI, NO Slack promote
  command** — matches Phase-2 D-31 (promotion is a deliberate sit-down action,
  not a phone tap). Demotion is **one-click** and takes effect on the **next
  decision cycle** (SC-1).
- **D-T05:** **Material edits reset trust.** Editing `watchlist` or `hard_caps`
  (a new snapshot version per D-05) drops the strategy back to `propose-only` and
  **restarts the 10-approval streak** — trust is earned per-configuration.
  Thesis-only / cosmetic edits do NOT reset.
- **D-T18b (SC-5):** Attempting to enable auto on a strategy that hasn't met the
  criteria is **blocked with a clear explanation** (which criterion failed, how
  far along the streak is) — never a silent failure.

### Portfolio-level caps (TRUST-02 / SC-2)
- **D-T06:** Ship **all four** portfolio caps, aggregating across all of this
  user's strategies: **(1) max total exposure** (% of account equity across all
  open positions), **(2) max sector concentration** (aggregate sector exposure
  across strategies), **(3) max correlated-strategy exposure**, **(4) max total
  daily loss** (USD, portfolio-wide circuit breaker stacking on per-strategy
  `max_daily_loss_usd`).
- **D-T07:** **Correlated-strategy exposure = same-ticker overlap across
  strategies** — cap the combined per-ticker exposure when multiple strategies
  hold the same ticker. Cheap, deterministic, uses positions/watchlists we
  already have. The honest, explainable definition (no correlation engine).
- **D-T08:** Portfolio caps apply to **ALL orders (HITL + auto)**, enforced in
  the deterministic OrderGuard layer that already runs on every order. No path
  where manual approval can exceed the portfolio limit.
- **D-T09:** Portfolio caps are **user-level config in dashboard Settings**
  (single-tenant per D-18), alongside the Phase-4 cost ceiling + Phase-3
  quiet-hours. Ship conservative **defaults** (exact numbers = planner/researcher
  discretion); runtime-editable.

### Capital scaling (TRUST-03 / SC-3)
- **D-T14:** Capital scaling shape = **arbitrary per-strategy USD capital
  ceiling**; any **increase requires a fresh confirmation step + audit record**;
  **lowering is free** (always safe to de-risk). No artificial fixed rungs.
- **D-T15:** Represented as a **new per-strategy absolute USD capital-ceiling
  field**, enforced in **OrderGuard**: caps the strategy's **total deployed
  capital** (sum of open positions + this order) to the ceiling, **stacking with
  `max_position_pct`** and the portfolio caps. Absolute dollars is what "scale to
  $10K" means.
- **D-T16:** First-promotion starting ceiling default = **$1,000** (matches SC-3
  example + PROJECT.md "start with small dollars").
- **D-T17:** Capital scaling is a **separate ladder from trust** — scaling up
  requires its own confirmation + a **capital-limit audit event**, but does
  **NOT** reset the `propose-only` streak or auto status. Two independent
  ladders: autonomy and capital.

### Anomaly auto-demotion (TRUST-04 / SC-4)
- **D-T10:** Trigger metric = **single-day drawdown** (today's realized +
  unrealized loss for the strategy vs its start-of-day value, as a %). Best match
  for SC-4's "sudden drawdown" / runaway-loop failure mode.
- **D-T11:** Threshold is **configurable per-strategy, default 10% single-day**.
  It is a **separate, earlier trip than the per-strategy `max_daily_loss_usd`
  hard cap** — anomaly **removes autonomy** before the hard cap **halts trading**.
- **D-T12:** On fire: **cancel this strategy's pending auto-orders** (open broker
  orders + PENDING auto-proposals) **+ demote to `propose-only`**. The strategy
  **keeps running research** — it just needs human approval again. Surgical, not
  a full halt; does not cascade to other strategies.
- **D-T13:** The anomaly-demotion Slack DM **bypasses quiet hours** — it is
  operator-safety-critical, same tier as kill-switch / cap-rejection / first-live
  (reuse the Phase-3 bypass-category path).

### Auto-execution review surface (SC-2)
- **D-T18:** Each auto-execution sends a **real-time informational Slack DM (no
  approve/reject buttons** — ticker/side/size/rationale) **AND** appears in the
  daily P&L digest. The informational DM **respects quiet hours** (it is not
  safety-critical; demotion/cap-rejection DMs keep their urgent bypass).

### Claude's Discretion
- **State representation** — where `trust_level` and the capital ceiling /
  anomaly threshold live. Strong candidate: new columns on `StrategyMetadata`
  (already the per-(user, strategy_name) home for the live-promotion ladder),
  plus an Alembic migration following the Phase-2/3/4 pattern.
- **Clean-approval counting mechanism** — how the 10-approval streak + "no
  cap-breach in window" is computed from the append-only `events` log
  (`approval` events vs `cap_rejection` events), and where the window boundary
  resets (on demotion, on material edit per D-T05).
- **Exact default numbers** for the four portfolio caps.
- **Where the auto-execute branch lives** — likely after `write_proposal` in
  `trigger_strategy_run`: if the strategy is `auto-within-caps`, skip the HITL DM
  and route directly into `execute_proposal` (which already runs OrderGuard as
  the last line). Researcher/planner confirm the exact insertion point.
- **Anomaly evaluation cadence** — when the single-day drawdown check runs
  (post-fill, on a scheduler tick, or both).
- **New audit `event_type` values** — e.g. `trust_promoted` / `trust_demoted` /
  `anomaly_demotion` / `capital_scaled` / `auto_execution`, vs reusing existing
  types. Follow the Phase-2/3/4 CHECK-constraint-extension pattern in
  `db/models.py` `_EVENT_TYPES`.
- **A safety-invariant AST/test gate** — "auto-execute is impossible unless the
  promotion criteria are met" is a strong candidate for the Phase-4 D-05 AST-gate
  treatment.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 5 scope & success criteria
- `.planning/ROADMAP.md` §"Phase 5: Trust Ladder (Per-Strategy Promotion &
  Portfolio Caps)" — Goal + 5 success criteria (TRUST-01…06). **Authoritative
  requirement source** — REQUIREMENTS.md has no TRUST-* detail.
- `.planning/PROJECT.md` §Requirements (Active v2.0) "Trust Ladder" bullet;
  §Key Decisions ("HITL → graduated autonomy via per-strategy promotion",
  "Trust ladder design treated as a dedicated phase", "Single-tenant runtime
  per Gekko instance (D-18)"); §Constraints "Trade-execution safety".

### Existing promotion ladder (paper/live) — extend, don't duplicate
- `src/gekko/strategy/promotion.py` — `promote_strategy_to_live` /
  `demote_strategy_from_live` / `stamp_first_live_trade` /
  `load_strategy_metadata`. The trust-axis promote/demote helpers mirror this
  module's shape (no `claude_agent_sdk` import; per-user session-factory shim;
  audit events). D-31 (symmetric CLI+dashboard, no Slack promote) and D-32
  (per-strategy first-live stamp) are the precedents the trust ladder follows.
- `src/gekko/db/models.py` — `StrategyMetadata` (per-(user, strategy_name) home
  for the ladder; add trust columns here), `User` (user-level settings columns:
  kill, quiet-hours, timezone, daily cost ceiling — portfolio caps join here),
  `Proposal` (status state machine; `account_mode`), `Event` (`_EVENT_TYPES`
  CHECK vocabulary — extend for new trust/anomaly/capital event types).

### Caps + OrderGuard (deterministic enforcement)
- `src/gekko/execution/checks/_hard_caps.py` — the 4 per-strategy hard caps
  (`max_position_pct`, `max_daily_loss_usd`, `max_trades_per_day`,
  `max_sector_exposure_pct`); Decimal-exact math; best-effort sector resolution.
  Portfolio caps + capital ceiling are NEW checks stacking on these.
- `src/gekko/execution/orderguard.py` — `OrderGuard(Brokerage)` wraps the broker;
  `place_order` runs the check pipeline as the last line before broker
  submission. Portfolio caps + capital-ceiling check slot into this pipeline.
- `src/gekko/schemas/strategy.py` — `HardCaps` + `Strategy` Pydantic models
  (frozen field set; snapshot-versioned per D-05). Any new per-strategy field
  (capital ceiling, anomaly threshold) must respect the forward-compat note.

### Auto-execution path (skip HITL)
- `src/gekko/agent/runtime.py` — `trigger_strategy_run` orchestrates
  Researcher→Decision→`write_proposal`; returns the PENDING proposal. The
  auto-within-caps branch decides here whether to route to HITL or auto-execute.
  Quiet-hours gate + `_send_slack_dm_respecting_quiet_hours` live nearby.
- `src/gekko/approval/actions.py` — `execute_proposal` (PENDING → APPROVED →
  OrderGuard + broker). The auto path calls into this; OrderGuard re-checks at
  `execute_proposal` time as the last line.

### Notification + digest surfaces
- `src/gekko/reporter/daily_pnl.py` — `send_daily_pnl_digest`,
  `_aggregate_today_events`, `_build_digest_blocks`,
  `_send_dm_blocks_respecting_quiet_hours`. Auto-executions surface here (SC-2);
  natural place to also fold in anomaly-demotion summary.
- `src/gekko/execution/executor.py` — `_send_slack_dm_respecting_quiet_hours` +
  the bypass-category routing (kill / cap_rejection / first-live always fire).
  Anomaly-demotion DM reuses the bypass category (D-T13); auto-execution
  informational DM uses the quiet-hours-respecting path (D-T18).

### Locked decisions to honor (prior phases)
- `.planning/phases/02-orderguard-real-money-alpaca-live-safety-floor/02-CONTEXT.md`
  — D-31 (symmetric promote surfaces, no Slack promote), D-32 (per-strategy
  first-live stamp), D-26 (OrderGuard is a `Brokerage` subclass), D-29 (hard
  caps).
- `.planning/phases/04-agent-architecture-cost-bounds/04-CONTEXT.md` — D-01
  (single per-user pooled ceiling → user-level config precedent), D-05
  (safety-invariant AST/test-gate pattern), deterministic-guard-before-LLM idiom.
- `.planning/phases/01-foundation-vertical-slice-alpaca-paper-slack-hitl/01-CONTEXT.md`
  — D-05 (snapshot-row versioning), D-14 (append-only audit + event vocabulary),
  D-18 (single-tenant per-user runtime).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `StrategyMetadata` (`db/models.py`) — already the per-(user, strategy_name)
  ladder home; trust level + capital ceiling + anomaly threshold are natural new
  columns. `strategy/promotion.py` is the exact template for trust promote/demote
  helpers (session-factory shim, audit events, no LLM imports).
- `check_hard_caps` (`execution/checks/_hard_caps.py`) — the per-strategy cap
  pattern (Decimal-exact, raises `OrderGuardRejected`, best-effort sector
  resolution, `_today_utc_window`); portfolio caps are the cross-strategy
  generalization. `cap_rejection` audit events are the signal for the clean-streak
  gate (D-T02).
- OrderGuard's `place_order` pipeline + `OrderGuardRejected` — the insertion
  point for the new portfolio + capital-ceiling checks; runs deterministically
  before the broker on EVERY order (D-T08).
- `_send_slack_dm_respecting_quiet_hours` + bypass-category routing (Phase 3/4) —
  reused directly: informational auto-exec DM respects quiet hours (D-T18);
  anomaly-demotion DM bypasses them (D-T13).
- `send_daily_pnl_digest` + `_build_digest_blocks` — the SC-2 review surface for
  auto-executed decisions.

### Established Patterns
- **Deterministic guards run BEFORE the LLM and as the last line at execute
  time** (OrderGuard, market-hours) — portfolio caps + capital ceiling follow
  this; the LLM cannot reason past them.
- **Safety invariants locked by AST/test gates** (Phase-4 D-05, place_order grep
  gates) — "auto-execute requires met promotion criteria" is a prime candidate.
- **Append-only audit with an extensible `_EVENT_TYPES` CHECK vocabulary** +
  Alembic migration per phase (0001→0006) — new trust/anomaly/capital events
  follow this; the prior `live_mode_promoted`/`live_mode_demoted` additions are
  the template.
- **Snapshot-row strategy versioning (D-05)** — material edits create a new
  version; the trust reset (D-T05) keys off this.
- **Decimal everywhere for money math** (`normalize_decimals`) — capital ceiling
  + portfolio caps must stay Decimal-exact.

### Integration Points
- `trigger_strategy_run` (after `write_proposal`): branch on trust level →
  HITL DM vs auto-execute via `execute_proposal`.
- OrderGuard `place_order` pipeline: add portfolio-caps check + capital-ceiling
  check stacking on `check_hard_caps`.
- `StrategyMetadata` + Alembic migration: trust level, capital ceiling, anomaly
  threshold columns.
- `User` + Settings route/template: user-level portfolio-cap config (alongside
  cost ceiling + quiet hours).
- Anomaly evaluator: post-fill and/or scheduler tick → drawdown check → demote +
  cancel + urgent DM.
- Daily digest + executor DM paths: auto-execution surfacing.

</code_context>

<specifics>
## Specific Ideas

- **Two orthogonal ladders, kept distinct:** paper↔live (Phase 2) and
  propose-only↔auto-within-caps (Phase 5), plus capital scaling as a third
  independent rung. The user explicitly wants these decoupled — `live + auto`
  stacks every gate; scaling capital does not reset autonomy trust.
- **Trust is earned per-configuration:** material edits to watchlist/caps reset
  the streak (D-T05). The track record only means something for the config it
  was earned on.
- **Anomaly demotion is an early-warning reflex, not the hard stop:** it sits
  *below* `max_daily_loss_usd` and removes autonomy (back to HITL) before the
  hard cap halts trading entirely.
- **"Portfolio is portfolio":** portfolio caps gate ALL orders, not just auto —
  no path where a human approval sneaks past the aggregate limit.
- **Promotion is a deliberate sit-down action** (dashboard/CLI, no Slack
  promote) — consistent with the Phase-2 stance that autonomy shouldn't be a
  phone tap.

</specifics>

<deferred>
## Deferred Ideas

- **Market-data correlation engine** for correlated-strategy exposure — declined
  for v2.0 in favor of the pragmatic same-ticker-overlap definition (D-T07).
  Revisit if same-ticker overlap proves too coarse.
- **Email digests** (daily/weekly) as an auto-execution review surface — belongs
  to Phase 6; Phase 5 uses the existing Slack daily P&L digest.
- **Portfolio-wide anomaly cascade** (demoting all auto strategies when one trips)
  — considered and declined (D-T12 keeps demotion surgical to the offending
  strategy). Revisit if a correlated drawdown across strategies becomes a real
  pattern.
- **Fixed/multiplier capital rungs** — declined in favor of an arbitrary ceiling
  with confirm-on-increase (D-T14).

### Reviewed Todos (not folded)
None — no pending todos matched this phase.

</deferred>

---

*Phase: 05-trust-ladder-per-strategy-promotion-portfolio-caps*
*Context gathered: 2026-06-26*
