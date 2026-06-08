# Project Gekko

## What This Is

Project Gekko is a simple, easy-to-use autonomous stock trading agent powered by Claude. The user defines an investment strategy in plain English (with form-based tuning), and the agent researches the market, proposes or executes trades on the user's chosen brokerage(s), and reports progress back. It runs as an always-on desktop client (Mac Mini or Windows machine) and supports a small group of independent users — each with their own broker connections, strategies, and portfolios.

## Core Value

A trustworthy autonomous agent that turns a plain-English investment thesis into actual, monitored trades on the user's own brokerage account — starting human-in-the-loop with small dollars and graduating to autonomy as trust is earned. If everything else fails, *this* must work: a user describes a strategy, the agent researches it, surfaces concrete trade decisions with rationale, and (with appropriate authorization) executes them.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] User can define an investment strategy via natural-language chat ("I'm bullish on AI infra, max 5% per position, avoid Chinese stocks")
- [ ] User can tune the resulting strategy via a structured form (risk tolerance, position sizing, sector preferences, watchlist)
- [ ] User can drop ad-hoc guidance during a run ("look at energy this week") that the agent factors into research
- [ ] Agent runs autonomously on a configurable cadence — scheduled (open/midday/close), event-driven (news/price moves/earnings), or continuous-with-cooldowns
- [ ] Agent connects to broker APIs for Alpaca, IBKR, and Schwab (where API support is available)
- [ ] Agent connects to brokers without public APIs (Robinhood, Fidelity) by driving the brokerage website via Claude-for-Chrome / browser-use as a fallback
- [ ] Agent researches each trade decision using price/market data (free tier), news & sentiment APIs, fundamentals (SEC EDGAR + free APIs), and web research via Claude-for-Chrome
- [ ] Agent operates in **human-in-the-loop mode by default** — proposes trades with rationale and waits for explicit approval before executing real-money trades
- [ ] User can configure per-strategy hard caps: max position size, max daily loss, max trades per day, max exposure per sector
- [ ] Agent reports out via Slack DM (trade proposals, execution confirmations, daily P&L), web dashboard (portfolio view, trade history, strategy editor), and email digests (daily/weekly summary)
- [ ] Multi-user support: each user has their own broker connections, strategies, portfolios — fully isolated
- [ ] Paper-trading mode is available before any real money flows (Alpaca/IBKR paper accounts; simulated execution for browser-fallback brokers)
- [ ] Trust ladder is configurable per strategy — a strategy can be flipped from "propose-only" to "auto-execute-within-caps" once the user is satisfied

### Out of Scope

- **Day-trading-grade execution speeds (sub-second loops)** — Claude inference latency makes this unrealistic and would explode LLM cost. Optimize for swing/long-term horizons; revisit if needed.
- **Public SaaS / open sign-ups** — v1 is "me + a few trusted people I share with" with each user running on a shared self-hosted instance. No anonymous sign-ups, no billing, no compliance scaffolding for public service.
- **Custom brokerage clearing / direct market access** — we route through retail brokerages, not exchanges directly.
- **Crypto / forex / futures / options as first-class strategies** — v1 focuses on US equities (with optional crypto via Alpaca if it falls out naturally). Other asset classes deferred.
- **Tax-loss harvesting / wash-sale enforcement / tax-form generation** — the agent will *flag* potential wash sales but will not be the source of truth for taxes. Punt to user's tax software.
- **Mobile native app / push notifications** — Slack DM + email + web dashboard cover the notification need. Mobile is a future option, not v1.
- **Fully autonomous trading from day one** — this is the explicit risk posture: every strategy starts HITL with small capital and graduates to autonomy only after explicit user promotion.

## Context

**Why this project:** Chris wants a personal autonomous trading agent that takes plain-English investment theses and turns them into researched, executed trades — without reinventing the wheel. Existing agent frameworks are being evaluated, but the strong preference is to stay inside the Anthropic / Claude ecosystem (Claude Agent SDK + tools, Claude-for-Chrome for the browser-fallback path) to minimize technical debt from cross-ecosystem dependencies.

**Frameworks under consideration:** Claude Agent SDK (default — Anthropic-native, lowest tech debt), with research planned into OpenClaw.ai and NVIDIA NeMo-Claw (user has heard good things; recommendation will surface during the research phase). LangGraph / CrewAI / OpenBB explicitly considered and rated lower because they add an outside-ecosystem layer.

**Runtime target:** Always-on desktop client on a Mac Mini or Windows machine on the user's network. Acts as a small self-hosted server for the dashboard and webhook receivers. Not cloud-deployed for v1 (no AWS/Azure dependency, no SaaS billing).

**Brokerage landscape:** Public-API brokers (Alpaca, IBKR, Schwab post-TD-merger) are the primary path. Robinhood and Fidelity lack official public APIs; reaching them requires browser automation via Claude-for-Chrome / browser-use — slower and more fragile but unlocks the brokers many users actually have.

**Research data landscape:** Price/market data is largely free (Yahoo unofficial, Alpaca's feed, broker quotes); news/sentiment can start on free tiers (Finnhub, Alpha Vantage); fundamentals from SEC EDGAR are free. Premium data (Polygon, FMP paid, etc.) deferred to when justified.

**User context:** Chris (technical leader at Adeptia) is the primary user. Heavy Slack user already (Slack DM is the natural notification channel). Familiar with Kubernetes/AKS and self-hosting; Mac Mini / Windows-machine deployment is well within his comfort zone.

## Constraints

- **Tech stack:** Claude Agent SDK preferred for orchestration; cross-platform runtime (Mac + Windows) — leans toward Python or Node. — *Stay in Anthropic ecosystem to minimize technical debt.*
- **Trade-execution safety:** Human-in-the-loop is mandatory for v1 real-money trades. Autonomous execution is allowed only after explicit per-strategy user promotion, and only within hard caps. — *Real money on the line; one runaway loop wipes confidence in the project.*
- **Multi-tenant isolation:** Each user's broker credentials, strategy state, and portfolio data must be isolated. — *Sharing real-money credentials across users is a non-starter.*
- **Cost:** Claude API spend per user per day should be bounded with a configurable ceiling (e.g. $X/day) and the agent should degrade gracefully (longer cadence, smaller research depth) when approaching it. — *Continuous-loop strategies can otherwise rack up unbounded LLM costs.*
- **Deployment:** Runs on Chris's Mac Mini or a Windows machine (no AWS/Azure dependency for v1). — *Self-hosted is the deliberate choice; avoid cloud lock-in early.*
- **Regulatory posture:** This is a personal-use / friends-and-family tool, not a regulated financial product. The agent must not give "investment advice" in a regulated sense — strategies and trades are the user's own decisions, the agent executes them. — *Treat compliance carefully so the project doesn't accidentally become a regulated entity.*
- **Browser-fallback fragility:** Browser-automation paths (Robinhood, Fidelity) are inherently fragile to broker UI changes. Treat them as second-class: more retries, screenshot logging, easier to disable per-broker. — *Brokers change their UIs unpredictably; never block a release on a broken browser-driver path.*

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Claude Agent SDK is the default orchestration framework | Stay in Anthropic ecosystem; minimize cross-ecosystem technical debt; first-class tool-use semantics | — Pending (research phase will validate vs. OpenClaw.ai / NeMo-Claw) |
| Both broker API and browser-fallback paths supported | API path is reliable but excludes Robinhood/Fidelity (no public APIs); browser path unlocks them via Claude-for-Chrome | — Pending |
| Multi-user with full per-user isolation (own broker, strategy, portfolio) | Sharing broker credentials is unacceptable; each user runs independent strategies | — Pending |
| HITL with small real $ → graduated autonomy (per-strategy promotion) | Real-money safety; trust is earned not granted; runaway agents are the catastrophic failure mode | — Pending (trust-ladder design is its own phase) |
| Strategy specification = natural-language chat + structured form tuning + ad-hoc guidance | Chat is flexible and intuitive; form gives precision; guidance lets the user steer the agent live without re-onboarding | — Pending |
| Self-hosted on always-on Mac Mini / Windows machine (no cloud for v1) | Avoid cloud lock-in early; keep broker credentials on-premise; lower running cost | — Pending |
| Trust ladder design treated as a dedicated phase | This is the riskiest part — real money + autonomy interaction. Needs its own dedicated design pass, not glued on | — Pending |
| Day-trading explicitly out of scope | LLM inference latency + cost make sub-minute loops unrealistic | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-08 after initialization*
