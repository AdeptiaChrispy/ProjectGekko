# Roadmap: Project Gekko

**Created:** 2026-06-08
**Last reorganized:** 2026-06-15 (v1.0 shipped)
**Mode:** mvp (Vertical MVP — each phase delivers an end-to-end user capability)
**Core Value:** A trustworthy autonomous agent that turns a plain-English investment thesis into actual, monitored trades on the user's own brokerage account — starting human-in-the-loop with small dollars and graduating to autonomy as trust is earned.

## Milestones

- ✅ **v1.0 Vertical-Slice MVP** — Phase 1 (shipped 2026-06-15) — Paper-trading + Slack HITL works end-to-end on the operator's machine. See `milestones/v1.0-ROADMAP.md` for the archived snapshot.
- ✅ **v2.0 Safety & Trust** — Phases 2-5 (shipped 2026-06-28) — OrderGuard, real-money Alpaca live, production HITL UX, agent architecture hardening, trust ladder. Archived as `tech_debt` (Phase 03/04 human verification + Phase 03 UAT deferred). See `milestones/v2.0-ROADMAP.md` for the archived snapshot.
- 📋 **v3.0 Research & Analysis + Multi-User + Multi-Broker + Deployment** — Phases 5.1–5.5 + 6-9 (planned) — Research & Analysis block (backtesting, quant factor library, fundamental research/reports, behavior analytics, conversational strategy UI), then web dashboard with multi-user auth, operations/observability, IBKR + Schwab, browser-fallback brokers + one-command install.

## Phase Ordering Rationale

This roadmap reflects the **safety-first sequencing** that all four research dimensions (stack, features, architecture, pitfalls) converged on independently:

1. **Vertical slice through Alpaca paper + Slack HITL first.** (✅ shipped as v1.0) Proves the agent loop end-to-end with zero real-money risk; foundation decisions (`user_id` plumbing, `Decimal` everywhere, append-only audit, regulatory framing) cannot be retrofitted.
2. **OrderGuard + real-money Alpaca live next.** Per PITFALLS.md: "If this layer doesn't exist by Phase 2, every later phase is building on quicksand." The non-LLM cap-enforcement layer is the single most important architectural element. Knight-Capital prevention lives here.
3. **HITL UX dedicated phase.** Production Slack Block Kit with idempotent buttons (Slack's at-least-once delivery is a real failure mode), quiet hours, timeout=REJECT, first-live-trade gate.
4. **Agent architecture + cost bounds.** Research/decision separation (defends against drift AND prompt injection); two-tier cost ceiling (80% graceful degradation, 100% hard halt); tool-use enforcement.
5. **Trust Ladder dedicated phase.** Per PROJECT.md key decision and three of four researchers. Per-strategy promotion, portfolio caps, capital scaling rung — the highest-stakes design surface.
6. **Web Dashboard + Auth.** Magic-link, strategy editor, portfolio view, audit browser, web-based approval fallback.
7. **Operations & Observability.** Supervisors, heartbeat, NTP, reconciliation, sleep/wake.
8. **Additional API brokers (IBKR + Schwab).** Layer onto a hardened `Broker` ABC; Schwab's 7-day refresh-token coordinator is the operational headline.
9. **Browser-Fallback Brokers + Deployment Packaging.** Robinhood + Fidelity last (fragility, TOS risk); merged with one-command install + first-run wizard.

**Research & Analysis block (Phases 5.1–5.5, inserted 2026-06-28):** A native Claude Agent SDK reimplementation of capabilities inspired by HKUDS/Vibe-Trading and ai4finance-foundation/finrobot — concepts only, no LangChain/AutoGen, Claude-only LLM (honors the "stay in the Anthropic ecosystem" constraint). Sequenced *before* the dashboard so the dashboard has real backtests, factors, reports, and analytics to surface. Internal order: 5.1 Backtesting Engine → 5.2 Factor/Signal Library (feeds the backtester) → 5.3 Fundamental Research & Reports → 5.4 Behavior Analytics → 5.5 Conversational Strategy Interface (the chat-driven surface that presents all of the above; precursor to the Phase 6 dashboard).

**Hard sequencing constraints:**

- Phases 1 → 2 → 3 cannot be reordered (slice → safety floor → production HITL UX)
- Phase 5 (Trust Ladder) must precede Phase 9 (Browser-Fallback) — graduate autonomy on stable API brokers, not fragile browser path
- Phase 7 (Ops) must precede full autonomy in production usage
- Phases 5.1 → 5.2 build the quant validation stack; 5.5 (Conversational Interface) depends on 5.1–5.4 and precedes Phase 6 (Dashboard reuses the 5.5 surface)

## Phases

<details>
<summary>✅ v1.0 Vertical-Slice MVP (Phase 1) — SHIPPED 2026-06-15</summary>

- [x] **Phase 1: Foundation & Vertical Slice (Alpaca Paper + Slack HITL)** — 9/9 plans complete; manual demo passed 2026-06-12 (22-event audit chain intact across 3 real paper-trading runs)

Six demo-discovery fixes landed at close (commit `297a882` + quick tasks `260612-dix` and `260612-nlv`). One Phase-3 backlog item carried forward: executor-error → Slack DM surfacing on `MarketClosed` / `BrokerOrderError`.

See `milestones/v1.0-ROADMAP.md` for the full archived snapshot (with detailed plan list, requirements mapping, and success criteria).

</details>

<details>
<summary>✅ v2.0 Safety & Trust (Phases 2-5) — SHIPPED 2026-06-28</summary>

- [x] **Phase 2: OrderGuard & Real-Money Alpaca Live (Safety Floor)** — Non-LLM cap-enforcement layer; first real money flows (still HITL). (completed 2026-06-17)
- [x] **Phase 3: Production HITL UX (Slack Block Kit + Dashboard Fallback)** — Idempotent approval flow, quiet hours, timeout=REJECT, first-live gate. 15/15 plans. Security 98/98. (completed 2026-06-23)
- [x] **Phase 4: Agent Architecture & Cost Bounds** — Research/decision separation, prompt-injection defense, two-tier cost ceiling. 8/8 plans. Security 32/32. (completed 2026-06-25)
- [x] **Phase 5: Trust Ladder (Per-Strategy Promotion & Portfolio Caps)** — Propose-only → auto-within-caps; portfolio-level caps; capital scaling rung; anomaly demotion. (completed 2026-06-26)

Archived as `tech_debt` (Phase 03/04 human verification + Phase 03 UAT deferred). See `milestones/v2.0-ROADMAP.md` for the full archived snapshot.

</details>

### 📋 v3.0 Research & Analysis + Multi-User + Multi-Broker + Deployment (Planned)

*Research & Analysis block (Phases 5.1–5.5, INSERTED 2026-06-28): a native Claude Agent SDK reimplementation of capabilities inspired by [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) and [ai4finance-foundation/finrobot](https://github.com/ai4finance-foundation/finrobot). Concepts only — no LangChain/AutoGen, Claude-only LLM, honoring the PROJECT.md "stay in the Anthropic ecosystem" constraint. 5.1–5.4 build the analysis capabilities; 5.5 is the chat-driven web interface that surfaces them. Sequenced before the dashboard so it has real backtests/reports/analytics to surface.*

- [ ] **Phase 05.1: Backtesting Engine** *(INSERTED)* — Validate a strategy on history before it trades live: walk-forward, Monte Carlo CIs, point-in-time safety, Sharpe/drawdown/IR/win-rate, reproducible run cards. Feeds the trust-ladder.
- [ ] **Phase 05.2: Quant Factor and Signal Library** *(INSERTED)* — Library of testable quant factors/signals with IC/IR ranking, lookahead-guard tests, alive/reversed/dead categorization. Feeds the 05.1 backtester.
- [ ] **Phase 05.3: Fundamental Research and Reports** *(INSERTED)* — Financial-statement analysis, DCF valuation, peer comparables, 3-yr projections, and equity-research report generation (HTML/PDF). Deepens the "research the thesis" half.
- [ ] **Phase 05.4: Behavior Analytics Shadow Account** *(INSERTED)* — Import broker journals; profile disposition effect/overtrading; extract recurring trades into signal logic; counterfactual rule-vs-actual; reports with audit trails.
- [ ] **Phase 05.5: Conversational Strategy Interface** *(INSERTED)* — Chat-driven web UI (Vibe-Trading-style) to build strategies in plain English, watch the agent's step-trace, see trade proposals, and view strategy performance over time inline. Surfaces 5.1–5.4 outputs; precedes the full dashboard.
- [ ] **Phase 6: Web Dashboard & Multi-User Auth** — Magic-link auth, strategy editor, portfolio view, audit browser, web approval fallback.
- [ ] **Phase 7: Operations & Observability** — launchd/NSSM supervision, heartbeat, NTP, reconciliation, market-hours scheduling.
- [ ] **Phase 8: Additional API Brokers (IBKR + Schwab)** — `Broker` ABC implementations; Schwab 7-day OAuth refresh coordinator; IBKR Gateway supervision.
- [ ] **Phase 9: Browser-Fallback Brokers & Deployment Packaging** — Robinhood + Fidelity via `browser-use`; one-command install + first-run wizard.

## Phase Details

### Phase 05.1: Backtesting Engine

> *INSERTED 2026-06-28. Native Claude Agent SDK reimplementation — concept inspired by [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)'s signal/backtest core. No LangChain; Python + SQLite on the existing stack.*

**Goal**: User can run a defined strategy against historical market data and get a trustworthy, reproducible performance report — walk-forward windows, Monte Carlo / bootstrap confidence intervals, point-in-time data safety, and OHLC integrity checks — so a strategy is validated on history before it ever trades live, feeding the existing trust-ladder (paper-validated → live promotion).
**Milestone:** v3.0
**Mode:** mvp
**Depends on**: Phase 5
**Requirements**: BTST-01, BTST-02, BTST-03, BTST-04, BTST-05 *(NEW — to be formalized in REQUIREMENTS.md before planning)*
**Success Criteria** (what must be TRUE):

  1. User can point a strategy at a historical date range and receive a run card with Sharpe ratio, max drawdown, information ratio, win rate, and benchmark comparison, all computed from point-in-time-safe data (no lookahead leakage)
  2. User running the same backtest twice with the same inputs gets identical results — every run card records its inputs, data snapshot, and code version for reproducibility
  3. User sees walk-forward results (out-of-sample windows), not just a single in-sample fit, and a Monte Carlo / bootstrap confidence interval around the headline metrics so a lucky single path is distinguishable from a robust edge
  4. Backtest hard-rejects data that fails OHLC integrity checks (high < low, non-positive prices, gaps) with the offending rows surfaced, rather than silently producing a misleading result
  5. A strategy that fails to clear a user-set backtest threshold cannot be promoted toward live trading — the result is wired into the trust-ladder promotion gate

**Plans**: TBD
**UI hint**: yes

### Phase 05.2: Quant Factor and Signal Library

> *INSERTED 2026-06-28. Native reimplementation — concept inspired by Vibe-Trading's alpha-factor zoos (GTJA191 / Qlib158 / Kakushadze101). Factor formulas reimplemented in Python; Claude-only for any reasoning.*

**Goal**: User (and the agent) can draw on a library of testable quantitative factors/signals — each with an information-coefficient (IC) and information-ratio (IR) profile, lookahead-guard tests, and an alive/reversed/dead status — so trade decisions rest on concrete, backtestable signals rather than pure-LLM hunches. Factors feed directly into the Phase 5.1 backtester.
**Milestone:** v3.0
**Mode:** mvp
**Depends on**: Phase 05.1
**Requirements**: SGNL-01, SGNL-02, SGNL-03, SGNL-04, SGNL-05 *(NEW — to be formalized in REQUIREMENTS.md before planning)*
**Success Criteria** (what must be TRUE):

  1. User can list available factors and see each one's IC mean/std and IR ranking over a chosen universe and period, computed through the Phase 5.1 backtest harness
  2. Every factor passes an automated lookahead-guard test before it can be listed; a factor that references future data is rejected and flagged, never silently included
  3. User sees each factor categorized as alive / reversed / dead based on recent vs. historical performance, so decayed signals are visibly distinguished from live ones
  4. The decision agent can request one or more factors as structured inputs to a trade rationale, and the chosen factors plus their current values are recorded in the trade's audit record
  5. User can add a new factor definition and have it validated (purity/lookahead checks) and benched against the standard universe without writing bespoke harness code

**Plans**: TBD
**UI hint**: yes

### Phase 05.3: Fundamental Research and Reports

> *INSERTED 2026-06-28. Native reimplementation — concept inspired by [ai4finance-foundation/finrobot](https://github.com/ai4finance-foundation/finrobot)'s equity-research agents. Built on Gekko's existing SEC EDGAR + data sources and the Claude Agent SDK; no AutoGen, Claude-only LLM.*

**Goal**: User can request a fundamental research brief on a ticker and receive a structured, sourced analysis — financial-statement extraction (income / balance sheet / cash flow), DCF valuation, peer comparables (P/E, EV/EBITDA), and 3-year projections — rendered as a shareable equity-research report (HTML/PDF with charts), deepening the "research the thesis" half of the agent.
**Milestone:** v3.0
**Mode:** mvp
**Depends on**: Phase 05.2
**Requirements**: FUND-01, FUND-02, FUND-03, FUND-04, FUND-05 *(NEW — to be formalized in REQUIREMENTS.md before planning)*
**Success Criteria** (what must be TRUE):

  1. User can request a research brief for a ticker and get income statement, balance sheet, and cash-flow figures extracted from primary filings (SEC EDGAR) with every figure traceable to its source filing and date
  2. User sees a DCF valuation with its assumptions (discount rate, growth, terminal value) shown and adjustable, plus a peer-comparables table (P/E, EV/EBITDA) against a named peer set
  3. User receives 3-year projections clearly labeled as model estimates (not facts), with the inputs that drive them visible
  4. The agent can attach a fundamental brief's key findings to a trade proposal's rationale, with confidence and the specific evidence cited, consistent with the existing structured-rationale format
  5. User can export the full brief as an HTML/PDF report with charts; the report carries a disclaimer consistent with the project's personal-use / non-regulated-advice posture

**Plans**: TBD
**UI hint**: yes

### Phase 05.4: Behavior Analytics Shadow Account

> *INSERTED 2026-06-28. Native reimplementation — concept inspired by Vibe-Trading's "Shadow Account" behavior analytics. Python + SQLite + Claude Agent SDK; a trust/observability feature, not a trading one.*

**Goal**: User can import their own broker trade journal and get an honest mirror of their trading behavior — holding days, win rate, PnL ratio, drawdown, disposition effect, and overtrading detection — plus extraction of recurring trades into explicit signal logic and a counterfactual rule-vs-actual comparison, delivered as a report with an audit trail.
**Milestone:** v3.0
**Mode:** mvp
**Depends on**: Phase 05.3
**Requirements**: BHVR-01, BHVR-02, BHVR-03, BHVR-04, BHVR-05 *(NEW — to be formalized in REQUIREMENTS.md before planning)*
**Success Criteria** (what must be TRUE):

  1. User can import a broker trade journal (CSV) and see it parsed into per-trade records scoped to their own account, with malformed rows surfaced rather than silently dropped
  2. User sees a behavior profile — holding days, win rate, PnL ratio, drawdown, disposition effect (holding losers / selling winners), and overtrading flags — computed from their actual fills
  3. The system extracts recurring trade patterns into explicit, human-readable signal logic the user can review and optionally turn into a candidate strategy
  4. User sees a counterfactual comparison: how a rule-based "shadow" version of their behavior would have performed versus their actual trades, highlighting misses and rule violations
  5. User can export the analysis as an HTML/PDF report; all imported data and derived findings are recorded in the append-only audit log and isolated per user

**Plans**: TBD
**UI hint**: yes

### Phase 05.5: Conversational Strategy Interface

> *INSERTED 2026-06-28. Chat-driven web surface, visually modeled on the Vibe-Trading agent UI. Built on Gekko's existing FastAPI + HTMX + Jinja2 web stack (extending the Phase 3 dashboard surface) — no new SPA framework, Claude Agent SDK for the conversation. Precursor to, and reused by, the Phase 6 dashboard.*

**Goal**: User can open a web chat interface, describe and refine an investment strategy in plain English, watch the agent's research/decision work as a live step-trace, review generated trade proposals, and view each strategy's performance over time — all inline in the conversation — turning Gekko's core "plain-English thesis → monitored trades" loop into a first-class visual experience.
**Milestone:** v3.0
**Mode:** mvp
**Depends on**: Phase 05.4
**Requirements**: CHAT-01, CHAT-02, CHAT-03, CHAT-04, CHAT-05 *(NEW — to be formalized in REQUIREMENTS.md before planning)*
**Design reference**: `../../../Interface Examples/` (4 Vibe-Trading screenshots, 2026-06-28) — left sidebar (Home / Agent / Sessions), welcome screen with capability chips + grouped example-prompt cards, conversational results with step-trace + inline metrics tables, analysis narrative + equity-curve sparkline + "Full Report", and a full-report view with metrics ribbon, Chart/Trades/Code tabs, candlestick chart, and CSV download. *(Visual inspiration; a UI-SPEC via `/gsd-ui-phase 5.5` should formalize the contract before planning.)*
**Success Criteria** (what must be TRUE):

  1. From the web UI, user describes a strategy in plain English in a chat input and the agent responds conversationally, producing/updating the same canonical strategy document used by the existing onboarding flow — no form required to get started
  2. User sees the agent's work as a live step-trace (e.g., "Done · N steps · Ns") and structured results — strategy summary and a key-metrics table — rendered inline in the conversation, visually consistent with the reference mockup
  3. A left sidebar lists prior sessions and the user can resume any past conversation with its context intact; a welcome screen offers example-prompt cards to start a new one
  4. User can see trade proposals for a strategy inline and act on them through the existing HITL approval path, and can view the strategy's performance over time (equity curve + key metrics with a link to the full report) drawing on the Phase 5.1 backtester and 5.4 analytics
  5. The interface runs on the existing FastAPI + HTMX web surface behind the current session login, is scoped to the signed-in user, and shows the paper-vs-live banner on every view — no new front-end framework introduced

**Plans**: TBD
**UI hint**: yes

### Phase 6: Web Dashboard & Multi-User Auth

**Goal**: Each user can sign into a personal web dashboard via magic-link email, view their portfolio and trade history with rationale, edit strategies via chat-and-form, drop ad-hoc guidance, browse the audit log, and approve trades via web fallback when Slack is unavailable.
**Milestone:** v3.0
**Mode:** mvp
**Depends on**: Phase 05.5
**Requirements**: AUTH-01, AUTH-02, DASH-01, DASH-02, DASH-03, DASH-05, DASH-06, REPT-02, REPT-03, REPT-05, AUDT-03, AUDT-04
**Note**: Phases 5.1–5.5 were inserted ahead of this phase (2026-06-28). The conversational interface (5.5) and the research/analysis capabilities (5.1–5.4) are reused here; the dashboard layers multi-user magic-link auth, audit browsing, and the web approval fallback on top of that surface.
**Success Criteria** (what must be TRUE):

  1. User can request a magic-link email, click the link, and land on their dashboard with a session that persists across browser refresh (default 7-day timeout); sessions are scoped per user and never leak data across users
  2. User can view their current portfolio (positions, cost basis, current value, unrealized P&L) per strategy and in aggregate, with a paper-vs-live indicator (green/red banner) visible on every page
  3. User can view trade history filterable by strategy/ticker/date with the structured per-trade rationale (thesis category, supporting evidence, confidence, alternatives considered) rendered inline; CSV export works for tax software
  4. User can edit a strategy via chat-and-form, drop ad-hoc guidance ("look at energy this week"), and see the change versioned with diff visible — same canonical strategy document as Slack onboarding
  5. User receives a daily email digest (portfolio snapshot, day's trades with rationale, P&L, anomalies) and a weekly digest (multi-day P&L, strategy attribution, rationale themes); audit log is browsable in the dashboard with filter/search and exportable as CSV

**Plans**: TBD
**UI hint**: yes
**Carried-in enhancements** (from Phase 3 live UAT, 2026-06-22 — deferred here per operator):

  - Segment /approvals proposals by state — expired trades in their own section, separate from pending/complete; consider tabs (Pending / Completed / Expired).
  - Add/improve a persistent site-wide nav toolbar so moving between dashboard pages (approvals, strategies, kill-switch, portfolio, audit) is easy.
  - OrderGuard preflight + modify-and-resubmit on edit/approve: run OrderGuard checks before committing; on would-reject, re-show the edit slider with the plain-language reason and let the operator adjust + resubmit (proposal stays PENDING, no FAILED dead-end). Covers all reject reasons beyond the position-size cap the slider already clamps. Approach locked in 03-CONTEXT.md (D-62 follow-up).
  - User-editable max_position_pct in the strategy editor (sets the edit-size slider range).

### Phase 7: Operations & Observability

**Goal**: Gekko runs as a supervised service that survives reboots, network blips, OS sleep events, and clock drift — with external heartbeat, daily broker reconciliation, log rotation, and trading-calendar-aware scheduling that respects market hours and the IBKR daily reset window.
**Milestone:** v3.0
**Mode:** mvp
**Depends on**: Phase 6
**Requirements**: OPS-01, OPS-02, OPS-03, OPS-04, OPS-05, OPS-06, OPS-07, OPS-08, CADENCE-01, CADENCE-03, CADENCE-04
**Success Criteria** (what must be TRUE):

  1. Agent runs as launchd (macOS) or NSSM (Windows) supervised service, auto-restarts on crash with exponential backoff, and Slack-DMs the user when max-restart-count is reached — process can be killed manually and recovers without losing in-flight proposals or pending orders
  2. External heartbeat / dead-man-switch pings every 5 minutes; if the agent misses heartbeats for 15 minutes the user receives a Slack DM — silent failure is impossible during market hours
  3. macOS pmset is configured during install to prevent sleep during market hours; Windows Update active-hours are configured to avoid reboots during market hours; agent refuses to run if system clock is more than 1 second off NTP
  4. Agent runs daily reconciliation at market close, comparing internal trade/position state against each connected broker, and Slack-DMs the user on any discrepancy (extra orders, missing fills, position drift)
  5. Per-strategy cadence is configurable (scheduled open/midday/close, event-driven on news/earnings/price-gap, or continuous-with-cooldowns); scheduler is trading-calendar-aware (no runs on closed days, respects half-days) and survives process restarts via APScheduler SQLite job store

**Plans**: TBD

### Phase 8: Additional API Brokers (IBKR + Schwab)

**Goal**: User can connect IBKR and Schwab accounts via the same `Broker` abstraction as Alpaca, with Schwab's per-user OAuth onboarding handled through a guided flow and a 7-day refresh-token coordinator that proactively renews tokens (and Slack-DMs the user 24h before expiry if renewal fails) — preventing the silent every-7-day broker death.
**Milestone:** v3.0
**Mode:** mvp
**Depends on**: Phase 7
**Requirements**: BROK-I-01, BROK-I-02, BROK-I-03, BROK-I-04, BROK-S-01, BROK-S-02, BROK-S-03, BROK-S-04
**Success Criteria** (what must be TRUE):

  1. User can connect an IBKR account by running TWS or IB Gateway locally; agent supervises the Gateway side-process, halts trading when Gateway is down, and skips the IBKR 23:45-00:45 ET daily reset window — without losing pending proposals
  2. User completes per-user Schwab onboarding (registers their own app at developer.schwab.com, OAuth with PKCE, tokens encrypted at rest in SQLCipher) via a guided in-product flow; tokens never appear in logs
  3. Schwab 7-day refresh-token renewal happens proactively (well before expiry); if renewal fails, user receives a Slack DM 24h before expiry with a re-auth link — broker connections do not silently die
  4. IBKR and Schwab orders flow through the same OrderGuard pipeline as Alpaca (idempotency, universe whitelist, hard caps, qty×price sanity, env-credential pairing, kill switch) — no broker-specific safety bypasses
  5. User with positions across Alpaca + IBKR + Schwab sees a single per-strategy and aggregate portfolio view in the dashboard with broker-level breakdown; reconciliation runs across all three brokers daily

**Plans**: TBD

### Phase 9: Browser-Fallback Brokers & Deployment Packaging

**Goal**: User can connect Robinhood and Fidelity via `browser-use`-driven adapters (hardened deterministic flows, DOM signature checks, MFA-halts-to-HITL, screenshot evidence per action), each behind per-user feature flags — and the whole product ships as a one-command install on macOS and Windows with a first-run wizard that walks the user through SQLCipher passphrase, Slack workspace connection, and first broker setup.
**Milestone:** v3.0
**Mode:** mvp
**Depends on**: Phase 8
**Requirements**: BROK-R-01, BROK-R-02, BROK-R-03, BROK-R-04, BROK-R-05, BROK-R-06, BROK-R-07, BROK-F-01, BROK-F-02, BROK-F-03, BROK-F-04, BROK-F-05, BROK-F-06, DEPLOY-01, DEPLOY-02, DEPLOY-03, DEPLOY-04
**Success Criteria** (what must be TRUE):

  1. User can install Gekko on a fresh macOS Mac Mini or Windows machine with a single command (Homebrew tap / `pipx` on macOS; `scoop` / installer on Windows) and complete a first-run wizard that sets up SQLCipher passphrase, Slack workspace connection, and first broker (Alpaca paper recommended)
  2. User connects a Robinhood account (only after the in-product check confirms Robinhood's official Agentic Trading API is still unavailable for this user) via `browser-use` with a hardened deterministic flow; before-and-after screenshots are captured and persisted to audit for every action
  3. When Robinhood or Fidelity UI signature changes (DOM check fails), the adapter halts and escalates to HITL — never "tries something else"; when MFA is prompted, the agent halts and Slack-DMs the user to complete manually
  4. User can disable the Robinhood or Fidelity adapter per-user via a feature flag without restarting the service — broker fragility never blocks releases, and TOS-risk disclosure is presented and acknowledged during onboarding
  5. User upgrades Gekko via `pipx upgrade gekko` (or equivalent) and SQLite schema migrations run automatically without data loss; rollback to the previous version is documented

**Plans**: TBD
**UI hint**: yes

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation & Vertical Slice | v1.0 | 9/9 | Complete ✅ | 2026-06-15 |
| 2. OrderGuard & Real-Money Alpaca Live | v2.0 | 7/7 | Complete   | 2026-06-17 |
| 3. Production HITL UX | v2.0 | 15/15 | Complete ✅ (secured) | 2026-06-23 |
| 4. Agent Architecture & Cost Bounds | v2.0 | 8/8 | Complete ✅ (secured) | 2026-06-25 |
| 5. Trust Ladder | v2.0 | 5/5 | Complete    | 2026-06-26 |
| 6. Web Dashboard & Multi-User Auth | v3.0 | 0/0 | Not started | - |
| 7. Operations & Observability | v3.0 | 0/0 | Not started | - |
| 8. Additional API Brokers (IBKR + Schwab) | v3.0 | 0/0 | Not started | - |
| 9. Browser-Fallback Brokers & Deployment | v3.0 | 0/0 | Not started | - |

## Coverage Summary

**v1 requirements (now `milestones/v1.0-REQUIREMENTS.md`):** 78 total, 78 mapped, 78 delivered in v1.0 = all Phase 1 requirements (33 of 78). Phase 2-9 requirements (the remaining 45) re-bind to v2.0+.
**Phase count:** 9 (standard granularity stretched by one phase for safety sequencing)
**Mode:** Vertical MVP — each phase delivers an end-to-end user capability

---
*Roadmap created: 2026-06-08*
*v1.0 shipped: 2026-06-15 (Phase 1 archived; Phases 2-9 re-bound to v2.0+)*
