# Stack Research

**Domain:** Autonomous stock trading agent (LLM-driven, self-hosted, multi-user, broker API + browser-fallback)
**Researched:** 2026-06-08
**Overall confidence:** HIGH on orchestration / brokers / runtime / storage; MEDIUM on auth + email (commodity choices); MEDIUM on browser-fallback (rapidly evolving)

---

## TL;DR — Opinionated Picks

| Layer | Pick | Confidence |
|---|---|---|
| Orchestration | **Claude Agent SDK (Python) 0.2.x** | HIGH |
| Runtime | **Python 3.12** | HIGH |
| Brokerage — Alpaca | **`alpaca-py` (official)** | HIGH |
| Brokerage — IBKR | **`ib_async` + TWS/IB Gateway** | HIGH |
| Brokerage — Schwab | **`schwab-py`** (unofficial, well-maintained) | MEDIUM |
| Brokerage — Robinhood | **Browser-fallback via `browser-use`** (NOT `robin_stocks` long-term) | MEDIUM |
| Brokerage — Fidelity | **Browser-fallback via `browser-use`** | MEDIUM |
| Browser automation | **`browser-use` (Playwright + LLM)** primary; **Playwright** for hardened deterministic flows | HIGH |
| Market data — prices | **`alpaca-py` data API** + **`yahooquery`** fallback (NOT raw `yfinance`) | HIGH |
| Market data — news/sentiment | **Finnhub free tier** + **Alpha Vantage free tier** | HIGH |
| Market data — fundamentals | **SEC EDGAR REST API** (direct) | HIGH |
| Storage | **SQLite (WAL mode)** for OLTP; **DuckDB** for analytical reads over the same data | HIGH |
| Web dashboard | **FastAPI + HTMX + Tailwind (Jinja2 templates)** | HIGH |
| Auth | **`fastapi-users`** with magic-link extension + per-user broker secrets in Fernet-encrypted SQLite blob | MEDIUM |
| Email digests | **Resend** (3,000/mo free, modern Python SDK) | HIGH |
| Slack | **Slack Bolt for Python** (already part of project) | HIGH |
| Process supervision | **`launchd` (macOS) + `NSSM` (Windows)** wrapping the same Python entrypoint | HIGH |
| Scheduler | **APScheduler 4.x** in-process (cadence + event triggers) | HIGH |

**What NOT to pick** (full rationale below):
- ❌ **OpenClaw.ai** — wrong tool for the job (personal-assistant chat gateway, not a financial-agent orchestrator)
- ❌ **NVIDIA NeMo-Claw / NeMo Agent Toolkit** — enterprise multi-agent platform that wraps OpenClaw + Hermes; massive overkill and locks you into NVIDIA infrastructure
- ❌ **LangGraph / CrewAI** — leaves the Anthropic ecosystem for no real gain over the Agent SDK on this scope
- ❌ **OpenBB Platform** — fantastic data tool, but the all-in-one workspace it provides is the wrong shape for a self-hosted agent (heavy + opinionated UI); cherry-pick its data providers if needed
- ❌ **Raw `ibapi`** (IBKR native) — usable but painful sync API; `ib_async` wraps it cleanly
- ❌ **`alpaca-trade-api`** (the old SDK) — superseded by `alpaca-py`; do not start here
- ❌ **`robin_stocks` / `pyrh`** as the long-term Robinhood path — unofficial reverse-engineered APIs are explicitly second-class per PROJECT.md constraints; the browser-fallback path is what you committed to

---

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| **Claude Agent SDK (Python)** | 0.2.93 (Jun 6 2026) | Agent orchestration: tool-use loop, HITL checkpoints, MCP client, subagents, sessions | First-party Anthropic SDK. Native HITL checkpoints map 1:1 to your trust-ladder requirement. Bundles Claude Code CLI. Per the PROJECT.md "stay in Anthropic ecosystem" constraint, this is the obvious choice. Alpha tag is misleading — Anthropic ships multiple releases per week and it's used in their own production tooling. |
| **Python** | 3.12.x | Backend runtime | Every meaningful brokerage + data + agent library targets Python first. Node would force you to lose `alpaca-py`, `ib_async`, `schwab-py`, the SEC EDGAR ecosystem, and the entire quant tooling world for a marginal Node ecosystem win. Python wins decisively here. |
| **FastAPI** | 0.115.x | Web framework (dashboard + webhook receivers) | Async, Pydantic-native, OpenAPI for free, plays nicely with HTMX. Standard modern Python web framework. |
| **HTMX** | 2.0.x | Dashboard interactivity without SPA tax | "Me + a few people" scale doesn't justify Next.js bundle/build tax. HTMX gives you SPA feel at 35-40KB total JS payload vs. 100-300KB Next.js. |
| **Tailwind CSS** | 4.x | Dashboard styling | Standard. Use the standalone CLI to avoid Node toolchain in production. |
| **SQLite (with WAL)** | 3.46+ | Primary OLTP store (strategies, trades, users, sessions) | Zero-config, no separate process, file-on-disk lines up with self-hosted Mac Mini / Windows. WAL mode handles the modest concurrency you need. Backup = file copy. |
| **DuckDB** | 1.1.x | Analytical reads over portfolio/trade history, P&L queries, dashboard charts | Reads SQLite directly via the `sqlite_scanner` extension. Use it for charting/P&L analytics; SQLite remains the source of truth. |
| **APScheduler** | 4.0.x | Cadence + event-driven scheduling | Pure-Python, in-process, persists jobs in SQLite. Lighter than Celery+Redis and you have no real distributed need. |
| **`cryptography` (Fernet)** | 43.x | Encrypting per-user broker credentials at rest | Standard Python crypto library. Fernet is "safe by default" symmetric encryption. Master key from `keyring` (OS keychain). |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| **`alpaca-py`** | 0.32.x+ | Alpaca brokerage + market data | Always — Alpaca is the easiest broker to onboard. Also gives you free real-time IEX data + paper trading. |
| **`ib_async`** | 1.0.x+ | Interactive Brokers API (sync + async wrapper) | When user has IBKR. Successor to `ib_insync` (renamed after author's passing in early 2024 and moved to `ib-api-reloaded` org). Still requires running TWS or IB Gateway locally. |
| **`schwab-py`** | latest | Charles Schwab API (post-TD-Ameritrade merger) | When user has Schwab. Schwab requires per-user OAuth app registration via developer.schwab.com (annoying but supported). |
| **`browser-use`** | 0.x latest (May 26 2026 release) | LLM-driven browser automation | Robinhood + Fidelity. 97.7k stars, MIT, actively maintained, Playwright underneath. Lets Claude drive the broker UI with retry/screenshot logging baked in. |
| **`playwright`** | 1.x | Underlying browser driver | Pinned by `browser-use`. Use directly when a flow is stable enough to hardcode (faster + cheaper than asking the LLM each time). |
| **`yahooquery`** | latest | Yahoo Finance data via official endpoints | Free fallback for prices/fundamentals when Alpaca data is insufficient. Prefer this over `yfinance` — `yfinance` scrapes HTML and gets rate-limited (429s) constantly in 2026. |
| **`finnhub-python`** | latest | News, sentiment, alternative data | Free tier: 60 calls/min. Best free news + social sentiment + insider transactions. |
| **`alpha_vantage`** (Python) | latest | Backup quotes, technical indicators, earnings | Free tier: 25 calls/day (yes, day — small). Useful for pre-computed technical indicators (50+) that you'd otherwise hand-roll. Has an official MCP server, which is rare. |
| **`sec-edgar-api`** or direct `httpx` calls | latest | SEC EDGAR filings (10-K, 10-Q, 8-K, insider Form 4) | Free, no API key. Just send a `User-Agent` header per SEC fair-use policy. |
| **`fastapi-users`** | 14.x | User registration, sessions, auth backends | Multi-user scaffold. Magic-link auth requires a small custom strategy on top; that's fine for "me + a few people" scope. |
| **`slack-bolt`** | latest | Slack DM bot for trade proposals + approvals | Standard Slack SDK for Python. Use `socket_mode=True` so you don't need a public webhook endpoint for v1. |
| **`resend`** (Python SDK) | latest | Email digests | 3,000 emails/mo free, permanent (not a trial). Modern API. |
| **`pydantic`** | 2.x | Schema validation everywhere | Used by Claude Agent SDK, FastAPI, alpaca-py. Already transitively required. |
| **`httpx`** | latest | HTTP client for everything else | Async-first, replaces `requests`. |
| **`apscheduler`** | 4.x | Scheduler (see above) | Always. |
| **`structlog`** | latest | Structured logging | Auditability is a real requirement for a trading agent; structured logs make trade-history reconstruction trivial. |
| **`keyring`** | latest | OS keychain access (master encryption key) | macOS Keychain / Windows Credential Manager. Hosts the master key that decrypts the per-user Fernet-encrypted credential blobs. |
| **`uvicorn`** | latest | ASGI server for FastAPI | Production server in front of FastAPI. Standard. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| **`uv`** | Python package + project manager | Replaces pip/poetry. 10-100x faster, single tool, handles venvs. Standard in 2026. |
| **`ruff`** | Linter + formatter | Replaces black, flake8, isort. Standard in 2026. |
| **`pyright`** or **`mypy`** | Static typing | Trading code = real money; types catch bugs. Pyright is faster, mypy has wider ecosystem support. Pick one. |
| **`pytest`** + **`pytest-asyncio`** | Testing | Standard. |
| **`vcrpy`** or **`respx`** | HTTP mocking for broker/data API tests | Critical — you do NOT want tests hitting real broker APIs. |
| **`pre-commit`** | Git hooks for ruff/pyright | Standard. |

---

## Installation

```bash
# Install uv first (standard 2026 Python tooling)
# macOS: brew install uv
# Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

uv init project-gekko && cd project-gekko
uv python pin 3.12

# Core
uv add claude-agent-sdk fastapi uvicorn pydantic httpx structlog apscheduler

# Brokers
uv add alpaca-py ib_async schwab-py

# Browser fallback
uv add browser-use playwright
uv run playwright install chromium

# Market data
uv add yahooquery finnhub-python alpha_vantage

# Web dashboard
uv add jinja2 python-multipart
# (HTMX + Tailwind loaded via CDN or vendored — no Node build step)

# Auth + crypto
uv add fastapi-users[sqlalchemy] cryptography keyring

# Email + Slack
uv add resend slack-bolt

# Storage
uv add duckdb
# SQLite ships with Python stdlib

# Dev
uv add --dev ruff pyright pytest pytest-asyncio respx pre-commit
```

---

## Detailed Evaluation: Orchestration Frameworks

### ✅ Claude Agent SDK — PRIMARY CHOICE (HIGH confidence)

**What it is:** Anthropic's official Python SDK for building agents on top of Claude (and Claude Code under the hood). Released as `claude-agent-sdk` on PyPI (the older `claude-code-sdk` is deprecated; do not use it).

**Version verified:** 0.2.93, released 2026-06-06. Marked "Development Status: 3 - Alpha" but ships multiple releases per week — that's "fast-moving," not "unstable."

**What it gives you out of the box:**
- Tool-use loop with HITL (human-in-the-loop) checkpoints — **exactly your trust-ladder primitive**
- File editing, bash execution, web search, web fetch — built-in
- First-class MCP client (so you can plug in `alpaca-py` / market data tools as MCP servers if you want)
- Subagents (for parallel research → trade-proposal patterns)
- Persistent sessions (resume mid-conversation; matches your "drop ad-hoc guidance" requirement)
- Configurable effort levels (`low`/`medium`/`high`/`max`/`xhigh`) — useful for the cost-ceiling constraint in PROJECT.md

**Why it wins over LangGraph/CrewAI for this project:**
- Already inside the Anthropic ecosystem (your stated preference)
- HITL is a first-class primitive — every other framework treats it as an afterthought
- One vendor, one billing relationship, one set of release notes to track
- Built-in MCP support lets you cleanly add tools without a framework switch

### ❌ OpenClaw.ai — DO NOT USE (HIGH confidence on the "no")

**What it actually is:**
- Open-source **personal AI assistant** built by Peter Steinberger (`@steipete`)
- Launched November 2025; 247k GitHub stars by March 2026 ("fastest-growing project in GitHub history" per coverage)
- TypeScript/Node.js, requires Node 24 or 22.19+
- MIT licensed
- Steinberger joined OpenAI in 2026 and moved OpenClaw to a foundation; the project continues independently

**What it's designed for:** Multi-channel personal assistant — WhatsApp, Telegram, Discord, Slack, Signal, iMessage. Voice on macOS/iOS/Android. Inbox-clearing, calendar management, flight status, smart home, document processing.

**Why it's a bad fit for Project Gekko:**
1. **Wrong shape.** It's a *chat gateway* assistant, not an orchestration framework. Your project already has the chat surface covered (Slack DM via Bolt). You don't need a meta-gateway.
2. **TypeScript/Node, not Python.** Adopting it forces you off Python — which means losing `alpaca-py`, `ib_async`, `schwab-py`, the entire quant ecosystem. Massive technical debt to take on.
3. **No native HITL primitive for financial actions.** Built around "do tasks for me," not "propose trades, await approval, log audit trail."
4. **Not Anthropic-native.** Steinberger is now at OpenAI; the project leans agnostic-but-OpenAI-flavored. Directly contradicts your "stay in Anthropic ecosystem" constraint.
5. **Hype curve, not product fit.** It's the hottest open-source project of 2026, but "popular" ≠ "right for autonomous trading."

**Verdict:** Skip. If you want a personal assistant on top of your messaging apps, OpenClaw is great. For Project Gekko, it would be re-platforming onto the wrong tool.

### ❌ NVIDIA NeMo-Claw / NeMo Agent Toolkit — DO NOT USE (HIGH confidence on the "no")

**What it actually is:**
- **NVIDIA NeMo Agent Toolkit** (the broader product, v1.7 as of June 2026): enterprise observability, profiling, routing, and orchestration *wrapper* around existing agent frameworks (LangChain, LlamaIndex, CrewAI, Semantic Kernel, Google ADK, custom)
- **NVIDIA NemoClaw** (the GitHub repo): a *blueprint / deployment harness* that runs **Hermes agents (Nous Research) and OpenClaw agents** "more securely inside NVIDIA OpenShell with managed inference"
- So NemoClaw is literally "OpenClaw + Hermes, but inside NVIDIA's enterprise stack." It is not its own agent framework.

**Why it's a bad fit:**
1. **It runs OpenClaw.** Everything wrong with OpenClaw above applies, plus a NVIDIA infrastructure dependency.
2. **Enterprise positioning.** Designed for "secure, autonomous AI workers that scale" — Fortune-500 problems, not "me + a few friends on a Mac Mini." Massive overkill.
3. **NVIDIA inference dependency.** "Managed inference" inside "NVIDIA OpenShell" pushes you toward NVIDIA-hosted models / their cloud. Contradicts your no-cloud-for-v1 constraint and your Anthropic-ecosystem preference.
4. **Doesn't talk to Claude.** Verified: NemoClaw documentation contains zero mentions of Claude or Anthropic integration.
5. **It's a wrapper, not a replacement.** Even if you used it, you'd still need a base framework (LangChain/CrewAI/etc.) underneath — adding two layers of complexity to solve a problem you don't have.

**Verdict:** Skip. NeMo Agent Toolkit is real and useful for enterprises running fleets of agents who want NVIDIA-native observability. For a single self-hosted trading agent on a Mac Mini, it's wrong on every axis.

### ❌ LangGraph — Mentioned for completeness (HIGH confidence on the "no")

Excellent state-machine agent framework. But: leaves the Anthropic ecosystem (LangChain-flavored), brings LangSmith-or-nothing observability, and gives you nothing for HITL trade approval that the Agent SDK doesn't already provide. **Skip unless** you outgrow the Agent SDK's state-management story — at which point reconsider.

### ❌ CrewAI — Mentioned for completeness (HIGH confidence on the "no")

Multi-agent role-playing framework. Useful when you genuinely need 5+ specialized agents collaborating. Your project is one trading agent per user; you don't need a "crew." Skip.

### ⚠️ OpenBB Platform — Don't use as orchestration; CONSIDER for data (MEDIUM confidence)

OpenBB is a **financial data platform**, not an agent framework — the comparison was a category error. It aggregates ~100 data sources behind one consistent API and exposes MCP servers. It's worth a second look in Phase 2 if Finnhub + Alpha Vantage + EDGAR turn out to be insufficient and you want one unified data layer. But don't adopt it day-one — its workspace UI is opinionated and heavy, and most of its value over individual provider SDKs only materializes once you've outgrown the free tiers.

---

## Detailed Evaluation: Browser-Fallback Path (Robinhood + Fidelity)

The PROJECT.md is explicit: browser-fallback is **second-class** — more retries, screenshot logging, easier to disable per-broker. Pick tools accordingly.

### ✅ `browser-use` — PRIMARY (HIGH confidence)

- 97.7k GitHub stars, MIT, actively maintained (release May 26 2026)
- Python-native Playwright wrapper purpose-built for LLM-driven browser automation
- Pluggable LLM backend — use Claude Sonnet 4.6 here, which per Anthropic's own evals produces zero hallucinated links in computer-use tasks (vs. ~1 in 3 previously)
- Built-in screenshot/trace logging — matches your "screenshot logging" requirement
- Three modes: isolated local browsers, **real Chrome profiles** (preserves broker login sessions across runs), cloud-hosted remote

**Why this and not the alternatives:** It's a Python library you can drop into the agent runtime. No separate Chrome extension UX problem, no enterprise-only Claude-for-Chrome dependency, no token-explosion problem of Playwright MCP.

### ⚠️ Claude for Chrome (Anthropic) — DON'T USE FOR v1 (HIGH confidence on "wait")

- Still beta as of December 2025; **not generally available**. Available to Pro/Max/Team/Enterprise subscribers
- Browser-extension architecture (`chrome-extension://` context) creates connection-instability issues per current reviews
- Anthropic has publicly cited unresolved prompt-injection vulnerabilities as the gating issue for GA
- Designed for interactive coding-assistant use, not unattended overnight agent runs

**Reconsider when:** GA + Anthropic publishes financial-action prompt-injection mitigations.

### ⚠️ Anthropic Computer Use API directly — POWERFUL BUT EXPENSIVE (MEDIUM confidence)

- Beta as of April 2026 (`computer-use-2025-11-24` header) on Sonnet 4.x
- $0.50–$5 per task vs. ~$0 for deterministic Playwright scripts
- Use **only** when `browser-use` can't reliably handle a flow (broker UI weirdness)

### ✅ Raw Playwright — KEEP IN TOOLBOX (HIGH confidence)

- Once a Robinhood/Fidelity flow stabilizes (login → place order → confirm), **hard-code it in deterministic Playwright** and only fall back to `browser-use`/Computer Use when the deterministic path fails
- Cost: $0. Speed: milliseconds. Reliability: high until UI changes
- This is the "graduate to autonomy" pattern but for browser scripts, mirroring your trust-ladder

### ❌ `robin_stocks` / `pyrh` — DON'T USE (HIGH confidence)

Unofficial reverse-engineered HTTP API wrappers. Robinhood does NOT publish a documented retail API. These libraries break whenever Robinhood changes internal endpoints/headers/auth. **PROJECT.md explicitly chose the browser-fallback path** over this approach — honor that decision. Don't accidentally drift into using these "just for now."

---

## Detailed Evaluation: Brokerage SDKs

### Alpaca → `alpaca-py` (HIGH confidence)

- Official SDK, actively maintained (last commit May 31 2026 per repo)
- Supports trading + market data + paper trading sandbox
- Free real-time IEX data, free paper trading — **this is why Alpaca is the right first broker** for the project
- Python 3.7+, Pydantic-based models
- **Do NOT use `alpaca-trade-api`** (the older SDK) — superseded since 2023

### Interactive Brokers → `ib_async` + IB Gateway (HIGH confidence)

- `ib_async` is the maintained successor to `ib_insync` (original author Ewald de Wit passed away in early 2024; project renamed and moved to the `ib-api-reloaded` GitHub org)
- Wraps IBKR's official `ibapi` (which is painful sync-only Java-flavored code) with a clean Python sync/async API
- **Operational reality:** IBKR requires you to run TWS (Trader Workstation) or IB Gateway locally on the same machine. Plan for this in process supervision — it's another process to keep alive
- IBKR has daily 5am ET auto-disconnect — schedule re-auth

### Schwab → `schwab-py` (MEDIUM confidence — Schwab API itself is the bottleneck)

- `schwab-py` is the most mature unofficial SDK; alternatives `Schwabdev`, `pythonic-schwab-api`, `pyschwab` all exist with smaller communities
- Per-user OAuth setup is annoying: each user must register their own app at `developer.schwab.com` with callback URL `https://127.0.0.1:8182`, wait for "Ready for use" status. Document this clearly in onboarding.
- Schwab's API is well-known to be less stable than Alpaca's — bake in retry/backoff and don't make Schwab the critical path

### Robinhood & Fidelity → Browser-fallback only (per PROJECT.md)

See browser-automation section above.

---

## Detailed Evaluation: Market Data

| Source | Free Tier | What It's For | Notes |
|---|---|---|---|
| **Alpaca data API** (via `alpaca-py`) | Free IEX real-time + historical | Primary prices + bars | Comes free with Alpaca account. Use first. |
| **`yahooquery`** | Free, uses official endpoints | Backup prices, fundamentals, holders, earnings calendar | **NOT `yfinance`** — `yfinance` scrapes HTML and gets 429-rate-limited constantly in 2026. `yahooquery` uses Yahoo's documented endpoints. |
| **Finnhub** | 60 req/min free | News, social sentiment, insider transactions, congressional trades | Most generous free tier. Best free news + sentiment. |
| **Alpha Vantage** | 25 req/day free | Technical indicators, earnings, FX | Free tier is *day* not minute — use sparingly. Has an official MCP server (rare). |
| **SEC EDGAR** | Free, unlimited (with `User-Agent` header) | 10-K, 10-Q, 8-K, Form 4 (insider), filing search | Direct REST API — no library needed (`httpx` is enough). Fundamentals truth source. |
| **Polygon ("Massive")** | No free tier — $99/mo Basic, $199/mo Advanced | Tick data, low-latency WebSockets | **Defer.** Free stack is sufficient for swing/long-horizon strategies (which PROJECT.md scopes to). Revisit if/when day-trading creeps in (which is out of scope). |
| **Financial Modeling Prep (FMP)** | Limited free, paid for full | Alternative fundamentals + SEC | Polygon alternative. Defer like Polygon. |

---

## Detailed Evaluation: Backend Runtime — Python vs Node

**Verdict: Python. HIGH confidence.**

| Dimension | Python | Node.js |
|---|---|---|
| Claude Agent SDK | First-class, actively developed | Also available but features lag |
| Alpaca SDK | `alpaca-py` — official, mature | `@alpacahq/alpaca-trade-api-js` — older, less actively developed |
| IBKR | `ib_async` — clean Python wrapper | Native API requires TWS bridge, third-party Node libs are immature |
| Schwab | `schwab-py` mature | No comparable Node lib |
| Browser automation | `browser-use` (Python-native) | `browser-use` works via subprocess; less natural |
| Quant ecosystem | pandas/numpy/numba/polars/duckdb/scikit-learn | Effectively nonexistent |
| SEC EDGAR / data plumbing | `sec-edgar-api`, `yahooquery`, `finnhub-python`, `alpha_vantage` all Python-native | Patchy |
| Cross-platform deployment | Excellent (Python ships everywhere) | Excellent |
| Async story | `asyncio` mature, used by FastAPI, Agent SDK, alpaca-py | Native event loop |

Python wins on every line that matters except "Node has a slightly nicer single-process event loop," which doesn't move the needle for swing-trading cadence.

---

## Detailed Evaluation: Storage

**Verdict: SQLite (with WAL) + DuckDB for analytics. HIGH confidence.**

| Pick | Use For |
|---|---|
| **SQLite (WAL mode)** | All transactional writes: users, sessions, strategies, trade proposals, trade fills, audit log, encrypted credentials |
| **DuckDB** | Analytical queries the dashboard needs: P&L over time, position-by-sector breakdowns, performance vs. benchmark. DuckDB reads SQLite files directly. |

**Why this and not Postgres:**
- Self-hosted Mac Mini / Windows machine — adding a Postgres process to supervise is real ops overhead
- "Me + a few people" multi-user scale fits comfortably in SQLite WAL mode (handles ~1000 writes/sec)
- Backups = `sqlite3 .backup` or a file copy
- Easy to migrate to Postgres later if you outgrow it (SQLAlchemy + small migration effort)

**Why not Postgres-from-day-one:** Real benefit is at higher concurrency / larger data scales you're not going to hit. Cost is another process to babysit. Not worth it for v1.

**Why not DuckDB as primary:** DuckDB is OLAP-first — column store, optimized for big analytical scans. Point lookups against indexed rows are slower than SQLite. You want SQLite as the source of truth and DuckDB as a read-side analytics engine.

---

## Detailed Evaluation: Web Dashboard

**Verdict: FastAPI + HTMX + Tailwind + Jinja2. HIGH confidence.**

For "me + a few people" scale, Next.js is overkill:
- Next.js ships 100-300KB JS to clients; HTMX ships ~35-40KB total
- Next.js requires a Node toolchain in production; HTMX needs zero build step
- You already have FastAPI for the API layer — HTMX lets you reuse FastAPI routes to return HTML fragments

For real-time updates (trade proposals appearing on the dashboard the moment the agent generates them), use **SSE (Server-Sent Events) via `sse-starlette`** + HTMX's SSE extension. Lower complexity than WebSockets and unidirectional flow matches your needs.

**Reconsider Next.js or SvelteKit if:** You add a public-facing landing page or marketing surface (PROJECT.md says you won't — no public SaaS in scope). Or if you hire a frontend engineer who wants React.

---

## Detailed Evaluation: Auth for Multi-User with Broker-Key Isolation

**Verdict: `fastapi-users` + magic-link + Fernet-encrypted credential blobs keyed by user. MEDIUM confidence.**

**Architecture:**

```
                    ┌─────────────────────────────────────────┐
                    │  OS Keychain (macOS Keychain / Windows  │
                    │  Credential Manager) — via `keyring`    │
                    │  Stores: MASTER_KEY (Fernet key)        │
                    └─────────────────────────────────────────┘
                                       │
                                       │ decrypts
                                       ▼
                    ┌─────────────────────────────────────────┐
                    │  SQLite table: user_credentials         │
                    │  ┌────────┬────────┬──────────────────┐ │
                    │  │user_id │broker  │encrypted_blob    │ │
                    │  │  alice │alpaca  │gAAAAABm...       │ │
                    │  │  alice │ibkr    │gAAAAABm...       │ │
                    │  │  bob   │schwab  │gAAAAABm...       │ │
                    │  └────────┴────────┴──────────────────┘ │
                    └─────────────────────────────────────────┘
```

**Flow:**
1. On app start, read `MASTER_KEY` from OS keychain (one-time setup: `keyring.set_password("project-gekko", "master", <key>)`)
2. Per request, look up the encrypted credential blob for `(user_id, broker)` and decrypt with `Fernet(MASTER_KEY)`
3. Pass decrypted credentials to the broker SDK; **never log them, never put them in error messages, never write to disk**

**Auth flow:**
- `fastapi-users` provides user model, registration, session cookies
- Add a magic-link strategy (small custom extension; the linked guide does this in ~50 lines)
- All API routes require authenticated session; every DB query filters by `user_id` (multi-tenant via shared-DB shared-table model — simplest, fine for small N)

**Why not enterprise auth (PropelAuth/Auth0/WorkOS):** Overkill for "me + a few people" — those add a monthly bill and an outside-vendor dependency for zero benefit at your scale.

**Reconsider:** If user count exceeds ~20, or if you want to onboard non-technical users who can't reset their own magic-link inbox.

---

## Detailed Evaluation: Email Digests

**Verdict: Resend. HIGH confidence.**

- 3,000 emails/month free, **permanent** (not a 30-day trial)
- Modern Python SDK
- Built by the team that built React Email — best developer experience in the space
- Best deliverability among the modern players

| Service | Free tier | Why not picked |
|---|---|---|
| Postmark | 100/mo trial only | Trial, not production |
| SendGrid | 100/day | Daily caps punish growth bursts |
| Self-hosted (Postfix) | Free | Deliverability is a nightmare; not worth the hours |

---

## Detailed Evaluation: Process Supervision + Scheduler

### Process supervision: launchd (macOS) + NSSM (Windows). HIGH confidence.

| Platform | Tool | Why |
|---|---|---|
| **macOS (Mac Mini)** | **launchd** with a `~/Library/LaunchAgents/com.gekko.agent.plist` | Native macOS service manager. Runs at login, auto-restart on crash, log capture built in. |
| **Windows** | **NSSM (Non-Sucking Service Manager)** | The accepted standard for "make a Python script a real Windows service." Auto-restart, integrates with Service Control Manager, runs before login. |
| **Linux** (if added later) | **systemd** | Standard. Not needed for v1 per PROJECT.md. |

Wrap the **same Python entrypoint** under both; the only difference is the service definition file. Use `structlog` JSON output and have both supervisors capture stdout/stderr to log files.

### Scheduler: APScheduler 4.x in-process. HIGH confidence.

- Persistent job store in SQLite (same DB as the rest of the app)
- Supports cron-style schedules (open/midday/close), interval triggers, and one-shot events
- For event-driven triggers (news webhooks, price alerts), receive them on FastAPI endpoints → enqueue an immediate-fire APScheduler job
- **Don't use Celery + Redis** — that's "we have 50 workers across 10 machines" infrastructure. You have one machine.

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| Claude Agent SDK | LangGraph | If you need explicit state-machine modeling that the Agent SDK can't express; reassess after Phase 2 |
| Claude Agent SDK | CrewAI | If you split the agent into 5+ specialized collaborating agents (you won't in v1) |
| Python | Node.js | If the team is Node-only AND you can live without `alpaca-py`/`ib_async`/`schwab-py` quality (you can't) |
| FastAPI + HTMX | Next.js | If you add a public marketing surface or hire a React-only frontend engineer |
| FastAPI + HTMX | SvelteKit | If you fall in love with Svelte; technically fine but adds Node build step |
| SQLite | Postgres | When user count or query concurrency outgrows SQLite (~50+ heavy users or background workers writing aggressively) |
| SQLite | DuckDB as primary | Never as primary OLTP — it's OLAP-first |
| `browser-use` | Raw Playwright | For *stable* broker flows where you've validated the script and want deterministic + cheap execution |
| `browser-use` | Anthropic Computer Use API direct | When `browser-use` can't handle a flow; accept the cost |
| Resend | Postmark | If deliverability becomes a real problem (Resend is good; Postmark is best-in-class) |
| `alpaca-py` data | Polygon ("Massive") | When free real-time IEX isn't enough and you need full SIP/options L2 — defer until justified |
| Finnhub | NewsAPI / GDELT | When Finnhub's news coverage shows blind spots — unlikely at v1 scope |
| Magic-link auth | PropelAuth / WorkOS | If user count > ~20 or you add SSO / org-level features |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| **OpenClaw.ai** | Personal-assistant chat gateway, not an agent orchestration framework. TypeScript-native (forces you off Python). Not Anthropic-aligned (creator now at OpenAI). Hype, not fit. | Claude Agent SDK |
| **NVIDIA NeMo-Claw / NeMo Agent Toolkit** | Enterprise wrapper around LangChain/CrewAI/OpenClaw + NVIDIA inference dependency. Massive overkill. No Claude integration. Pushes you to NVIDIA cloud. | Claude Agent SDK |
| **`claude-code-sdk`** (the old PyPI package) | Deprecated; superseded by `claude-agent-sdk` | `claude-agent-sdk` |
| **`alpaca-trade-api`** (the old SDK) | Superseded by `alpaca-py` since 2023 | `alpaca-py` |
| **`ib_insync`** | Original author passed away in early 2024; project archived. | `ib_async` (community-maintained successor) |
| **Raw `ibapi`** | Painful sync-only API with Java-flavored idioms | `ib_async` (wraps it cleanly) |
| **`yfinance`** | Scrapes Yahoo's HTML; rate-limited (HTTP 429) constantly in 2026; brittle | `yahooquery` (uses official endpoints) — or just lean on Alpaca data |
| **`robin_stocks` / `pyrh` as the long-term Robinhood path** | Unofficial reverse-engineered API wrappers; PROJECT.md explicitly chose the browser-fallback path. They will break unpredictably when Robinhood changes internals. | `browser-use` driving Robinhood UI |
| **Polygon ($99+/mo) at v1** | Real-time tick data you don't need for swing-horizon strategies; sunk cost too early | Alpaca free IEX data; revisit when justified |
| **Celery + Redis** | Distributed task queue overhead for a single-machine deployment | APScheduler 4.x in-process with SQLite job store |
| **Postgres at v1** | Another process to supervise; SQLite WAL handles your scale fine | SQLite WAL + DuckDB for analytics |
| **Next.js for the dashboard** | 100-300KB JS bundle, Node build toolchain, SPA tax for "me + a few people" | FastAPI + HTMX + Jinja2 |
| **Auth0 / Clerk / WorkOS at v1** | Enterprise SSO platforms; monthly bill for zero benefit at your scale | `fastapi-users` + magic-link + Fernet credential encryption |
| **SendGrid free tier** | 100/day cap punishes any user-burst situation | Resend (3,000/mo, permanent) |
| **Task Scheduler (Windows) / cron (macOS)** as the supervisor | These start jobs but don't supervise — no auto-restart, no log capture, no "run before login" reliably | NSSM (Windows) / launchd (macOS) |

---

## Stack Patterns by Variant

**If user has Alpaca:**
- `alpaca-py` for trading + data
- Paper trading account first (per HITL + trust-ladder requirement)

**If user has IBKR:**
- `ib_async` + run IB Gateway as a side-by-side process (NSSM/launchd job)
- Watch for 5am ET daily auto-disconnect

**If user has Schwab:**
- `schwab-py` + each user registers their own developer app at `developer.schwab.com`
- Document the OAuth flow clearly in onboarding

**If user has Robinhood or Fidelity:**
- `browser-use` driving real Chrome profile (preserves session/2FA)
- Screenshot every action; log to structured trace file
- Treat as second-class per PROJECT.md — degraded mode is acceptable

**If user is on macOS Mac Mini:**
- launchd LaunchAgent (`~/Library/LaunchAgents/com.gekko.agent.plist`)
- Master key in macOS Keychain via `keyring`

**If user is on Windows:**
- NSSM-installed service running the same Python entrypoint
- Master key in Windows Credential Manager via `keyring`

**If LLM cost is approaching ceiling:**
- Drop Agent SDK effort level: `xhigh` → `high` → `medium`
- Slow scheduler cadence
- Reduce browser-fallback retries (browser flows are the priciest path because they invoke Computer Use / browser-use loops)

---

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| `claude-agent-sdk` 0.2.x | Python ≥ 3.10 | Pin to a specific minor — releases ship multiple times per week; pin and update deliberately |
| `alpaca-py` 0.32.x | Python 3.7+ | Use the v2 trading + market data APIs (v1 is deprecated) |
| `ib_async` 1.x | Python 3.10+ | Requires TWS or IB Gateway 10.20+ running locally |
| `browser-use` latest | Python 3.11+ | Requires Playwright + `playwright install chromium` |
| FastAPI 0.115.x | Pydantic 2.x, Starlette 0.4x | Standard FastAPI 2026 stack |
| `fastapi-users` 14.x | FastAPI 0.115+, SQLAlchemy 2.x | Use the SQLAlchemy adapter for SQLite |
| HTMX 2.0.x | Any backend | Pin to 2.x — 1.x extension URLs differ |
| APScheduler 4.x | Python 3.9+ | v4 is a substantial rewrite from v3 — read migration notes if you ever see v3 examples |
| Sonnet 4.6 | Computer Use beta header `computer-use-2025-11-24` | Required for any Computer Use API direct calls |

---

## Sources

### Orchestration
- [Claude Agent SDK on PyPI](https://pypi.org/project/claude-agent-sdk/) — version 0.2.93 verified, released 2026-06-06 — **HIGH confidence**
- [anthropics/claude-agent-sdk-python (GitHub)](https://github.com/anthropics/claude-agent-sdk-python) — HIGH confidence
- [Claude Agent SDK Python docs](https://code.claude.com/docs/en/agent-sdk/python) — HIGH confidence
- [OpenClaw GitHub repo](https://github.com/openclaw/openclaw) — verified TypeScript/Node project, MIT, personal assistant focus — HIGH confidence
- [OpenClaw.ai homepage](https://openclaw.ai/) — confirms personal-assistant positioning — HIGH confidence
- [NVIDIA NeMo Agent Toolkit (developer.nvidia.com)](https://developer.nvidia.com/nemo-agent-toolkit) — confirms wrapper-around-other-frameworks architecture — HIGH confidence
- [NVIDIA/NemoClaw GitHub](https://github.com/NVIDIA/NemoClaw) — confirms it runs Hermes + OpenClaw agents — HIGH confidence

### Brokerage
- [alpaca-py GitHub](https://github.com/alpacahq/alpaca-py) — active, May 31 2026 commit — HIGH confidence
- [ib_async GitHub (ib-api-reloaded)](https://github.com/ib-api-reloaded/ib_async) — confirmed successor to ib_insync — HIGH confidence
- [schwab-py docs](https://schwab-py.readthedocs.io/en/latest/getting-started.html) — MEDIUM confidence (unofficial)

### Browser automation
- [browser-use GitHub](https://github.com/browser-use/browser-use) — 97.7k stars verified — HIGH confidence
- [Anthropic Computer Use docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool) — beta status confirmed — HIGH confidence
- [Piloting Claude in Chrome (Anthropic)](https://www.anthropic.com/news/claude-for-chrome) — beta only, not GA — HIGH confidence
- [Browser Automation in Claude Code: 5 Tools Compared (2026)](https://www.heyuan110.com/posts/ai/2026-01-28-claude-code-browser-automation/) — MEDIUM confidence (independent review)

### Market data
- [yahooquery vs yfinance discussion (Medium)](https://medium.com/@trading.dude/why-yfinance-keeps-getting-blocked-and-what-to-use-instead-92d84bb2cc01) — MEDIUM confidence
- [Best Free Stock Market APIs 2026 (DEV.to)](https://dev.to/nexgendata/best-free-stock-market-apis-and-data-tools-in-2026-a-developers-honest-comparison-1926) — MEDIUM confidence
- [robin_stocks PyPI](https://pypi.org/project/robin-stocks/) — confirms unofficial status — HIGH confidence

### Storage / runtime
- [DuckDB vs SQLite vs PostgreSQL 2026 (AI2SQL)](https://builder.ai2sql.io/blog/duckdb-vs-sqlite-vs-postgresql) — MEDIUM confidence
- [DuckDB sqlite_scanner extension (DuckDB docs)](https://duckdb.org/why_duckdb) — HIGH confidence

### Web / auth / email / supervision
- [FastAPI + HTMX patterns (TestDriven.io)](https://testdriven.io/blog/fastapi-htmx/) — HIGH confidence
- [Resend free tier (Resend docs)](https://automationatlas.io/answers/resend-free-tier-explained-2026/) — HIGH confidence
- [Top 5 FastAPI auth solutions 2026 (WorkOS)](https://workos.com/blog/top-authentication-solutions-fastapi-2026) — MEDIUM confidence
- [NSSM Windows service guide](https://www.mssqltips.com/sqlservertip/7325/how-to-run-a-python-script-windows-service-nssm/) — HIGH confidence

---

*Stack research for: Project Gekko (autonomous stock trading agent, Anthropic-ecosystem-first)*
*Researched: 2026-06-08*
*Confidence: HIGH on core decisions; MEDIUM on commodity layers (auth/email) and rapidly-evolving layers (browser fallback)*
