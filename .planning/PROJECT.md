# Project Gekko

## What This Is

Project Gekko is a self-hosted, human-in-the-loop autonomous stock trading agent powered by Claude. The user defines an investment strategy in plain English (with form-based tuning), and the agent researches the market, proposes trades on the user's chosen brokerage(s), and executes them after Slack approval. v1.0 delivers a working end-to-end walking-skeleton against **Alpaca paper trading** with Slack HITL approval; subsequent versions add real-money safety guardrails (OrderGuard), production HITL UX, a Trust Ladder for graduating to autonomy, web dashboard + multi-user auth, operations/observability, and additional brokers (IBKR, Schwab, Robinhood, Fidelity).

It runs as an always-on desktop client (Mac Mini or Windows machine) and supports a small group of independent users — each with their own broker connections, strategies, and portfolios. Multi-user isolation is in the data model from day one; the user-facing multi-user surface ships in v3.0.

## Core Value

A trustworthy autonomous agent that turns a plain-English investment thesis into actual, monitored trades on the user's own brokerage account — starting human-in-the-loop with small dollars and graduating to autonomy as trust is earned. **v1.0 confirmed this is the right core value**: the manual walking-skeleton demo on 2026-06-12 produced three real paper-trading fills (AVGO, NVDA, AMD) with a SHA-256 audit-chain proof of correctness; the loop works.

## Current State (v2.0 shipped 2026-06-28)

**v2.0 "Safety & Trust"** (Phases 2-5, 44 plans, 94 tasks) added: the OrderGuard single deterministic order firewall (every order — HITL and auto — traverses one zero-decorator `place_order` pipeline), real-money Alpaca live with the first-live dual-channel gate, production HITL UX (Slack Block Kit, quiet hours, timeout=REJECT), agent Researcher/Decision separation + two-tier cost ceiling, and the **trust ladder**: per-strategy `propose-only → auto-within-caps` promotion, portfolio-level caps, capital scaling as a separate rung, and anomaly auto-demotion on single-day drawdown. Tagged `v2.0`.

**v2.0 close note:** milestone audit was `tech_debt`. One in-scope gap (anomaly reflex not armed in the FastAPI lifespan) was fixed at close (commit `9e0fcb5`). Deferred to Phase 7: autonomous scheduled cadence + scheduled-run HITL surfacing (audit BLOCKER-2) and the COST-04 "longer cadence" reschedule. The full pytest suite has pre-existing order-dependent isolation fragility (reliable signal is per-file isolation).

**Codebase (v1.0 baseline):** ~37 src files, Python 3.12, Claude Agent SDK 0.2.93. 365+ unit tests + 11 integration tests passing on cassette mode; manual demo proved real-world correctness on real Slack + Alpaca paper + Claude Sonnet 4.6.

**Tech stack delivered:**
- Backend: Python 3.12, Claude Agent SDK, FastAPI, SQLAlchemy 2.x async, Alembic, APScheduler 3.x
- Frontend: HTMX 2.0.4 vendored + SHA-384 SRI + Tailwind subset (no Node build)
- Storage: SQLCipher (PRAGMA-key) + SQLite WAL — per-user encrypted DB
- Broker: `alpaca-py` (paper-only at v1.0)
- Slack: `slack-bolt` with Socket Mode (no public tunnel)
- Observability: `structlog` with credential-redaction processor + traceback capture

**User feedback themes:** Manual demo discovered 7 real production bugs that cassette tests couldn't have caught — most were identity-split errors between `gekko_user_id` and `slack_user_id`, plus an LLM-rationale-overflow `ValidationError`. All fixed before close. Pattern: real-world LLM output and Slack identity model don't always match what cassette/mocked tests assume.

**Known technical debt:**
- Phase-3 carry-forward: executor errors (`MarketClosed`, `BrokerOrderError`) don't surface to Slack — operator sees silence when post-approval execution fails. Tracked in `quick/260612-dix.../deferred-items.md`.

## Requirements

### Validated (shipped in v1.0)

- ✓ User can author a strategy in plain-English chat or via flags and see it persist as a versioned, structured document — v1.0
- ✓ User can drop ad-hoc guidance and see it injected as a structured directive into the next research run — v1.0
- ✓ User can manually trigger a research-and-propose run and receive a Slack DM with ticker, action, size, rationale, evidence, and approve/reject buttons within ~5 minutes — v1.0 (real demo: 4-5 min Researcher + Decision)
- ✓ User approving a paper-trade proposal sees the order execute against Alpaca paper, the fill confirmed, and the full chain (decision → proposal → approval → order → fill) recorded in the append-only audit log — v1.0 (verified across 3 real demo runs)
- ✓ Every record carries `user_id`; per-user encrypted credentials (SQLCipher PRAGMA-key) work end-to-end with no plaintext on disk — v1.0
- ✓ Slack DM as the primary HITL surface — v1.0 (Block Kit card + Approve/Reject/Edit-Size/Escalate buttons, with cross-user defense)

### Validated (shipped in v2.0)

- ✓ **OrderGuard** — non-LLM cap-enforcement firewall (size, daily loss, max trades/day, sector exposure, qty×price ≤2%, paper/live pairing); first-real-money dual-channel confirmation; kill switch — v2.0 (single zero-decorator pipeline; every order traverses it)
- ✓ **Real-money Alpaca live** — paper→live promotion with all OrderGuard guarantees, red banner, PDT awareness, wash-sale flagging — v2.0
- ✓ **Production HITL UX** — idempotent Slack buttons, quiet hours, timeout=REJECT, edit-size + escalate, stale-proposal expiry — v2.0
- ✓ **Executor-error → Slack notification** (Phase-3 carry-forward) — operator DM on post-approval execution failure — v2.0
- ✓ **Agent architecture** — Researcher/Decision separation, prompt-injection defense (source allowlist + delimiters), bounded turns — v2.0
- ✓ **Two-tier cost ceiling** — 80% degrade (Haiku triage, shorter context) + 100% hard halt; per-user ledger — v2.0 *(note: "longer cadence" reschedule half deferred to Phase 7 — COST-04 partial)*
- ✓ **Trust Ladder** — per-strategy `propose-only → auto-within-caps` promotion gate, portfolio caps, capital-scaling rung, anomaly auto-demotion — v2.0

### Active (v3.0 — Multi-User + Multi-Broker + Deployment)

- [ ] Web dashboard with magic-link multi-user auth; portfolio view, trade history with rationale, strategy editor, ad-hoc guidance, audit browser
- [ ] Daily/weekly email digests
- [ ] Operations: launchd/NSSM supervision, heartbeat / dead-man-switch, NTP enforcement, daily broker reconciliation, trading-calendar-aware scheduling
- [ ] IBKR via `ib_async` + TWS/Gateway supervision (5am ET re-auth, IBKR daily reset window)
- [ ] Schwab via `schwab-py` + per-user OAuth flow + 7-day refresh-token coordinator with 24h-before-expiry DM
- [ ] Robinhood and Fidelity via `browser-use`-driven adapters with DOM signature checks, MFA-halts-to-HITL, before/after screenshots
- [ ] One-command install + first-run wizard for macOS and Windows; in-product upgrade via `pipx upgrade` with automatic SQLite schema migrations

### Out of Scope

- **Day-trading-grade execution speeds (sub-second loops)** — Claude inference latency makes this unrealistic and would explode LLM cost. v1.0 confirmed swing/long-horizon strategies are the right shape.
- **Public SaaS / open sign-ups** — v1 is "me + a few trusted people I share with." No anonymous sign-ups, no billing, no compliance scaffolding for public service.
- **Custom brokerage clearing / direct market access** — we route through retail brokerages, not exchanges directly.
- **Crypto / forex / futures / options as first-class strategies** — v1 focuses on US equities (with optional crypto via Alpaca if it falls out naturally). Other asset classes deferred.
- **Tax-loss harvesting / wash-sale enforcement / tax-form generation** — agent will *flag* potential wash sales but is not the source of truth for taxes. Punt to user's tax software.
- **Mobile native app / push notifications** — Slack DM + email + web dashboard cover the notification need.
- **Fully autonomous trading from day one** — every strategy starts HITL with small capital and graduates to autonomy only after explicit user promotion. v1.0 confirmed this is the right risk posture.
- **LLM-generated UI / live UI redraws** — UI is templated Jinja2 + HTMX, not LLM-rendered. Saves cost and reduces prompt-injection surface.

## Context

**Why this project:** Chris wants a personal autonomous trading agent that takes plain-English investment theses and turns them into researched, executed trades — without reinventing the wheel. v1.0 confirmed staying inside the Anthropic / Claude ecosystem (Claude Agent SDK + Sonnet 4.6) is the right call — no cross-ecosystem dependencies, first-class tool-use, native HITL via the SDK's tool-call protocol.

**Frameworks rejected after research:** OpenClaw.ai (personal-assistant chat gateway, wrong shape; TypeScript-only forces off Python ecosystem; founder moved to OpenAI), NVIDIA NeMo-Claw (enterprise wrapper around LangChain/CrewAI/OpenClaw + NVIDIA inference; massive overkill), LangGraph / CrewAI (out-of-ecosystem for no real gain), OpenBB (great data tool, wrong shape as orchestrator).

**Runtime target:** Always-on desktop client on a Mac Mini or Windows machine on the user's network. Acts as a small self-hosted server for the dashboard and webhook receivers. v1.0 confirmed: works on Windows 11 Pro with Socket Mode adapter (no public tunnel needed).

**Brokerage landscape:** v1.0 ships with Alpaca paper only. v2.0 adds Alpaca live. v3.0 adds IBKR + Schwab (API path) and Robinhood + Fidelity (browser-fallback path).

**Research data landscape (v1.0 in production):** Alpaca data API (real-time IEX bars) + `yahooquery` fallback (NOT `yfinance` — rate-limited HTML-scrape) for prices; Finnhub free tier (60/min) for news + sentiment; SEC EDGAR direct REST for filings; Anthropic Web Fetch for general research. Premium data deferred to when justified.

**User context:** Chris (technical leader at Adeptia) is the primary user. Heavy Slack user already. Familiar with Kubernetes/AKS and self-hosting; Mac Mini / Windows-machine deployment is well within his comfort zone. v1.0 ran on his Windows 11 Pro desktop.

## Constraints

- **Tech stack:** Claude Agent SDK + Python 3.12 (locked in v1.0). — *Stay in Anthropic ecosystem to minimize technical debt; Python beats Node for `alpaca-py` / `ib_async` / `schwab-py` / SEC EDGAR / `pandas_market_calendars` / `browser-use` quality.*
- **Trade-execution safety:** Human-in-the-loop is mandatory for v1.0 real-money trades. Autonomous execution is allowed only after explicit per-strategy user promotion via the Trust Ladder (v2.0/Phase 5), and only within hard caps. — *Real money on the line; one runaway loop wipes confidence in the project.*
- **Multi-tenant isolation:** Each user's broker credentials, strategy state, and portfolio data must be isolated. — *Sharing real-money credentials across users is a non-starter.* Data model carries `user_id` everywhere from v1.0; multi-user UI ships in v3.0.
- **Cost:** Claude API spend per user per day should be bounded with a configurable ceiling. v1.0 uses a `BudgetTracker` with soft/hard limits; v2.0 (Phase 4) adds the formal two-tier ceiling with graceful degradation and hard halt.
- **Deployment:** Runs on Chris's Mac Mini or a Windows machine (no AWS/Azure dependency for v1). — *Self-hosted is the deliberate choice; avoid cloud lock-in early.*
- **Regulatory posture:** This is a personal-use / friends-and-family tool, not a regulated financial product. The agent must not give "investment advice" in a regulated sense — strategies and trades are the user's own decisions, the agent executes them. — *Treat compliance carefully so the project doesn't accidentally become a regulated entity.* REG-01..04 acceptance text shown at `gekko init`.
- **Browser-fallback fragility:** Browser-automation paths (Robinhood, Fidelity) are inherently fragile to broker UI changes. Treat them as second-class: more retries, screenshot logging, easier to disable per-broker. — *Brokers change their UIs unpredictably; never block a release on a broken browser-driver path.* Deferred to v3.0 (Phase 9).

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Claude Agent SDK is the orchestration framework | Stay in Anthropic ecosystem; minimize cross-ecosystem technical debt; first-class tool-use semantics | ✓ Good — confirmed by v1.0 production run on real Sonnet 4.6 |
| Both broker API and browser-fallback paths supported | API path is reliable but excludes Robinhood/Fidelity; browser path unlocks them via `browser-use` | — Pending (v3.0 / Phase 9) |
| Multi-user with full per-user isolation in the data model from day one | Sharing broker credentials is unacceptable; data model is the load-bearing decision (cannot be retrofitted) | ✓ Good — v1.0 ships per-user SQLCipher + `user_id` plumbing; UI in v3.0 |
| HITL with small real $ → graduated autonomy (per-strategy promotion via Trust Ladder) | Real-money safety; trust is earned not granted; runaway agents are the catastrophic failure mode | — Pending (v2.0 / Phase 5) |
| Strategy specification = natural-language chat + structured form tuning + ad-hoc guidance | Chat is flexible and intuitive; form gives precision; guidance lets the user steer the agent live without re-onboarding | ✓ Good — STRAT-01/02/03 all shipped in v1.0 (chat-mode via `compile_strategy_from_chat`; form via dashboard) |
| Self-hosted on always-on Mac Mini / Windows machine (no cloud for v1) | Avoid cloud lock-in early; keep broker credentials on-premise; lower running cost | ✓ Good — v1.0 ran on Chris's Windows 11 Pro |
| Trust ladder design treated as a dedicated phase | This is the riskiest part — real money + autonomy interaction. Needs its own dedicated design pass | — Pending (v2.0 / Phase 5) |
| Day-trading explicitly out of scope | LLM inference latency + cost make sub-minute loops unrealistic | ✓ Good — v1.0 confirmed Researcher+Decision is ~4-5 min/cycle; swing-horizon is the right shape |
| SQLCipher whole-DB encryption + passphrase-on-start (over Fernet+keychain) | Cross-platform parity; avoids silent failures when service runs without logged-in user session | ✓ Good — `gekko init` + `prompt_passphrase` with `GEKKO_DB_PASSPHRASE` env fallback for headless runs |
| Decimal for money math, idempotency via `client_order_id` | Non-negotiable per PITFALLS Pitfall 1 (Knight-Capital prevention) | ✓ Good — `normalize_decimals` everywhere; `compute_client_order_id` is the load-bearing 32-char hex |
| Wash-sale default = flag-only | User asked 2026-06-08 — agent flags but does not auto-avoid (deferred to v2.0 Phase 2 / Phase 5 if needed) | — Pending decision revisit after v2.0 |
| Identity split: `gekko_user_id` (DB) ≠ `slack_user_id` (Slack) | Discovered during 2026-06-12 manual demo — silent cross-user defense bug. Fixed in commit `297a882` and quick task `260612-nlv`. | ✓ Good — class-of-bug now well-understood; pattern locked across all Slack DM paths |
| Single-tenant runtime per Gekko instance (D-18) | Per-user-isolated deployment (each user runs their own Gekko on their own hardware). Simplifies passphrase model, scheduler, and Slack identity. | ✓ Good — confirmed by v1.0; multi-user becomes a packaging/onboarding concern (v3.0 / Phase 9) not a runtime concern |

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
*Last updated: 2026-06-28 after v2.0 Safety & Trust milestone*
