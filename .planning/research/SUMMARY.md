# Project Gekko — Research Summary

**Project:** Project Gekko — autonomous stock trading agent powered by Claude
**Domain:** LLM-driven autonomous trading agent (multi-broker, multi-user, self-hosted)
**Researched:** 2026-06-08
**Confidence:** HIGH on core technology + safety architecture; MEDIUM on browser-fallback + regulatory edge cases

## Executive Summary

Project Gekko is a self-hosted, LLM-driven swing-trading agent that converts a plain-English investment thesis into researched, monitored trades on the user's own brokerage account. The four parallel research dimensions (stack, features, architecture, pitfalls) **converge tightly** on the same core picture: a single-process Python application built on the **Claude Agent SDK**, fronted by **Slack-native HITL**, gated by a **non-LLM OrderGuard layer** that enforces hard caps deterministically, and shipped as a **vertical slice** through Alpaca paper trading before any other broker or feature is layered on. All four researchers independently rejected OpenClaw.ai (wrong shape, wrong ecosystem) and NVIDIA NeMo-Claw (enterprise wrapper, wrong scale). All four pointed at the same vertical-slice Phase 1.

The dominant risk is **not technology choice — it is real-money safety**. Catastrophic failure modes documented in the research include Knight-Capital-style duplicate-order loops, LLM ticker hallucination, off-by-magnitude position sizing, LLM "talking itself into" a bad trade through autoregressive reasoning drift, prompt injection from news/web content, and SEC investment-adviser registration thresholds that bite at >4 users. The mitigation pattern is consistent across all four research files: **the LLM proposes, deterministic code executes, and OrderGuard is the final non-LLM backstop on every order**. Multi-user isolation is foundational (cannot be retrofitted), and the trust ladder warrants its own dedicated phase.

The recommended approach is therefore a **trust-first roadmap**: ship a working end-to-end loop on paper trading for a single user with Alpaca and Slack HITL, then build out the trust infrastructure (OrderGuard + caps + audit + cost ceiling) before adding any second broker, any multi-user features, any autonomy, and *especially* before any browser-fallback broker. Browser-fallback for Robinhood and Fidelity is treated as P2 — meaningful and shippable, but never P1. Robinhood's official Agentic Trading API (launched 2025) may obsolete the browser path for that specific broker and should be validated before committing engineering time to a browser adapter.

## Cross-Cutting Consensus (Strongest Signals)

| Finding | STACK | FEATURES | ARCH | PITFALLS |
|---|:---:|:---:|:---:|:---:|
| Claude Agent SDK (Python) is the right orchestration choice | HIGH | implied | HIGH | YES |
| Reject OpenClaw.ai (TypeScript, OpenAI-aligned, personal-assistant gateway) | HIGH | — | implied | implied |
| Reject NVIDIA NeMo-Claw (enterprise wrapper, no Claude integration) | HIGH | — | implied | implied |
| Phase 1 = vertical slice: Alpaca paper + single user + Slack HITL | YES | YES | YES | YES |
| Non-LLM OrderGuard / cap-enforcement layer is mandatory | implied | YES | YES | YES (the single most important architectural decision) |
| Browser-fallback brokers (Robinhood, Fidelity) are P2 not P1 | YES | YES | YES (Ph 6/7) | YES (Ph 7 — last to ship) |
| Trust ladder deserves its own dedicated phase | implied | YES | YES (Ph 5) | YES (Ph 5) |
| Multi-user isolation is foundational — cannot be retrofitted | implied | YES | YES | YES (Catastrophic if leaked) |
| HITL = state-managed, never blocking await | — | implied | YES | YES (idempotent buttons, timeout=REJECT) |
| Money math = `Decimal`, never `float` | implied | — | YES | YES |
| Idempotency via `client_order_id` is non-negotiable | — | — | YES | YES (Knight Capital — Catastrophic) |

These eleven cross-cutting findings constitute the **roadmap's load-bearing assumptions**. Any phase that violates them should be challenged.

## Stack Decision Summary

| Layer | Pick | Version | Confidence |
|---|---|---|---|
| Orchestration | Claude Agent SDK (Python) | 0.2.93 | HIGH |
| Runtime | Python | 3.12.x | HIGH |
| Web framework | FastAPI | 0.115.x | HIGH |
| Dashboard UI | HTMX + Tailwind + Jinja2 | HTMX 2.0.x | HIGH |
| OLTP store | SQLite (WAL) — with SQLCipher per ARCH | 3.46+ | HIGH |
| Analytics | DuckDB | 1.1.x | HIGH |
| Scheduler | APScheduler (in-process, SQLite job store) | 4.0.x | HIGH |
| Auth | `fastapi-users` + magic-link | 14.x | MEDIUM |
| Email | Resend (3,000/mo free) | latest | HIGH |
| Slack | `slack-bolt` | latest | HIGH |
| Process supervision | launchd (macOS) + NSSM (Windows) | n/a | HIGH |
| Broker — Alpaca | `alpaca-py` (official) | 0.32.x+ | HIGH |
| Broker — IBKR | `ib_async` + TWS/IB Gateway | 1.0.x+ | HIGH |
| Broker — Schwab | `schwab-py` (unofficial, well-maintained) | latest | MEDIUM |
| Broker — Robinhood/Fidelity | `browser-use` (Playwright + LLM) | latest | MEDIUM |
| Market data — prices | `alpaca-py` + `yahooquery` (NOT `yfinance`) | latest | HIGH |
| Market data — news | Finnhub + Alpha Vantage (free tiers) | latest | HIGH |
| Market data — fundamentals | SEC EDGAR REST | n/a | HIGH |
| Credentials | SQLCipher + `cryptography` (Fernet) | 43.x | HIGH |
| Logging | `structlog` (JSON) | latest | HIGH |

**Explicit rejections (HIGH confidence):** OpenClaw.ai, NVIDIA NeMo-Claw, LangGraph, CrewAI, OpenBB Platform (as orchestration), `alpaca-trade-api` (old SDK), `robin_stocks`/`pyrh` long-term, raw `yfinance`, Celery+Redis, Postgres-at-v1, Next.js-for-dashboard, Auth0/Clerk/WorkOS, SendGrid free tier, Task Scheduler/cron as supervisor.

**Disagreement to resolve:** STACK recommends Fernet + OS-keychain master key; ARCHITECTURE recommends SQLCipher whole-DB encryption + passphrase-on-start, citing cross-platform parity (silent failures with keychain when service runs without logged-in user session). **Recommend ARCH's SQLCipher approach** for cross-platform reliability.

## Table Stakes Summary

(See FEATURES.md for full inventory.)

- **Strategy:** plain-English chat → structured doc → form tuning; ad-hoc guidance; per-trade rationale as structured artifact; version history
- **Safety floor:** hard caps enforced at *execution layer* (size, daily loss, max trades/day, sector); paper-mode default; market-hours/holiday calendar; limit/market/stop + slippage tolerance; rate-limit backoff; wash-sale flagging; kill switch; broker-disconnect halt
- **HITL:** Slack Block Kit proposal cards (approve/reject/edit); timeout=REJECT default (~30 min); per-strategy trust level (propose-only vs auto-within-caps); per-trade override; append-only audit log with rationale
- **Multi-user:** per-user encrypted credentials; per-user portfolios; per-user Slack DM routing; per-user dashboard sessions (OIDC); per-user LLM budget ceiling with graceful degradation
- **Reporting:** Slack execution confirmations; daily P&L (Slack + email); web dashboard (portfolio, trade history with rationale, strategy editor); exportable CSV
- **Operations:** heartbeat; persistent state across restarts

**Explicit anti-features:** sub-second day-trading; options/futures/derivatives; crypto leverage/perps; public sign-ups/SaaS; tax form generation; mobile native app; copy-trading marketplace; auto-strategy generation; tick-streaming into LLM; "investment advice" framing.

## Watch Out For (Catastrophic-Severity Pitfalls)

| # | Pitfall | One-Line Mitigation |
|---|---|---|
| 1 | **Knight Capital loop** — duplicate orders | Deterministic `client_order_id`; pre-flight "does order exist?" check; never auto-retry order POSTs — query instead |
| 2 | **Hallucinated ticker** (NVAX instead of NVDA) | Universe whitelist in OrderGuard; LLM never types tickers — calls `resolve_ticker(company_name)`; HITL card shows ticker+company+sector+price |
| 3 | **Off-by-magnitude sizing** (500 shares vs $500 notional) | All sizing in deterministic code; OrderGuard enforces `qty*price` ≈ declared notional within 2%; hard $ ceiling regardless of caps |
| 4 | **LLM talks itself into a bad trade** (autoregressive drift) | Bounded research turns (~12 calls / 8K tokens); separate research from decision agent; fresh-context sanity check before submit; "no_action" is first-class output |
| 5 | **Prompt injection** via news/filings/web | Privilege separation (research agent has zero order/credential access); untrusted-content delimiters; source allowlist; OrderGuard is final backstop |
| 6 | **SEC adviser-line breach** at >4 users | Per-user isolated deployment; software-not-advice framing; no central performance dashboard; written user agreement; legal review at ~5 users |
| 9 | **Multi-user credential/data leakage** | Per-user encryption keys; creds never in LLM context; explicit `user_id` through every layer; OrderGuard validates cred-to-order user pairing |
| 10 | **Paper-vs-live mix-up** | Explicit env indicator on every UI surface (red=live, green=paper); OrderGuard validates env-credential pairing; first-live-trade gate via separate-channel confirmation |
| 14 | **Trust-ladder cap compounding** (4 strategies × 5% all correlated = 20% concentration) | Portfolio-level caps + per-strategy caps; cross-strategy correlation check; capital scaling is its own trust rung |

**Architectural implication:** OrderGuard — non-LLM, deterministic, every order passes through it — is the single most important architectural element. PITFALLS.md: "If this layer doesn't exist by Phase 2, every later phase is building on quicksand."

## Suggested Phase Order (Consolidated)

### Phase 1 — Foundation & Vertical Slice
**Delivers:** Working end-to-end loop: Alpaca paper credentials → strategy chat → manual trigger → Slack proposal → approve → paper fill → audit log. Foundational decisions baked in: multi-user data model (even with one user), `UserContext` pattern, per-user encryption, `Decimal` everywhere, deterministic Executor, append-only audit, regulatory framing.
**Avoids:** 6 (regulatory), 9 (multi-user data model), 17 (tool-use enforcement).

### Phase 2 — Broker Integration & OrderGuard (real-money safety floor)
**Rationale:** PITFALLS is unambiguous: "No real money flows until this phase passes verification." Largest concentration of catastrophic pitfalls.
**Delivers:** Production-grade order execution with all safety rails; real-money Alpaca live (still HITL). OrderGuard (idempotency, universe whitelist, hard caps, qty×price sanity, env-credential pairing, kill switch); paper-vs-live indicator everywhere; PDT/settlement/buying-power; OAuth refresh coordinator (Schwab 7-day tokens); broker-disconnect detection.
**Avoids:** 1, 2, 3, 9, 10, 11, 16.

### Phase 3 — HITL UX (dedicated phase)
**Delivers:** Production Slack approval with idempotent buttons (Slack retries are at-least-once); quiet hours (no 2am approvals); timeout = REJECT; structured rationale card; edit-size option; expiry on stale proposals; designed friction for first-live-trade.
**Avoids:** 12.

### Phase 4 — Agent Architecture & Cost Bounds
**Delivers:** Research/decision agent separation (defends against drift AND injection); bounded research turns; fresh-context sanity check; ticker resolver tool; untrusted-content delimiters + source allowlist; per-user-per-day hard cost ceiling (circuit-breaker not warning); tool-use enforcement (eliminates JSON parsing failures); summarize-and-discard for long content; Haiku-for-triage tier.
**Avoids:** 4, 5, 7, 17.

### Phase 5 — Trust Ladder (dedicated phase, per PROJECT.md key decision)
**Delivers:** Per-strategy promotion (HITL → auto-execute-within-caps) with statistical promotion criteria; portfolio-level caps in addition to per-strategy; capital scaling as its own rung distinct from autonomy promotion; revocation UI; anomaly detection.
**Avoids:** 10 (paper→live), 13 (backtest-to-live), 14 (cap compounding).

### Phase 6 — Multi-User UI & Web Dashboard
**Delivers:** User provisioning UI; per-user dashboard (strategy editor, portfolio, trade history with rationale, audit browser); web-based approval fallback; strategy guidance UI; OIDC auth.
**Disagreement to surface:** ARCH puts multi-user (Ph 3) before dashboard (Ph 4); FEATURES treats dashboard as v1. **Recommend:** data model in Phase 1; user-facing UI here.

### Phase 7 — Operations & Observability
**Delivers:** launchd/NSSM auto-restart; macOS pmset wake; Windows Update active-hours; log rotation; NTP drift monitoring; trading-calendar-aware scheduling (skip IBKR 23:45-00:45 ET reset); external heartbeat / dead-man-switch; daily reconciliation; UPS recommendation.
**Avoids:** 15, shores up 7.

### Phase 8 — Additional API Brokers
**Delivers:** IBKRBroker (`ib_async` + Gateway side-process); SchwabBroker (`schwab-py` + per-user OAuth onboarding); per-broker circuit breakers.
**Disagreement to surface:** ARCH puts Schwab in Phase 2; PITFALLS/FEATURES are agnostic. **Recommend:** Schwab earlier if Chris or a v1 user has Schwab holdings; otherwise here.

### Phase 9 — Browser-Fallback Brokers (last to ship)
**Delivers:** `BrowserBroker` base; Robinhood adapter (only after re-validating Robinhood's official Agentic Trading API); Fidelity adapter; per-broker feature flags; DOM signature checks; screenshot evidence per action; MFA = halt+HITL escalate.
**Open question:** Whether to build Robinhood via browser at all given new official API.
**Avoids:** 8.

### Phase 10 — Polish, Differentiators, Event Triggers
**Delivers:** Reasoning retrospective dashboard (hit rates by thesis category — depends on rationale being structured from Phase 1); cross-broker portfolio aggregation; event-driven cadence (news/earnings/price alerts); multi-strategy per user with allocation; weekly performance email; anomaly alerts beyond connection loss.

### Phase Ordering Rationale

- **Phases 1 → 2 → 3 is a hard sequence.** Can't do OrderGuard without the slice; can't do production HITL UX without the execution path.
- **Phase 4 could be earlier in theory** — research/decision separation is cheaper from day one — **but the failures it defends against only surface at scale.** Make the structural decision in Phase 1; invest in hardening in Phase 4.
- **Phase 5 before browser brokers** — graduate autonomy on stable API brokers, not fragile browser path.
- **Phase 7 (Ops) after trust ladder** — autonomy + unreliable ops = silent failure.
- **Phase 9 (Browser) last** — highest fragility, highest TOS risk, lowest confidence.

### Research Flags

**Needs research-phase:**
- Phase 2 (OrderGuard implementation per broker; fault-injection design)
- Phase 4 (latest Claude Agent SDK subagent isolation; conviction calibration)
- Phase 5 (statistical promotion criteria; portfolio-cap math)
- Phase 9 (Robinhood Agentic Trading API alternative; current `browser-use` reliability)

**Standard patterns (skip research):**
- Phase 1 (well-documented Agent SDK + FastAPI + Slack Bolt)
- Phase 6 (standard FastAPI + HTMX + `fastapi-users`)
- Phase 7 (standard launchd/NSSM/structlog)
- Phase 8 (well-documented `ib_async` / `schwab-py` once `Broker` ABC solid)

## Open Questions for Roadmap (decision points before phases lock)

1. **Browser-fallback brokers as P1 or P2?** All four researchers say P2 / Phase 9 / last. **Recommend P2.** Validate Robinhood official API first.
2. **Multi-user: Phase 1 (data model) or Phase 6 (operational surface)?** **Recommend both: data model Phase 1, UI Phase 6.** Cannot retrofit.
3. **Credential encryption: STACK's Fernet+keychain vs ARCH's SQLCipher+passphrase?** **Recommend ARCH's SQLCipher** for cross-platform parity.
4. **Trust ladder gets its own dedicated phase?** PROJECT.md, ARCH, PITFALLS all say yes. **Confirm yes — Phase 5.**
5. **Schwab integration: Phase 2 (ARCH) or Phase 8 (default)?** **Recommend:** Phase 2 only if Chris/v1 user has Schwab holdings; otherwise Phase 8.
6. **Approval timeout default: REJECT or EXECUTE?** PITFALLS explicit: REJECT. **Confirm REJECT.**
7. **Strategy session continuity: per-strategy persistent vs per-run fresh?** **Recommend:** fresh session per run + persistent guidance/notes table injected as context.
8. **Master-key UX: passphrase-on-start vs env-var?** **Recommend:** passphrase-on-start for v1.
9. **Paper-vs-live machine-level switch for dev boxes?** **Recommend yes** — cheap, real value.
10. **Wash-sale: flag only (PROJECT.md) or "avoid causing avoidable wash sales" (FEATURES suggestion)?** **Decision needed from Chris.**
11. **Per-user isolated deployment vs shared self-hosted instance (regulatory)?** PROJECT.md leans shared; PITFALLS notes that's weaker regulatorily. **Surface explicitly to Chris.**
12. **Read-only strategy sharing (v1.x in FEATURES) — allowed?** **Recommend:** explicit "view-only, no auto-replicate" rule.
13. **Cost ceiling response: graceful degradation vs hard halt?** Reconcile: graceful at 80%, hard halt at 100%. **Codify two-tier.**

## Notable Surprises

1. **Robinhood has an official Agentic Trading API now (2025).** May obsolete browser-fallback for Robinhood specifically. Validate before Phase 9.
2. **Schwab refresh tokens expire at 7 days, NOT 90.** Common misconception. Without proactive refresh + 24h-warning Slack DM, the agent silently dies every 7 days.
3. **SEC investment-adviser de minimis is "fewer than 6 clients"** in most states (some at 15). SEC 2024 rule changes tightened internet-adviser exemption. Family-office exemption is narrow (lineal descendants only — not friends). Legal review at 5-user mark.
4. **`yfinance` is unreliable in 2026** — HTML scrape, rate-limited (429) constantly. Community shifted to `yahooquery`.
5. **`ib_insync` was renamed `ib_async`** in early 2024 after original author (Ewald de Wit) passed. Moved to `ib-api-reloaded` org. Old docs everywhere are stale.
6. **OpenClaw creator joined OpenAI in 2026.** Project now OpenAI-flavored. Reinforces rejection.
7. **NVIDIA NeMo-Claw runs OpenClaw + Hermes inside NVIDIA OpenShell with zero Claude integration.** Literal OpenClaw deployment wrapper, not a competing framework.
8. **Knight Capital (2012, $440M in 45 min)** is the canonical worst-case for the exact failure Gekko must defend against.
9. **Continuous-loop LLM agents have produced bills of $4,200 (long weekend), $8,000+ (2.5 hours, 49 subagents), $47,000/3 days (23 unattended subagents).** Cost ceiling is a circuit-breaker, not a warning.
10. **Cash-account settlement is T+1 since May 2024** (not T+2). Good-faith violation enforcement depends on this.
11. **Robinhood TOS explicitly prohibits automation.** Account closures propagate via industry databases (affect onboarding at other brokers). Worth explicit user disclosure.
12. **Reasoning-as-structured-artifact (not free text) is a one-shot architectural decision.** If rationale starts as freeform Slack text, the long-term differentiator (cohort by thesis category vs hit rate) is impossible to retrofit. Must be structured from day one.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | **HIGH** | Verified against PyPI, GitHub, official docs (Anthropic, Alpaca, IBKR, Schwab, browser-use). Versions current to 2026-06-06. MEDIUM only on rapidly-evolving browser-fallback and commodity auth/email layers. |
| Features | **MEDIUM-HIGH** | Ecosystem patterns well-documented (Composer, Capitalise.ai, TradingAgents/FinRobot); specific Claude-HITL-Slack-multi-user combination less precedented. MVP inventory HIGH; differentiator competitive analysis MEDIUM. |
| Architecture | **MEDIUM-HIGH** | Component design HIGH from established patterns (state-managed approval, deterministic executor, idempotent broker calls). Specific library choices MEDIUM pending POC. Browser-broker design LOW until tested. |
| Pitfalls | **HIGH** | Finance/broker/regulatory pitfalls verified against SEC, FINRA, broker docs, Knight Capital post-mortems. LLM-specific failures verified against arXiv 2025/2026 + vendor docs. Browser-automation specifics MEDIUM (TOS interpretation varies). |

**Overall: HIGH** on the four cross-cutting consensus findings (stack core, vertical slice, OrderGuard, browser-as-P2). **MEDIUM** on Phase 6+ ordering (flexibility based on user demand).

### Gaps to Address During Planning

- **Robinhood Agentic Trading API status** — validate in Phase 1 before committing to browser adapter
- **Trust ladder statistical promotion criteria** — needs explicit thresholds in Phase 5; placeholder needed for v1
- **Exact LLM cost-ceiling thresholds** — Phase 4 needs default values + tier breakpoints (80% soft / 100% hard halt)
- **Wash-sale default behavior** — decision needed from Chris (flag only vs avoid causing)
- **Regulatory deployment model** — per-user isolated vs shared self-hosted (Chris decision before Phase 1 locks)
- **Capital scaling thresholds in trust ladder** — strategy at $1K may not work at $50K; explicit "capital bump" rung needed
