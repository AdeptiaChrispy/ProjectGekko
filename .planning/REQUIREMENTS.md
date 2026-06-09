# Requirements: Project Gekko

**Defined:** 2026-06-08
**Core Value:** A trustworthy autonomous agent that turns a plain-English investment thesis into actual, monitored trades on the user's own brokerage account — starting human-in-the-loop with small dollars and graduating to autonomy as trust is earned.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases (see Traceability).

### Strategy Definition & Management

- [ ] **STRAT-01**: User can author a strategy via natural-language chat ("I'm bullish on AI infra, max 5% per position, prefer dividend payers"), which the agent compiles into a structured strategy document
- [ ] **STRAT-02**: User can view and edit the resulting structured strategy via a form UI (risk tolerance, position sizing, sector preferences, watchlist tickers)
- [ ] **STRAT-03**: User can drop ad-hoc guidance during a run ("focus on energy this week", "avoid Chinese stocks for now") which the agent persists and injects into future research/decision cycles
- [x] **STRAT-04**: User can version strategies — every change creates a new version with diff visible
- [x] **STRAT-05**: User can run multiple named strategies in parallel (each with its own portfolio, caps, trust level)
- [x] **STRAT-06**: User can mark a strategy paper-mode-only or live-mode-eligible; flipping live requires an explicit confirmation step

### Research & Analysis

- [ ] **RES-01**: Agent can fetch price and quote data (Alpaca free IEX feed primary, `yahooquery` fallback)
- [ ] **RES-02**: Agent can fetch recent news for a ticker (Finnhub + Alpha Vantage free tiers)
- [ ] **RES-03**: Agent can fetch fundamentals from SEC EDGAR (10-K, 10-Q, 8-K filings)
- [ ] **RES-04**: Agent can perform open-ended web research using a sandboxed browser tool (Claude-for-Chrome / browser-use)
- [ ] **RES-05**: Agent's research turns are bounded per cycle (max ~12 tool calls / ~8K tokens) to prevent autoregressive drift
- [ ] **RES-06**: Research agent and decision agent have separated context (research output is summarized and passed to decision agent, never shared raw context)
- [ ] **RES-07**: All untrusted external content (news, SEC filings, web) is wrapped in delimiters and the source is allowlisted
- [x] **RES-08**: User-supplied guidance is stored as a structured record (timestamp, scope, expiry) and injected into the decision context, not lost in chat history

### Trade Execution & Safety (OrderGuard)

- [ ] **EXEC-01**: All money math uses `Decimal`; `float` is banned at the order-placement layer
- [ ] **EXEC-02**: Every order has a deterministic `client_order_id` derived from `(strategy_id, decision_id, side, qty, ticker)` to enforce broker-side idempotency
- [ ] **EXEC-03**: Order POSTs are never auto-retried — failures trigger a `query_existing_order(client_order_id)` check first to prevent duplicate orders (Knight Capital prevention)
- [ ] **EXEC-04**: All orders pass through a non-LLM OrderGuard layer that validates: universe whitelist (ticker is in user's allowed list), hard caps (size, daily loss, max trades/day, sector exposure), qty×price sanity check (within 2% of declared notional)
- [ ] **EXEC-05**: OrderGuard enforces paper-vs-live env-credential pairing — paper credentials cannot place live orders and vice versa
- [ ] **EXEC-06**: Kill switch — user can halt all trading for a strategy or globally with a single command (Slack `/gekko kill` or dashboard button)
- [ ] **EXEC-07**: Limit, market, and stop order types supported; orders default to limit with configurable slippage tolerance
- [ ] **EXEC-08**: Broker-rate-limit aware (per-broker token bucket + exponential backoff)
- [ ] **EXEC-09**: Wash-sale flagging — agent identifies trades that would create a wash sale within the user's connected accounts and surfaces this in the HITL card; agent does NOT block (user decides)
- [ ] **EXEC-10**: Market-hours and exchange-holiday awareness — agent only places orders during valid market windows (`pandas_market_calendars`)
- [ ] **EXEC-11**: PDT (pattern day trader) and T+1 settlement awareness — agent refuses trades that would violate these rules for the user's account type

### Human-in-the-Loop (HITL)

- [ ] **HITL-01**: Every proposed trade in a HITL strategy is sent as a Slack Block Kit card to the user with: ticker, company name, sector, action, size, rationale, supporting evidence summary, current quote, paper-vs-live indicator
- [ ] **HITL-02**: Slack buttons are idempotent — Slack's at-least-once delivery cannot cause double-execution
- [ ] **HITL-03**: HITL default is timeout = REJECT (configurable per strategy); proposals expire after 30 minutes (configurable)
- [ ] **HITL-04**: User can approve, reject, edit-size, or escalate-to-dashboard from a Slack card
- [ ] **HITL-05**: Quiet hours configurable per user — proposals outside the window are queued until window opens (no 2am pings)
- [ ] **HITL-06**: First live-money trade for any new strategy requires a separate-channel confirmation (Slack DM + dashboard confirmation, both required)

### Trust Ladder

- [ ] **TRUST-01**: Every strategy has a trust level: `propose-only` (HITL required) or `auto-within-caps` (executes within hard caps, surfaces decisions for review)
- [ ] **TRUST-02**: User can manually promote a strategy from `propose-only` to `auto-within-caps`; promotion requires explicit confirmation
- [ ] **TRUST-03**: Promotion is revocable at any time — flipping back to `propose-only` takes effect immediately
- [ ] **TRUST-04**: Portfolio-level caps in addition to per-strategy caps (max total exposure, max correlated-strategy exposure)
- [ ] **TRUST-05**: Capital scaling is a separate rung from autonomy promotion — a strategy can be auto-within-caps at $1K and require re-confirmation to scale capital
- [ ] **TRUST-06**: Anomaly detection — sudden drawdown beyond per-strategy threshold demotes the strategy to `propose-only` automatically and Slack-DMs the user

### Reporting

- [ ] **REPT-01**: Slack DM for: trade proposals (HITL), trade executions, daily P&L summary, errors and operational alerts
- [ ] **REPT-02**: Daily email digest — portfolio snapshot, day's trades with rationale, P&L, anomalies
- [ ] **REPT-03**: Weekly email digest — multi-day P&L, strategy attribution, rationale themes
- [x] **REPT-04**: Every trade execution generates a structured rationale record (thesis category, supporting evidence, confidence level, alternatives considered) persisted to the audit log
- [ ] **REPT-05**: User can export trade history as CSV (for tax software)

### Web Dashboard

- [ ] **DASH-01**: Dashboard shows current portfolio (positions, cost basis, current value, unrealized P&L) per strategy and aggregate
- [ ] **DASH-02**: Dashboard shows trade history with rationale, filterable by strategy / ticker / date
- [ ] **DASH-03**: Dashboard provides a strategy editor (chat onboarding + form fine-tuning)
- [ ] **DASH-04**: Dashboard provides a web-based approval fallback for HITL when Slack is unavailable
- [ ] **DASH-05**: Dashboard provides ad-hoc guidance entry ("look at energy this week")
- [ ] **DASH-06**: Dashboard provides paper-vs-live mode indicator on every page (red banner for live, green for paper)

### Broker — Alpaca (API)

- [ ] **BROK-A-01**: Connect to Alpaca paper account using API key + secret
- [ ] **BROK-A-02**: Connect to Alpaca live account using API key + secret (separate from paper key, enforced)
- [ ] **BROK-A-03**: Fetch positions, buying power, account status
- [ ] **BROK-A-04**: Place limit, market, stop orders with `client_order_id` idempotency
- [ ] **BROK-A-05**: Cancel pending orders
- [ ] **BROK-A-06**: Stream order updates via websocket (fill, partial fill, rejection)

### Broker — Interactive Brokers (API)

- [ ] **BROK-I-01**: Connect to IBKR via `ib_async` to a locally-running TWS or IB Gateway
- [ ] **BROK-I-02**: Supervisor restarts IB Gateway side-process on crash; agent halts when Gateway is down
- [ ] **BROK-I-03**: Implements the same `Broker` interface as Alpaca (positions, orders, cancellations, fills)
- [ ] **BROK-I-04**: Handles IBKR's 23:45-00:45 ET daily restart window — scheduler skips that window

### Broker — Schwab (API)

- [ ] **BROK-S-01**: Per-user Schwab developer-app onboarding flow (user registers their own app at developer.schwab.com)
- [ ] **BROK-S-02**: OAuth flow with PKCE; tokens encrypted at rest
- [ ] **BROK-S-03**: Proactive refresh-token renewal — Schwab refresh tokens expire at 7 days; agent renews automatically and Slack-DMs the user 24h before expiry if renewal fails
- [ ] **BROK-S-04**: Implements the same `Broker` interface (positions, orders, cancellations, fills)

### Broker — Robinhood (Browser-fallback)

- [ ] **BROK-R-01**: Re-validate Robinhood's official Agentic Trading API status before building browser adapter; if API path is viable, use it instead
- [ ] **BROK-R-02**: If browser-fallback is the path, use `browser-use` (Playwright-based) with a hardened deterministic flow per action
- [ ] **BROK-R-03**: Browser session credentials (cookies, MFA tokens) encrypted in SQLCipher; never logged
- [ ] **BROK-R-04**: DOM signature checks before each action — if Robinhood's UI changes shape, the adapter halts and escalates to HITL
- [ ] **BROK-R-05**: Screenshot evidence captured per action and persisted to the audit log
- [ ] **BROK-R-06**: User explicitly acknowledges Robinhood TOS implication (automation prohibited) during onboarding
- [ ] **BROK-R-07**: Feature-flagged per user — can disable Robinhood adapter without restart

### Broker — Fidelity (Browser-fallback)

- [ ] **BROK-F-01**: `browser-use` (Playwright-based) hardened deterministic flow per action
- [ ] **BROK-F-02**: Browser session credentials encrypted in SQLCipher; never logged
- [ ] **BROK-F-03**: DOM signature checks before each action; halts and escalates to HITL on UI change
- [ ] **BROK-F-04**: Screenshot evidence captured per action
- [ ] **BROK-F-05**: MFA flow handled — when Fidelity prompts MFA, agent halts and Slack-DMs the user to complete manually
- [ ] **BROK-F-06**: Feature-flagged per user

### Auth & Sessions

- [ ] **AUTH-01**: User signs into the dashboard via magic-link email (`fastapi-users` + custom magic-link strategy)
- [ ] **AUTH-02**: Sessions persist across browser refresh; configurable timeout (default 7 days)
- [x] **AUTH-03**: All broker credentials encrypted in SQLCipher; passphrase-on-start unlocks the DB (no env-var fallback) (Plan 01-03)
- [x] **AUTH-04**: Credentials never appear in logs, never enter LLM context (Plan 01-02)

### Cost Management

- [ ] **COST-01**: Per-user daily LLM cost ceiling configurable (default conservative — TBD in planning)
- [ ] **COST-02**: At 80% of daily cap, agent enters graceful-degradation mode — longer cadence, cheaper model (Haiku for triage), shorter context windows
- [ ] **COST-03**: At 100% of daily cap, agent hard-halts further LLM calls and Slack-DMs the user
- [ ] **COST-04**: Daily cost reset at user's configured time-zone midnight
- [ ] **COST-05**: Cost spend per strategy + per user is logged for review on the dashboard

### Audit Log

- [x] **AUDT-01**: Every decision, every order, every fill, every cap rejection, every kill-switch event is recorded as an append-only audit entry ✅ Plan 01-04
- [x] **AUDT-02**: Audit entries include: actor (user/system/agent), action, inputs, outputs, rationale, row hash chained to previous entry ✅ Plan 01-04
- [ ] **AUDT-03**: Audit log is browsable via the dashboard with filtering and search
- [ ] **AUDT-04**: Audit log is exportable as CSV for tax / personal-records use

### Operations & Reliability

- [ ] **OPS-01**: Agent runs as a supervised service — launchd on macOS, NSSM on Windows
- [ ] **OPS-02**: Auto-restart on crash with exponential backoff; max-restart-count alerts the user via Slack
- [ ] **OPS-03**: Heartbeat — external dead-man-switch pings every 5 minutes; user gets a Slack DM if heartbeat misses
- [ ] **OPS-04**: macOS pmset configured during install to prevent the Mac Mini from sleeping during market hours
- [ ] **OPS-05**: Windows Update active-hours configured during install
- [ ] **OPS-06**: Log rotation — structlog JSON output rotated nightly
- [ ] **OPS-07**: NTP drift check — agent refuses to run if system clock is more than 1 second off NTP
- [ ] **OPS-08**: Daily reconciliation — agent compares its internal state against each broker at market close and Slack-DMs discrepancies

### Cadence & Scheduling

- [ ] **CADENCE-01**: Per-strategy cadence configurable — scheduled (open/midday/close), event-driven (news, earnings, price moves), or continuous-with-cooldowns
- [ ] **CADENCE-02**: APScheduler with SQLite job store; survives process restarts
- [ ] **CADENCE-03**: Trading-calendar-aware — agent does not schedule research/decision runs outside market hours unless explicitly configured for after-hours analysis
- [ ] **CADENCE-04**: Event triggers: SEC filing alerts (EDGAR), news with high relevance score, price gap beyond threshold

### Regulatory Framing

- [ ] **REG-01**: All UI surfaces frame Gekko as "personal trade-execution tooling acting on your own authored strategy" — never as "investment advice"
- [ ] **REG-02**: First-run onboarding presents a user agreement that the user owns their strategy, tax consequences, and trade decisions
- [ ] **REG-03**: Each user runs their own isolated Gekko instance on their own hardware (Mac Mini or Windows machine); no shared multi-tenant runtime
- [ ] **REG-04**: No central performance dashboard across users; no copy-trading marketplace

### Deployment & Packaging

- [ ] **DEPLOY-01**: One-command install on macOS (Homebrew tap or `pipx`) sets up service, dashboard, scheduler, supervisor
- [ ] **DEPLOY-02**: One-command install on Windows (`scoop` or installer) does the same
- [ ] **DEPLOY-03**: First-run wizard walks the user through SQLCipher passphrase, Slack workspace connection, first broker connection (paper Alpaca recommended)
- [ ] **DEPLOY-04**: Upgrade is a `pipx upgrade gekko` (or equivalent) — automatic migrations for SQLite schema

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Differentiator Polish

- **RETRO-01**: Reasoning retrospective dashboard — hit-rate by thesis category, by sector, by holding period (requires structured rationale capture from v1)
- **RETRO-02**: Cross-broker portfolio aggregation view
- **RETRO-03**: Strategy attribution analysis (which strategies drove P&L)

### Sharing

- **SHARE-01**: Read-only strategy publishing (view-only, no auto-replicate) — users can browse other users' (consented) strategies and rationales

### Advanced Cadence

- **CAD-V2-01**: Pre-market and after-hours analysis windows (not trading) — agent does research/positioning prep before market open

### Backtesting

- **BACK-01**: Strategy backtest harness using historical OHLCV + news/sentiment data
- **BACK-02**: Honest transaction-cost and slippage modeling (no survivorship/look-ahead bias)

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Day-trading / sub-second loops | LLM inference latency makes this unrealistic; explodes cost |
| Options spreads / futures / forex | v1 focuses on US equities; derivatives are different risk profile |
| Crypto leverage / perpetuals | Different risk profile; punt to specialized tools |
| Public SaaS sign-ups | v1 is "me + a few" — each user runs their own isolated instance |
| Tax-form generation (1099, 8949) | Punt to user's tax software; agent provides CSV export |
| Wash-sale enforcement (auto-block) | Agent flags; user owns the tax decision |
| Mobile native app | Slack + email + web dashboard cover notifications |
| Push notifications | Slack DM is the notification path |
| Copy-trading marketplace | Regulatory tripwire; explicit non-goal |
| Auto-strategy generation (LLM proposes its own strategies) | Strategies are user-authored — keeps agent inside "personal tool" framing |
| Tick-streaming into LLM context | Cost-prohibitive; not needed for swing/long-term horizons |
| Shared multi-tenant runtime | Each user runs their own instance for regulatory and isolation reasons |
| Investment-advice framing | Hard regulatory line — Gekko is execution tooling, not advice |
| Real-money autonomous trading from day one | All strategies start `propose-only`; explicit promotion required |

## Traceability

Mapping of every v1 requirement to exactly one phase. Updated at roadmap creation; status updated as phases complete.

| Requirement | Phase | Status |
|-------------|-------|--------|
| STRAT-01 | Phase 1 | Pending |
| STRAT-02 | Phase 1 | Pending |
| STRAT-03 | Phase 1 | Pending |
| STRAT-04 | Phase 1 | Complete |
| STRAT-05 | Phase 1 | Complete |
| STRAT-06 | Phase 1 | Complete |
| RES-01 | Phase 1 | Pending |
| RES-02 | Phase 1 | Pending |
| RES-03 | Phase 1 | Pending |
| RES-04 | Phase 1 | Pending |
| RES-05 | Phase 1 | Pending |
| RES-06 | Phase 2 | Pending |
| RES-07 | Phase 2 | Pending |
| RES-08 | Phase 1 | Complete |
| EXEC-01 | Phase 1 | Pending |
| EXEC-02 | Phase 1 | Pending |
| EXEC-03 | Phase 2 | Pending |
| EXEC-04 | Phase 2 | Pending |
| EXEC-05 | Phase 2 | Pending |
| EXEC-06 | Phase 2 | Pending |
| EXEC-07 | Phase 1 | Pending |
| EXEC-08 | Phase 2 | Pending |
| EXEC-09 | Phase 2 | Pending |
| EXEC-10 | Phase 1 | Pending |
| EXEC-11 | Phase 2 | Pending |
| HITL-01 | Phase 1 | Pending |
| HITL-02 | Phase 3 | Pending |
| HITL-03 | Phase 3 | Pending |
| HITL-04 | Phase 1 | Pending |
| HITL-05 | Phase 3 | Pending |
| HITL-06 | Phase 2 | Pending |
| TRUST-01 | Phase 5 | Pending |
| TRUST-02 | Phase 5 | Pending |
| TRUST-03 | Phase 5 | Pending |
| TRUST-04 | Phase 5 | Pending |
| TRUST-05 | Phase 5 | Pending |
| TRUST-06 | Phase 5 | Pending |
| REPT-01 | Phase 3 | Pending |
| REPT-02 | Phase 6 | Pending |
| REPT-03 | Phase 6 | Pending |
| REPT-04 | Phase 1 | Complete |
| REPT-05 | Phase 6 | Pending |
| DASH-01 | Phase 6 | Pending |
| DASH-02 | Phase 6 | Pending |
| DASH-03 | Phase 6 | Pending |
| DASH-04 | Phase 3 | Pending |
| DASH-05 | Phase 6 | Pending |
| DASH-06 | Phase 6 | Pending |
| BROK-A-01 | Phase 1 | Pending |
| BROK-A-02 | Phase 2 | Pending |
| BROK-A-03 | Phase 1 | Pending |
| BROK-A-04 | Phase 1 | Pending |
| BROK-A-05 | Phase 1 | Pending |
| BROK-A-06 | Phase 1 | Pending |
| BROK-I-01 | Phase 8 | Pending |
| BROK-I-02 | Phase 8 | Pending |
| BROK-I-03 | Phase 8 | Pending |
| BROK-I-04 | Phase 8 | Pending |
| BROK-S-01 | Phase 8 | Pending |
| BROK-S-02 | Phase 8 | Pending |
| BROK-S-03 | Phase 8 | Pending |
| BROK-S-04 | Phase 8 | Pending |
| BROK-R-01 | Phase 9 | Pending |
| BROK-R-02 | Phase 9 | Pending |
| BROK-R-03 | Phase 9 | Pending |
| BROK-R-04 | Phase 9 | Pending |
| BROK-R-05 | Phase 9 | Pending |
| BROK-R-06 | Phase 9 | Pending |
| BROK-R-07 | Phase 9 | Pending |
| BROK-F-01 | Phase 9 | Pending |
| BROK-F-02 | Phase 9 | Pending |
| BROK-F-03 | Phase 9 | Pending |
| BROK-F-04 | Phase 9 | Pending |
| BROK-F-05 | Phase 9 | Pending |
| BROK-F-06 | Phase 9 | Pending |
| AUTH-01 | Phase 6 | Pending |
| AUTH-02 | Phase 6 | Pending |
| AUTH-03 | Phase 1 | Completed (Plan 01-03, 2026-06-08) |
| AUTH-04 | Phase 1 | Completed (Plan 01-02, 2026-06-08) |
| COST-01 | Phase 4 | Pending |
| COST-02 | Phase 4 | Pending |
| COST-03 | Phase 4 | Pending |
| COST-04 | Phase 4 | Pending |
| COST-05 | Phase 4 | Pending |
| AUDT-01 | Phase 1 | Complete (Plan 01-04) |
| AUDT-02 | Phase 1 | Complete (Plan 01-04) |
| AUDT-03 | Phase 6 | Pending |
| AUDT-04 | Phase 6 | Pending |
| OPS-01 | Phase 7 | Pending |
| OPS-02 | Phase 7 | Pending |
| OPS-03 | Phase 7 | Pending |
| OPS-04 | Phase 7 | Pending |
| OPS-05 | Phase 7 | Pending |
| OPS-06 | Phase 7 | Pending |
| OPS-07 | Phase 7 | Pending |
| OPS-08 | Phase 7 | Pending |
| CADENCE-01 | Phase 7 | Pending |
| CADENCE-02 | Phase 1 | Pending |
| CADENCE-03 | Phase 7 | Pending |
| CADENCE-04 | Phase 7 | Pending |
| REG-01 | Phase 1 | Pending |
| REG-02 | Phase 1 | Pending |
| REG-03 | Phase 1 | Pending |
| REG-04 | Phase 1 | Pending |
| DEPLOY-01 | Phase 9 | Pending |
| DEPLOY-02 | Phase 9 | Pending |
| DEPLOY-03 | Phase 9 | Pending |
| DEPLOY-04 | Phase 9 | Pending |

**Coverage:**
- v1 requirements: 108 total (note: original header count of 78 was incorrect — actual category-by-category count is 108)
- Mapped to phases: 108 (100%)
- Unmapped: 0

**Per-phase requirement counts:**
- Phase 1 (Foundation & Vertical Slice): 33
- Phase 2 (OrderGuard & Real-Money Alpaca Live): 11
- Phase 3 (Production HITL UX): 5
- Phase 4 (Agent Architecture & Cost Bounds): 5
- Phase 5 (Trust Ladder): 6
- Phase 6 (Web Dashboard & Multi-User Auth): 12
- Phase 7 (Operations & Observability): 11
- Phase 8 (Additional API Brokers): 8
- Phase 9 (Browser-Fallback Brokers & Deployment): 17

---
*Requirements defined: 2026-06-08*
*Last updated: 2026-06-08 after roadmap creation (traceability table populated; coverage 100%)*
