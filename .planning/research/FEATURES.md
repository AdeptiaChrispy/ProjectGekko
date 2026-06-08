# Feature Research

**Domain:** Autonomous Personal Stock Trading Agent (LLM-driven, HITL-first, multi-broker, multi-user)
**Researched:** 2026-06-08
**Confidence:** MEDIUM-HIGH (ecosystem patterns are well-documented; the *Claude-specific* HITL trade-approval UX is less precedented and rated MEDIUM)

---

## Orientation

This research surveys the feature landscape for personal/retail autonomous trading bots in 2026, mapped specifically against Project Gekko's scope:

- **Swing/long-horizon equities**, not day trading
- **LLM-reasoned** (Claude), not indicator-driven
- **HITL-first** with graduated autonomy
- **Self-hosted, multi-user** (me + a few trusted people)
- **Slack + Web Dashboard + Email** reporting (no native mobile)

Competitive reference set used to anchor "table stakes":

| Product | Category | What they do well |
|---------|----------|-------------------|
| **Composer.Trade** | No-code algo trading on Alpaca | Multi-strategy portfolio, paper→live, end-of-day batch execution, P&L dashboard |
| **Capitalise.ai** | Natural-language → strategy | Plain-English strategy authoring, multi-broker connectors |
| **VibeTrader / Tuplemint** | "Vibe coding" trading bots | NL strategy intake, AI-generated execution logic |
| **IBKR IBot** | Conversational trade execution | Natural-language order placement, account Q&A inside a broker UX |
| **TradingAgents / FinRobot / FinGPT** | LLM research frameworks (open source) | Multi-agent debate, EDGAR ingestion, reasoning traces |
| **TradersPost / TrendSpider** | Alert→execution glue | Webhook receivers, alert-to-order plumbing |
| **eToro / STARTRADER copy** | Social/copy trading | Strategy following with risk-scaled replication |

Gekko's white-space relative to these: **LLM-reasoned trade rationale + Slack-native HITL approval + multi-user-with-isolation + browser-fallback for Robinhood/Fidelity**. No competitor combines all four. Composer is closest but is indicator-driven and not LLM-reasoned; TradingAgents is LLM-reasoned but is research-only with no execution layer; IBKR's IBot is conversational but locked to one broker.

---

## Feature Landscape

> Categorization rule used throughout: **Table stakes** are features whose absence will make Chris (or any of the "few trusted users") lose trust in the product — almost everything safety- and audit-related lives here for a real-money product. **Differentiators** are features that make Gekko meaningfully better than a hand-written Python script + cron. **Anti-features** are things that look tempting but are explicit "no" for v1.

### Table Stakes (Users Expect These — Missing = Untrustworthy for Real Money)

#### Strategy Definition & Management

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Plain-English strategy intake via chat | Project's core value proposition; PROJECT.md "Active" requirement | MEDIUM | LLM extracts a structured strategy document (thesis, universe, sizing, constraints) and stores it as the canonical source-of-truth; the chat transcript is preserved as provenance |
| Structured form to tune the parsed strategy | Chat is fuzzy; form gives precision over caps, sectors, watchlists | MEDIUM | The form edits the *same* structured document the chat produced — never two parallel sources of truth |
| Per-strategy hard caps (position size, daily loss, max trades/day, sector exposure) | PROJECT.md explicit requirement; safety floor for any real-money system | MEDIUM | Caps must be **enforced at the execution layer**, not just at the proposal layer — the agent must not be able to talk itself past them |
| Paper-trading mode (forced default before real money) | Industry-standard "must paper-trade first" pattern; Alpaca/IBKR offer native paper accounts | MEDIUM | For browser-fallback brokers (Robinhood/Fidelity), simulate execution against last-known quotes — clearly badge as "simulated, not broker-confirmed" |
| Version history of strategies | Users will tweak strategies constantly; need to see what changed and roll back | LOW | Strategy doc is just YAML/JSON in git or DB rows with `valid_from`/`valid_to` |
| Multi-strategy per user with capital allocation | Composer and every multi-strategy bot supports this; users will want "AI infra thesis" + "dividend value" in parallel | MEDIUM | Each strategy gets its own bucket of capital; portfolio-level caps still apply across strategies |
| Ad-hoc guidance injection ("focus on energy this week") | PROJECT.md explicit requirement | LOW-MEDIUM | Stored as an in-effect "directive" attached to the strategy with an expiry timestamp; surfaces in the agent's research prompt context |

#### Research & Analysis

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Price/quote data ingestion (intraday + historical) | Cannot reason about a trade without it | LOW | Alpaca's free feed is sufficient for swing horizons; Yahoo unofficial as backup |
| News & sentiment ingestion (per-ticker) | Every LLM trading framework treats this as a first-class input | MEDIUM | Finnhub / Alpha Vantage free tiers; cache aggressively to avoid blowing rate limits |
| Earnings calendar awareness | Users expect the agent to know "earnings tomorrow" — avoiding or sizing into earnings is table stakes | LOW | Free from Finnhub/Alpha Vantage; surface in proposal cards |
| SEC EDGAR fundamentals (10-K, 10-Q, 8-K) | FinRobot and FinGPT all treat this as core; "the agent didn't even read the 10-K" is a credibility killer for an LLM agent | MEDIUM | EDGAR's JSON API is free; budget Claude tokens carefully — full 10-Ks are huge, use targeted retrieval |
| Per-trade rationale ("why did the agent do this?") | This is the #1 thing that makes an LLM agent trustworthy over a black-box ML model | LOW | The LLM is already reasoning; just persist the chain-of-thought as a structured artifact attached to each proposal/order |
| Watchlist support per strategy | Universe-defining; every product has this | LOW | User-curated + strategy-implied (e.g., "all S&P energy") |

#### Trade Execution & Safety

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Propose-only mode (default for new strategies) | PROJECT.md core requirement; this is the whole HITL posture | MEDIUM | Proposals time out if not approved (e.g., 30 min) so a stale proposal doesn't fire next morning at a different price |
| Pre-trade preview (size, price, est. cost, current exposure after) | Industry-standard; users won't approve a trade they can't see | LOW | Block Kit card for Slack + matching dashboard card |
| Position size cap enforcement (hard) | Safety floor — no exceptions | LOW | Computed at execution time against live portfolio, not against proposal time snapshot |
| Daily loss cap with circuit-breaker behavior | Industry-standard ("kill at -2% to -5% of equity"); critical for autonomy | MEDIUM | Tiered response: tier 1 = no new entries; tier 2 = require approval for everything; tier 3 = full halt for the day |
| Max-trades-per-day cap | Prevents runaway-loop scenarios (a real failure mode for LLM agents) | LOW | Counter resets at market open |
| Sector / concentration exposure cap | PROJECT.md explicit requirement | MEDIUM | Requires a sector classifier (GICS via free data sources) |
| Market-hours awareness (don't propose at 3am; don't try to fill when closed) | Embarrassing failure mode if absent | LOW | NYSE calendar + half-day handling; `pandas_market_calendars` solves this |
| Holiday calendar awareness | Same as above; trade calendar is *not* the same as US federal holidays | LOW | Use exchange calendar, not federal |
| Limit / market / stop order types | Every broker supports them; missing = product feels primitive | LOW | Default to limit-with-slippage-tolerance; market only when explicitly requested |
| Slippage tolerance on limit orders | Without this, limits sit unfilled in fast markets | LOW | Config per-strategy (e.g., 0.25%); fall back to a price-improved market order if user opted in |
| Order cancellation (manual + auto on timeout) | Stale unfilled orders are a real problem | LOW | Auto-cancel any limit unfilled at session close |
| Broker rate-limit handling (exponential backoff + jitter, respect `Retry-After`) | Hitting Alpaca's 50 RPM trade-API limit will degrade UX visibly | LOW | Alpaca SDK does this natively; IBKR/Schwab need hand-rolled |
| Kill switch (human-triggered global halt) | Industry-standard; the "oh no" button | LOW | Big red button on dashboard + `/gekko halt` Slack command; flips every strategy to halted, cancels open orders |

#### Reporting & Communication

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Slack DM for trade proposals (Block Kit card with approve/reject buttons) | PROJECT.md explicit requirement; native channel for Chris | MEDIUM | Block Kit interactive messages with response_url; well-documented pattern |
| Slack DM for execution confirmations (fill price, slippage vs proposal, post-trade exposure) | Closes the loop on every approval | LOW | Same Block Kit infrastructure |
| Daily P&L summary (Slack + email digest) | PROJECT.md explicit requirement | LOW | End-of-day batch; include per-strategy breakdown |
| Web dashboard: portfolio view + open positions + recent trades | Slack is firehose-y; dashboard is the "review" surface | MEDIUM | Read-mostly; the heavy interactions stay in Slack |
| Web dashboard: strategy editor | Form-based tuning of strategy parameters | MEDIUM | |
| Web dashboard: trade history with per-trade rationale | "Why did the agent buy this?" must be one click away | MEDIUM | The persisted reasoning artifact from the research phase, rendered |
| Email digest (daily + weekly) | Async fallback for when Chris isn't watching Slack | LOW | Daily P&L + open positions; weekly perf vs benchmark + cap utilization |
| Anomaly alerts (sudden loss, error, broker disconnect, API failure) | Industry-standard for any always-on system | MEDIUM | Alert routes: critical→Slack DM immediately; warning→batched into next digest |

#### HITL / Trust Ladder

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Trade proposal card with approve / reject / edit (size, price) | Industry-standard approval-bot pattern | MEDIUM | Slack Block Kit with three buttons; "edit" opens a modal |
| Per-strategy trust level (propose-only vs auto-execute-within-caps) | PROJECT.md explicit requirement; the whole graduation premise | MEDIUM | Stored on the strategy; flipping is a deliberate user action with a confirm-step |
| Approval timeout (proposals auto-reject after N minutes) | Prevents a 9am proposal firing at 3pm at a stale price | LOW | Per-strategy configurable; default 30 min |
| Per-trade override even on auto-execute strategies | "I let it auto-trade but want to veto *this one*" — must be possible | LOW | Slack DM lands with a "veto" button while order is pending |
| Audit log of every proposal, approval, rejection, execution, edit | Real money + multi-user = real audit needs | MEDIUM | Append-only log table; persist the LLM reasoning artifact too |

#### Multi-User & Sharing

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Per-user broker credentials (encrypted at rest) | PROJECT.md explicit requirement; non-negotiable | MEDIUM | Each user's keys encrypted with a user-specific KEK; broker creds never logged |
| Per-user portfolios and strategies | Tenant isolation | MEDIUM | Row-level tenancy; every query scoped by `user_id` |
| Per-user Slack DM routing | Each user gets their own DM channel; their alerts never reach another user | LOW | One Slack bot, per-user user_id mapping in the user record |
| Per-user web dashboard (login + session) | Standard auth | MEDIUM | OIDC against a single IdP (e.g., Google) for the small group is simpler than rolling auth |

#### Compliance & Accountability

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Append-only audit log of trades + reasoning | Real-money + small-group sharing = need to be able to reconstruct any decision | MEDIUM | Includes proposal time, model version, prompt context hash, rationale, approval action |
| Exportable trade history (CSV / TXF for tax software) | Users will need this for taxes regardless of broker 1099s | LOW | Broker already produces 1099s; Gekko's CSV is the *agent-side* record (what it intended, what it did) |
| Wash-sale **flagging** (warn before placing) | PROJECT.md scopes wash-sale handling to flagging only — agent should not *cause* wash sales it could have avoided | MEDIUM | Track per-user closed-loss positions for 30 days; warn if proposing a "substantially identical" buy. NOTE: only flags within Gekko's view — cross-account wash sales remain user's responsibility (industry-standard limitation) |
| Market-hours guard (don't transmit orders outside session) | Embarrassing if missing | LOW | Same calendar as above |

#### Operations & Reliability

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Heartbeat / liveness indicator ("agent is alive") | Always-on systems need a "yes it's still up" signal | LOW | Last-heartbeat-at timestamp on dashboard; Slack daily "I'm alive" digest |
| Broker connection health monitoring + alerts | Lost broker connection = silent failure = worst case | MEDIUM | Periodic ping; on failure, halt new orders, alert user, retry with backoff |
| Retry with exponential backoff + jitter for transient failures | Industry-standard | LOW | Wrap every broker call |
| Graceful degradation on Claude API budget pressure | PROJECT.md explicit constraint (per-user $/day ceiling) | MEDIUM | Below 80% of budget: full research depth. 80-100%: shallow research, longer cadence. >100%: propose-only, no new research initiated |
| Persistent state across restarts | Agent restarts must not lose open proposals or pending orders | LOW | Just don't hold state in memory only |

---

### Differentiators (Where Gekko Beats a Hand-Written Script)

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Conversational strategy authoring + ongoing steering** | Capitalise.ai does NL intake but doesn't have an "ongoing chat" surface — Gekko's Slack DM IS the steering channel, so "focus on energy this week" is just a Slack message | MEDIUM | Differentiates from form-only competitors; the chat is *always-on*, not one-time onboarding |
| **LLM-narrated trade rationale (not just indicators triggered)** | A Composer/TrendSpider trade says "RSI<30 + MA crossover". A Gekko trade says "AVGO's Q4 hyperscaler capex commentary plus the AMD-Cisco partnership announced Tuesday make this a candidate for the AI-infra thesis, sized at 3% per the cap" — the rationale is the differentiator | MEDIUM | The reasoning is already happening for the trade; persisting and rendering it is the differentiator |
| **Browser-fallback for non-API brokers (Robinhood, Fidelity) via Claude-for-Chrome** | Composer is Alpaca-only. TradersPost adds more but still excludes Robinhood/Fidelity. Gekko *includes the brokers most retail users actually have* | HIGH | Fragile but unlocks the audience; PROJECT.md flags this — keep as second-class |
| **Slack-native HITL approval flow** | Most competitors push everything to a web dashboard. Gekko fits where Chris already lives (Slack), and Block Kit is mature enough to make this nice | MEDIUM | Approve/reject/edit in one DM; mobile-by-accident (via Slack mobile) without building a native app |
| **Per-strategy graduated trust ladder with explicit promotion** | Most bots are "on or off". Trust-ladder framing (propose → auto-within-caps → autonomous-within-wider-caps) maps to how a real human would delegate to a junior trader | MEDIUM | Worth a dedicated phase per PROJECT.md key decisions |
| **Strategy-as-document with versioned diffs** | Most competitors edit strategies in-place. Treating the parsed strategy as a versioned doc means "what changed between Tuesday and Thursday?" is one click — this matters for "I added a constraint and trade quality dropped, did the constraint cause it?" | LOW-MEDIUM | Just version it like code |
| **Cost-aware degradation (LLM budget ceiling per user/day)** | Hardly any competitor talks about this — most assume infinite LLM budget. PROJECT.md treats it as a first-class constraint | MEDIUM | Built-in budget meter; agent self-throttles |
| **Cross-broker portfolio view (when multiple brokers per user)** | Most bots are single-broker. Gekko aggregating across Alpaca + Schwab + Robinhood is a real user benefit | MEDIUM | Defer to v1.x unless Chris explicitly has positions across brokers from day one |
| **Reasoning-vs-outcome retrospective ("the agent thought X, then Y happened")** | The LLM rationale is a hypothesis. Closing the loop with what actually happened over the next 1d/5d/30d is uniquely possible because the rationale is persisted | MEDIUM | Cohort by reasoning category and look at hit rates — this becomes a strategy-improvement loop |

---

### Anti-Features (Things to Deliberately NOT Build)

| Feature | Why It Looks Tempting | Why It's a Bad Idea for Gekko | What to Do Instead |
|---------|----------------------|------------------------------|---------------------|
| Sub-second / day-trading execution loops | "Real trading bots scalp!" | PROJECT.md out-of-scope; Claude latency makes this impossible; cost blows up; and HITL UX collapses at this speed | Optimize for swing/long-horizon; cadence in minutes-to-hours |
| Options spreads, futures, complex derivatives | Many products support them | PROJECT.md out-of-scope; each adds a 10x compliance/explanation surface; brokers' option APIs differ wildly | US equities only for v1; revisit if a user actually asks |
| Crypto leverage / perp futures | Crypto trading is "trendy" | Out of scope (max risk: liquidation cascade in 30 sec while user is asleep); margin call failure mode is incompatible with HITL | If a user wants crypto, Alpaca spot crypto only, same caps |
| Public sign-up / SaaS billing | Natural progression idea | PROJECT.md out-of-scope; turns this from a personal tool into a regulated entity overnight | Stay friends-and-family; manual user provisioning |
| Tax form generation / wash-sale **enforcement** | Users will ask for it | PROJECT.md scopes out (correctly); tax software exists and is regulated; we have only an in-app view, not cross-account | Flag only; export CSV; punt to user's tax software |
| Mobile native app | Always-tempting on always-on products | PROJECT.md out-of-scope; Slack mobile covers 95% of the need for free | Slack DM + responsive web dashboard |
| Copy-trading / public strategy marketplace | eToro showed there's a market | Adds a regulatory surface (signal providers are arguably investment advisers); social mechanics turn the product into a different beast | Read-only sharing of *your own* strategy with *named* other users in your group only — not a public marketplace |
| Automatic strategy generation ("AI picks strategies for you") | Easy LLM party trick | Antithetical to the project's core: the *human's* thesis is the input. Replacing it with an AI thesis defeats the trust model | Agent helps refine/sharpen a user-authored thesis, never originates one |
| Real-time everything (streaming price ticks into LLM) | "AI sees the market live!" | Cost-explodes; LLMs are not good at tick-by-tick; the strategy horizon is swing/long, not ticks | Polled snapshots on cadence; event-triggered re-evaluation on news/earnings/price-move thresholds |
| Backtester / strategy simulator | Every algo tool has one | LLM-reasoned strategies are notoriously hard to backtest (the LLM didn't read 2019 news in 2019 context); fake backtests can be worse than none | Forward-test via paper trading. Defer rigorous backtester to v2+ when there's user demand |
| In-product investment "advice" framing | Easy UX shortcut | PROJECT.md regulatory constraint — agent must not give investment advice in a regulated sense | Frame consistently as "executes your strategy" and "presents research"; rationale text avoids prescriptive "you should buy" language |

---

## Feature Dependencies

```
[Plain-English strategy intake] (P1)
        |
        +--produces--> [Structured strategy document] (P1)
                            |
                            +--edited by--> [Form-based tuning] (P1)
                            +--steered by--> [Ad-hoc guidance directives] (P1)
                            +--versioned by--> [Strategy version history] (P1)

[Research pipeline] (P1)
        |
        +--reads--> [Structured strategy document]
        +--ingests--> [Price data] [News/sentiment] [Earnings calendar] [SEC EDGAR]
        +--produces--> [Trade proposal + rationale artifact] (P1)
                            |
                            +--enforced against--> [Hard caps: size, daily loss, sector, max trades] (P1)
                            +--gated by--> [Wash-sale flag check] (P1)
                            +--gated by--> [Market-hours guard] (P1)

[Trade proposal] (P1)
        |
        +--routed to--> [Slack DM approval card] (P1)
        +--routed to--> [Web dashboard proposal queue] (P1)
        |
        +--awaits--> [User decision: approve / reject / edit] (P1)
                            |
                            +--writes to--> [Audit log] (P1)
                            |
                            +--on approve--> [Order placement] (P1)
                                                    |
                                                    +--writes to--> [Audit log]
                                                    +--monitored by--> [Broker connection health] (P1)
                                                    +--retries via--> [Backoff + Retry-After handling] (P1)
                                                    +--confirms via--> [Slack execution confirmation] (P1)

[Per-strategy trust level: propose-only | auto-within-caps] (P1)
        |
        +--changes behavior of--> [Trade proposal flow]
            (auto-within-caps skips the approval gate, BUT all hard caps still enforce)

[Heartbeat + Operational liveness] (P1)
        |
        +--surfaces in--> [Dashboard] [Daily Slack digest]

[Multi-user isolation] (P1)
        |
        +--scopes--> [Strategies] [Portfolios] [Broker creds] [Slack DM routing] [Audit log] [Dashboard sessions]
        +--required by--> EVERYTHING (this is foundational)

[Browser-fallback brokers (Robinhood / Fidelity)] (P2)
        |
        +--alternate execution path for--> [Order placement]
        +--simulates execution for--> [Paper mode] (limitation: no real broker paper account for these)

[Cross-broker portfolio aggregation] (P2)
        +--requires--> [Multiple broker connections per user]

[Reasoning retrospective / hit-rate cohorts] (P2)
        +--requires--> [Audit log] [Persisted rationale artifacts] [Time-series outcomes]

[Email digest] (P1)
        +--enhances--> [Slack DM reporting] (provides async fallback)

[Anti-feature: Copy/follow strategy] (NOT BUILT)
        +--conflicts with--> [Regulatory posture in PROJECT.md]
```

### Critical Dependency Notes

- **Multi-user isolation is foundational.** Cannot be retrofitted. Every data model and every Slack/dashboard route must be tenant-scoped from day one. Trying to bolt it on after v1 is launched is the classic disaster.
- **HITL approval depends on Slack Block Kit + a web dashboard fallback.** Either path alone is insufficient: Slack down = no way to approve; web-only = no real-time push to the user.
- **Auto-execute-within-caps depends on cap enforcement being airtight at the execution layer.** If caps are only checked at proposal time, an LLM that talks itself past a cap will succeed. Caps must re-evaluate at order-placement time against live portfolio.
- **Wash-sale flagging depends on persisting closed-loss positions per user.** Cannot flag what you don't remember.
- **The reasoning retrospective differentiator depends on rationale being a structured artifact from day one.** If rationale starts as free-text in a Slack message, you can't cohort by it later. Persist it as structured data (thesis category, factors cited, confidence, model version) from the start.
- **Cost-aware degradation depends on per-call LLM accounting.** Must be plumbed through the agent loop from the start; cannot be retrofitted without re-instrumenting every Claude call.

---

## MVP Definition

### Launch With (v1) — Single-User Real-Money-Ready Slice

The ruthlessly-minimum v1 is: **one user (Chris), one strategy, one broker (Alpaca), HITL-only, paper mode → small live**.

- [ ] Plain-English strategy intake (chat) → structured strategy doc — *core value*
- [ ] Form-based tuning of the structured doc — *because chat alone is fuzzy*
- [ ] Ad-hoc guidance directives — *PROJECT.md requirement*
- [ ] Strategy version history (basic) — *trivial to add, painful to retrofit*
- [ ] Alpaca broker integration (paper account first, then live) — *API path is fastest and safest broker*
- [ ] Research pipeline: price + news + earnings calendar + SEC EDGAR — *minimum to justify the LLM*
- [ ] Trade proposal with per-trade rationale artifact — *the whole point of LLM trading vs indicators*
- [ ] Slack DM proposal card with approve / reject / edit — *Chris's native channel*
- [ ] Hard caps: position size, daily loss, max trades/day, sector exposure — *real-money safety floor*
- [ ] Market-hours + holiday calendar awareness — *embarrassing to miss*
- [ ] Limit / market / stop order types + slippage tolerance — *broker hygiene*
- [ ] Broker rate-limit handling (backoff + `Retry-After`) — *will hit Alpaca's 50 RPM otherwise*
- [ ] Wash-sale flagging (within-account) — *required for real money*
- [ ] Audit log (append-only) of proposals, approvals, executions, with reasoning artifact — *non-negotiable for real money*
- [ ] Paper-trading mode (default for all new strategies) — *industry standard*
- [ ] Per-strategy trust level (propose-only | auto-within-caps) — *PROJECT.md requirement; trust ladder is its own phase per Key Decisions*
- [ ] Heartbeat / liveness indicator — *always-on system table stakes*
- [ ] Broker disconnection detection + halt-on-disconnect — *silent failure prevention*
- [ ] Kill switch (`/gekko halt` Slack command + dashboard button) — *the "oh no" button*
- [ ] Multi-user data model and isolation from day one — *foundational; do not retrofit*
- [ ] Slack DM execution confirmations — *closes the loop*
- [ ] Daily P&L summary (Slack + email digest) — *PROJECT.md requirement*
- [ ] Basic web dashboard (portfolio view, trade history with rationale, strategy editor) — *the review surface*
- [ ] Per-user authentication for dashboard (OIDC) — *required even at small scale*
- [ ] Per-user LLM budget ceiling + graceful degradation — *PROJECT.md constraint*
- [ ] Exportable trade history (CSV) — *user-side tax record*

### Add After Validation (v1.x) — Once v1 Is Stable and Trusted

- [ ] Additional broker: IBKR (API) — *trigger: Chris or a user has assets at IBKR*
- [ ] Additional broker: Schwab (API) — *trigger: Chris or a user has assets at Schwab*
- [ ] Browser-fallback broker: Robinhood (Claude-for-Chrome) — *trigger: a user needs it; PROJECT.md flags fragility*
- [ ] Browser-fallback broker: Fidelity (Claude-for-Chrome) — *trigger: a user needs it*
- [ ] Cross-broker portfolio aggregation view — *trigger: multiple brokers actually connected*
- [ ] Multiple strategies per user with per-strategy capital allocation — *trigger: Chris wants to run "AI infra" + "dividend value" in parallel*
- [ ] Reasoning retrospective dashboard (hit rates by thesis category) — *trigger: ~3 months of trade history exists to analyze*
- [ ] Weekly performance email (vs benchmark) — *trigger: monthly is too coarse, daily is too noisy*
- [ ] Anomaly alerts beyond connection loss (sudden drawdown, unusual order rate, sentiment spike on holding) — *trigger: any single near-miss in v1*
- [ ] Read-only strategy sharing with named other users in the group — *trigger: a user asks "can I see what Chris's strategy is doing?"*
- [ ] Event-driven cadence triggers (earnings tomorrow, news event) layered on top of scheduled cadence — *trigger: scheduled-only misses important moments*

### Future Consideration (v2+) — Defer Until Real Demand

- [ ] Strategy backtester (LLM-reasoned, with leakage controls) — *only if users repeatedly ask and we figure out how to do this honestly*
- [ ] Options as a first-class strategy — *PROJECT.md out-of-scope for v1; revisit only with explicit user demand and a compliance pass*
- [ ] Spot crypto via Alpaca — *PROJECT.md says "if it falls out naturally"; only if a user wants it*
- [ ] Mobile native app — *unlikely to ever clear the bar; Slack mobile is sufficient*
- [ ] Public sign-ups / SaaS billing — *EXPLICITLY anti-feature; do not consider*

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Plain-English strategy intake | HIGH | MEDIUM | P1 |
| Hard caps enforcement (execution layer) | HIGH | MEDIUM | P1 |
| Slack DM proposal cards (Block Kit) | HIGH | MEDIUM | P1 |
| Per-trade LLM rationale artifact (persisted) | HIGH | LOW | P1 |
| SEC EDGAR fundamentals ingestion | MEDIUM | MEDIUM | P1 |
| Audit log (append-only) | HIGH (for trust) | MEDIUM | P1 |
| Multi-user data isolation | HIGH (foundational) | MEDIUM | P1 |
| Paper-trading mode | HIGH | MEDIUM | P1 |
| Per-strategy trust level | HIGH | MEDIUM | P1 |
| Kill switch | HIGH | LOW | P1 |
| Market-hours / holiday calendar | MEDIUM (table stakes) | LOW | P1 |
| Wash-sale flagging | MEDIUM | MEDIUM | P1 |
| Cost-aware LLM degradation | MEDIUM | MEDIUM | P1 |
| Heartbeat / liveness | MEDIUM | LOW | P1 |
| Web dashboard (basic) | HIGH | MEDIUM | P1 |
| Email digest (daily) | MEDIUM | LOW | P1 |
| IBKR API integration | MEDIUM | MEDIUM | P2 |
| Schwab API integration | MEDIUM | MEDIUM | P2 |
| Robinhood browser-fallback | MEDIUM | HIGH | P2 |
| Fidelity browser-fallback | MEDIUM | HIGH | P2 |
| Reasoning retrospective dashboard | HIGH (long-term) | MEDIUM | P2 |
| Cross-broker portfolio aggregation | MEDIUM | MEDIUM | P2 |
| Multi-strategy per user | MEDIUM | MEDIUM | P2 |
| Event-driven cadence triggers | MEDIUM | MEDIUM | P2 |
| Read-only strategy sharing | LOW | LOW | P2 |
| Strategy backtester | MEDIUM | HIGH | P3 |
| Options as first-class | LOW | HIGH | P3 (or never) |
| Mobile native app | LOW | HIGH | P3 (likely never) |

---

## Competitor Feature Analysis

| Feature | Composer.Trade | Capitalise.ai | TradingAgents (research) | IBKR IBot | **Gekko's Approach** |
|---------|----------------|---------------|--------------------------|-----------|----------------------|
| Strategy specification | Visual flow editor | Plain-English NL | Per-agent role prompts | Plain-English commands | **Plain-English chat → structured doc → form tuning + ongoing guidance** |
| Reasoning per trade | None (indicator-triggered) | Limited | Detailed (multi-agent debate) | None | **Persisted LLM rationale as structured artifact, viewable in dashboard** |
| Broker support | Alpaca only | Multi-broker via integrations | None (research-only) | IBKR only | **Alpaca + IBKR + Schwab via API; Robinhood + Fidelity via Claude-for-Chrome** |
| HITL approval | None (auto-exec) | Limited | N/A | N/A (direct trades) | **Slack-native approve/reject/edit with proposal timeout** |
| Paper-trading mode | Yes | Yes | Simulation only | Yes | **Yes, default for new strategies; simulated for browser-fallback** |
| Hard caps (size, daily loss, sector) | Position limits only | Yes | N/A | Manual | **All four, enforced at execution layer** |
| Audit / reasoning log | Trade log only | Trade log only | Reasoning trace exposed | Order audit | **Append-only log with persisted rationale, exportable** |
| Multi-strategy with allocation | Yes | Yes | N/A | N/A | **Yes, v1.x — single strategy in v1** |
| Multi-user with isolation | Single-user (account per user) | Single-user | N/A | Single-user | **Multi-user with per-user broker creds, portfolios, dashboards from day one** |
| Notification channels | Email + in-app | Email + Telegram | N/A | Email | **Slack DM (primary) + email digest + web dashboard** |
| Wash-sale awareness | None | None | N/A | Broker-handled | **Flag (warn before placing); not enforced; cross-account is user's responsibility** |
| Cost model | $30/mo flat | $40-200/mo | N/A (self-hosted) | Bundled | **Self-hosted; per-user Claude API budget ceiling** |

**Where Gekko intentionally diverges:**

- **Reasoning as a first-class persisted artifact** — competitors either don't have it (indicator-driven) or expose it as ephemeral chat (TradingAgents). Persisting it as structured data unlocks the long-term retrospective differentiator.
- **Slack-native HITL** — no competitor does this seriously; most rely on a web dashboard the user has to remember to open. Slack DM is push-by-default.
- **Multi-user with isolation, but private** — Composer/Capitalise are SaaS multi-tenant; TradingAgents is single-user self-hosted. Gekko is self-hosted multi-user-for-friends, which is a small but distinct niche that maps to PROJECT.md's "me + a few trusted people".
- **Browser-fallback for Robinhood/Fidelity** — nobody else does this seriously because it's fragile. Gekko's bet is that the LLM agent + Claude-for-Chrome makes it tractable for the slow swing-trading cadence we're targeting.

---

## Discussion Points / Push-Back for Chris

Per `feedback_challenge_decisions.md` and `feedback_raise_discussion_points.md`, surfacing items worth your input before the roadmap baseline locks:

1. **"Not investment advice" vs. "trade proposal with rationale" tension.** PROJECT.md's regulatory posture says the agent "must not give investment advice in a regulated sense." But a system that *proposes specific trades with reasoning* is, in plain English, advice. The defensible position is probably "this is a personal-use execution tool; the user authored the strategy; the agent is doing research and execution that the user themselves would otherwise do." Worth being deliberate about (a) language in the UI (avoid "you should buy", prefer "this matches your strategy"), and (b) the user agreement when shared with others. Want to flag for the trust-ladder phase, not block v1.

2. **Wash-sale "flag only" has a sharper edge worth surfacing.** The PROJECT.md scopes wash-sale enforcement out and flagging in. But there's a third thing: should the agent *avoid causing wash sales it could have avoided* without being asked? My read: yes, the agent should default to "don't propose a buy that triggers a wash sale" within its visibility, and only override with explicit user override per proposal. That's stronger than "flag" but weaker than "enforce tax compliance." Worth deciding the default.

3. **The trust ladder needs a concrete promotion criterion.** PROJECT.md lists trust-ladder design as its own phase, which is right. But for v1 to be useful, we need a *placeholder* criterion. Suggestion: "auto-execute-within-caps can be enabled per strategy after N successful HITL approvals with no losses exceeding cap." Otherwise users will toggle the flag the first day and the ladder becomes decorative.

4. **Browser-fallback brokers are a v2 risk masquerading as v1 scope.** PROJECT.md treats Robinhood/Fidelity as core but flags fragility. My honest read: this is at least a phase of work on its own, not a feature on top of Alpaca. Strongly recommend P2 (after Alpaca live), and clear messaging that browser-driven brokers have a different reliability profile (e.g., paper mode is simulated, not broker-confirmed).

5. **Cross-broker portfolio view interacts non-trivially with wash-sale flagging.** A user with Alpaca + Robinhood can have Gekko propose a wash-sale-triggering trade because Gekko sees both. But if cross-broker is v2, the wash-sale flag is single-broker only in v1 — which is fine, but worth saying explicitly.

6. **"Auto-execute-within-caps" with LLM agents has a specific failure mode worth designing for.** The LLM can hallucinate that a trade is within caps when it isn't, or it can rationalize its way past a cap ("yes the position cap is 5% but this is *clearly* an exception"). Caps **must** be enforced by a non-LLM check at order-placement time against live portfolio state, not by the LLM checking itself. Worth being explicit about in the architecture.

7. **"Read-only sharing" can quietly become a copy-trading regulatory surface.** PROJECT.md doesn't list sharing as in-scope but I include it in v1.x. If user A can see user B's strategy and trades and chooses to "do what B does" manually, that's fine. If Gekko adds a "replicate" button, that's copy-trading and arguably a regulated activity. Recommend explicit "view only, no auto-replicate" rule.

---

## Sources

- [AI Agents vs Trading Bots: What Actually Works in Crypto (2026) — RPC Fast](https://rpcfast.com/blog/ai-agents-vs-trading-bots) — *agentic trading capability framing*
- [Agentic Trading Explained — Wundertrading](https://wundertrading.com/journal/en/agentic-trading) — *autonomous agent feature inventory*
- [Comparing LLM-Based Trading Bots — FlowHunt](https://www.flowhunt.io/blog/llm-trading-bots-comparison/) — *LLM trading agent feature comparison*
- [How to Build Safe Trading Bots — Sarnia Journal](https://www.thesarniajournal.ca/other/how-to-build-safe-trading-bots-essential-risk-management-strategies-for-automated-trading-success-11781845) — *safety controls inventory*
- [Trading Bot Risk Management — Nadcab](https://www.nadcab.com/blog/trading-bot-risk-management-stop-loss-position-sizing-drawdown-control) — *position sizing, daily loss caps, circuit breakers*
- [Composer.Trade Review — DayTradeReview](https://daytradereview.com/composer-trade-review/) — *Composer feature reference*
- [Alpaca vs Composer — Composer](https://www.composer.trade/learn/composer-vs-alpaca-which-is-the-better-platform-to-create-a-stock-trading-bot) — *Composer execution model*
- [Slack Developer Docs — Approval Workflows](https://api.slack.com/best-practices/blueprints/approval-workflows) — *Block Kit approval pattern*
- [Slack Interactive Messages Guide](https://docs.slack.dev/messaging/creating-interactive-messages/) — *technical implementation reference*
- [Paper Trading vs Live Trading — Alpaca](https://alpaca.markets/learn/paper-trading-vs-live-trading-a-data-backed-guide-on-when-to-start-trading-real-money) — *paper-to-live graduation guidance*
- [Paper vs Live Trading — IBKR Campus](https://www.interactivebrokers.com/campus/trading-lessons/paper-trading-vs-live-trading-whats-the-difference/) — *broker-side paper mode caveats*
- [FinRobot — arXiv 2411.08804](https://arxiv.org/html/2411.08804v1) — *fundamentals + research framework*
- [FinGPT — fingpt.io](https://fingpt.io/) — *open-source financial LLM ecosystem*
- [TradingAgents — arXiv 2412.20138](https://arxiv.org/html/2412.20138v2) — *multi-agent debate framework*
- [Wash Sale Reporting — E*TRADE](https://us.etrade.com/knowledge/library/taxes/wash-sale) — *broker wash-sale reporting mechanics*
- [Wash Sale Tracking — Mezzi](https://www.mezzi.com/blog/wash-sale-tracking-software) — *cross-broker wash sale gaps*
- [Wash Sale Tracking — IBKR](https://www.interactivebrokers.com/en/support/tax-wash-sales.php) — *broker-side wash-sale handling*
- [Alpaca API Rate Limits — Trading Strategies Academy](https://trading-strategies.academy/archives/46906) — *Alpaca rate limits and retry strategy*
- [Alpaca API Limits — Trading Strategies Academy](https://trading-strategies.academy/archives/46910) — *order limits and data restrictions*
- [Broker API Trading Guide — TradersPost](https://blog.traderspost.io/article/broker-api-trading-guide) — *cross-broker order type comparison*
- [Capitalise.ai — Natural Language Strategy Creation](https://capitalise.ai/) — *plain-English strategy intake reference*
- [VibeTrader — vibetrader.markets](https://vibetrader.markets/) — *NL-to-strategy commercial product*
- [Interactive Brokers IBot](https://www.interactivebrokers.com/en/trading/ibot.php) — *conversational broker UX reference*
- [NYSE Holidays & Trading Hours](https://www.nyse.com/trade/hours-calendars) — *2026 holiday calendar*
- [STARTRADER Web STAR Copy Launch (2026)](https://financialcommission.org/2026/03/30/startrader-launches-web-star-copy-as-social-trading-demand-grows/) — *copy-trading state of the art (anti-feature reference)*
- [Why Your Algorithmic Trading Logs Might Not Survive a Regulatory Audit — VeritasChain via Medium](https://medium.com/@veritaschain/why-your-algorithmic-trading-logs-might-not-survive-a-regulatory-audit-1582bfd1445d) — *audit log tamper-evidence concerns*
- [AI Trading Laws Explained (2026) — Advanced AutoTrades](https://advancedautotrades.com/is-trading-with-ai-legal/) — *SEC posture for AI-driven retail trading*

---

*Feature research for: Project Gekko — autonomous personal stock trading agent*
*Researched: 2026-06-08*
*Confidence: MEDIUM-HIGH — competitor + safety landscape is well-documented; the specific LLM-HITL-Slack combination is less precedented*
