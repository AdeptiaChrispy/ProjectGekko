# Architecture Research

**Domain:** Autonomous LLM-powered trading agent (multi-user, self-hosted)
**Researched:** 2026-06-08
**Confidence:** MEDIUM-HIGH (component design HIGH from ecosystem patterns; specific library choices MEDIUM pending POC; broker-fallback design LOW until Robinhood/Fidelity sessions are tested in practice)

---

## TL;DR (for the roadmap)

- **Single Python process, modular monolith.** One `asyncio` event loop, one process, one SQLite (SQLCipher) data store. Do **not** split into microservices for "me + a few people on one machine" — that's premature distributed-systems tax. Decompose into **modules with clean interfaces** so future extraction is cheap.
- **Claude Agent SDK is the orchestration core.** The agent loop (research → propose) runs as a `query()` against the SDK, with each external capability (market data, news, broker, web research) exposed as an **in-process MCP tool**. Researcher and Trader are SDK **subagents** with isolated context windows; Executor is **not** an LLM — it's deterministic code that the human (or an auto-execute policy) authorizes.
- **HITL approval = Slack interactive buttons (primary) + web dashboard (fallback).** The agent **does not block** waiting for approval. It posts a proposal, persists state to `trade_proposals` table, and **returns control**. A separate `ApprovalReceiver` webhook resumes the workflow when the user clicks Approve/Reject. Default timeout: 30 minutes → auto-reject with notification.
- **Broker abstraction is the load-bearing interface of the project.** A `Broker` ABC with ~10 methods covers Alpaca, IBKR, Schwab, and (via a `BrowserBroker` adapter) Robinhood/Fidelity. Get this right early; every other component depends on it.
- **Credentials live in SQLCipher.** One encrypted SQLite database, AES-256, key derived from a passphrase entered at service start (and held in memory only). No OS keychain dependency (breaks cross-platform Mac/Windows parity). No plaintext `.env` per user.
- **Build order: thin vertical slice through paper-trading on Alpaca, single user, single strategy, Slack HITL, no autonomy.** Get one end-to-end loop working before adding multi-user, autonomy, browser-fallback brokers, or the dashboard.

---

## System Overview

```
┌────────────────────────────────────────────────────────────────────────────┐
│                          USER INTERFACE LAYER                               │
│  ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────────┐    │
│  │   Slack DM Bot   │   │  Web Dashboard   │   │   Email Digest       │    │
│  │ (Bolt + FastAPI) │   │  (FastAPI + HTMX │   │  (daily/weekly)      │    │
│  │  - proposals     │   │   or Next.js)    │   │                      │    │
│  │  - approvals     │   │  - portfolio     │   │                      │    │
│  │  - ad-hoc chat   │   │  - strategy edit │   │                      │    │
│  │  - daily P&L     │   │  - trade history │   │                      │    │
│  └────────┬─────────┘   └────────┬─────────┘   └──────────┬───────────┘    │
└───────────┼──────────────────────┼─────────────────────────┼────────────────┘
            │                      │                         │
┌───────────┴──────────────────────┴─────────────────────────┴────────────────┐
│                       APPLICATION CORE (single Python process)              │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                       Scheduler (APScheduler)                         │  │
│  │   - Cron triggers (market open/midday/close per user/strategy)        │  │
│  │   - Event triggers (news/price-move webhooks → queue)                 │  │
│  │   - Cooldown enforcement (cost budget gate)                           │  │
│  └────────────────────────────────┬─────────────────────────────────────┘  │
│                                   │ enqueue run                              │
│  ┌────────────────────────────────▼─────────────────────────────────────┐  │
│  │                  Strategy Runtime (per-user asyncio task)             │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │  │
│  │  │   Claude Agent SDK query() — main loop                          │ │  │
│  │  │   ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │ │  │
│  │  │   │  Researcher  │───▶│    Trader    │───▶│  Proposal Writer │  │ │  │
│  │  │   │  (subagent)  │    │  (subagent)  │    │  (deterministic) │  │ │  │
│  │  │   │  context=    │    │  context=    │    │                  │  │ │  │
│  │  │   │  market+news │    │  positions+  │    │                  │  │ │  │
│  │  │   │              │    │  risk caps   │    │                  │  │ │  │
│  │  │   └──────┬───────┘    └──────┬───────┘    └────────┬─────────┘  │ │  │
│  │  └──────────┼───────────────────┼─────────────────────┼────────────┘ │  │
│  │             │ uses MCP tools    │ uses MCP tools      │              │  │
│  └─────────────┼───────────────────┼─────────────────────┼──────────────┘  │
│                │                   │                     │                  │
│  ┌─────────────▼───────────────────▼─────────────────────▼──────────────┐  │
│  │                    In-Process MCP Tool Layer                          │  │
│  │  ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌──────────┐ ┌──────────┐  │  │
│  │  │ Market   │ │ News /   │ │ SEC EDGAR  │ │ Web      │ │ Broker   │  │  │
│  │  │ Data     │ │ Sentiment│ │ Funda-     │ │ Research │ │ (read    │  │  │
│  │  │ (Alpaca/ │ │ (Finnhub)│ │ mentals    │ │ (Claude- │ │ only:    │  │  │
│  │  │  yfinance)│ │          │ │            │ │ for-     │ │ positions│  │  │
│  │  │          │ │          │ │            │ │ Chrome)  │ │ /quotes) │  │  │
│  │  └──────────┘ └──────────┘ └────────────┘ └──────────┘ └──────────┘  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────────────────┐ │
│  │  Executor        │  │  Approval        │  │  Reporter                  │ │
│  │  (deterministic) │  │  Receiver        │  │  - Slack DM                │ │
│  │  - validates     │  │  (webhook)       │  │  - Email                   │ │
│  │    caps          │  │  - Slack action  │  │  - Dashboard updates       │ │
│  │  - calls Broker  │  │  - Web POST      │  │                           │ │
│  │  - records trade │  │  - resumes flow  │  │                           │ │
│  └────────┬─────────┘  └────────┬─────────┘  └───────────────────────────┘ │
│           │                     │                                          │
│  ┌────────▼─────────────────────▼──────────────────────────────────────┐  │
│  │                       Broker Abstraction                            │  │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌────────────┐ │  │
│  │  │ AlpacaBroker │ │  IBKRBroker  │ │ SchwabBroker │ │ Browser-   │ │  │
│  │  │ (alpaca-py)  │ │ (ib_async)   │ │ (schwab-py + │ │ Broker     │ │  │
│  │  │              │ │              │ │  OAuth)      │ │ (Claude-   │ │  │
│  │  │              │ │              │ │              │ │ for-Chrome)│ │  │
│  │  └──────────────┘ └──────────────┘ └──────────────┘ └────────────┘ │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
            │
┌───────────▼──────────────────────────────────────────────────────────────────┐
│                              DATA LAYER                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                  SQLCipher (encrypted SQLite)                        │    │
│  │   users · strategies · portfolios · positions · trades              │    │
│  │   trade_proposals · audit_log · broker_credentials (blob)           │    │
│  │   agent_sessions · cost_ledger                                       │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │              Claude SDK session files (~/.claude/projects/)         │    │
│  │              (per-user-strategy conversation JSONL)                 │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │              Browser profiles (~/.gekko/browser-profiles/<user>/)   │    │
│  │              (encrypted Chrome user-data dirs for browser-broker)   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Responsibilities

| Component | Responsibility | Implementation |
|-----------|----------------|----------------|
| **Scheduler** | Fires strategy runs on cron + event triggers; enforces cooldowns and cost budget gates | `APScheduler` `AsyncIOScheduler` with `SQLAlchemyJobStore` |
| **Strategy Runtime** | One asyncio task per (user, strategy, run) — owns the lifecycle of one research+propose cycle | Plain `async def run_strategy(user_id, strategy_id)` invoked by scheduler |
| **Researcher (subagent)** | Gathers market data, news, fundamentals, web research; produces a research brief | Claude Agent SDK subagent with read-only MCP tools |
| **Trader (subagent)** | Consumes research brief + current portfolio + risk caps; proposes specific trades with rationale | Claude Agent SDK subagent with portfolio-read MCP tools |
| **Proposal Writer** | Deterministic code that serializes Trader's output into a `TradeProposal` row | Plain Python; validates schema |
| **Approval Receiver** | HTTP endpoint that accepts Slack interaction payloads and web dashboard POSTs; flips proposal status | FastAPI route + Slack Bolt `AsyncSlackRequestHandler` |
| **Executor** | Validates approved proposal against hard caps; calls `Broker.place_order`; records the trade | Plain Python state machine; **no LLM** in this path |
| **Reporter** | Sends Slack DMs, web dashboard pushes, email digests for proposals, executions, daily P&L | Slack Bolt async client + Jinja for emails |
| **Broker Abstraction** | Uniform interface across Alpaca, IBKR, Schwab, browser-driven brokers | ABC + per-broker adapter classes |
| **Credential Vault** | Encrypts/decrypts broker credentials, OAuth tokens, browser cookies on read/write | SQLCipher blob columns + scoped accessor functions |
| **Audit Log** | Append-only record of every agent decision, LLM call, broker call, approval | SQLite table `audit_log`, event-sourced |
| **Web Dashboard** | Portfolio view, trade history, strategy editor, approval UI | FastAPI + HTMX (Phase ≥2) — keep simple |
| **Slack Bot** | Trade proposals with Approve/Reject buttons, ad-hoc chat, daily P&L | `slack_bolt` async with FastAPI adapter |

---

## Single Process vs Multi-Process — Justification

**Decision: Single Python process, modular monolith.**

### Why not microservices

The whole product is "me + a few trusted users on one Mac Mini or Windows box." Microservices solve problems Project Gekko does not have:

- **No independent scaling needs.** All components scale together — one user = one strategy run at a time, bounded by Claude API rate limits, not CPU.
- **No independent deployment needs.** Components ship together as one release; there's no "Researcher team" and "Executor team."
- **No language heterogeneity.** Everything is Python.
- **Microservices add real cost:** IPC, service discovery, distributed tracing, partial failures, deploy orchestration. None of that is free, and none of it pays back at this scale.

### Why a modular monolith works

- **One asyncio event loop** runs the Scheduler, the Strategy Runtime tasks (one per active run), the Slack/web webhook receivers, and the Broker I/O concurrently. Python's GIL is not a bottleneck because the workload is overwhelmingly I/O-bound (HTTP to brokers, news APIs, Claude).
- **One SQLite (SQLCipher) database** keeps transactions cheap and simple. SQLite handles many writers fine via WAL mode; one machine, single-digit users, dozens of trades/day is firmly in SQLite's sweet spot.
- **Module boundaries are enforced by imports + interfaces, not network calls.** This is the right level of decomposition: easy to test, easy to refactor, easy to extract later if needed.

### When to revisit

Split out a component to its own process only when one of these is true:
- The browser-driver path is so flaky it crashes the host process (then sandbox it as a subprocess managed by the main process).
- A user count grows past ~20 and you need to schedule strategy runs across machines.
- A specific broker integration needs a long-running native runtime (e.g., IBKR Gateway is already its own process — that's expected and fine; the `IBKRBroker` adapter just talks to it).

---

## Broker Abstraction Interface (load-bearing)

This is **the** most important interface in the project. Every other component depends on it. Design it before writing the first broker adapter.

### Minimum Viable `Broker` ABC

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional

class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"

class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"

class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"

@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    timestamp: int  # epoch ms

@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: Decimal
    avg_cost: Decimal
    market_value: Decimal
    unrealized_pl: Decimal

@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: OrderSide
    quantity: Decimal
    order_type: OrderType
    limit_price: Optional[Decimal] = None
    time_in_force: TimeInForce = TimeInForce.DAY
    client_order_id: str = ""  # idempotency key — REQUIRED in practice

@dataclass(frozen=True)
class OrderResult:
    broker_order_id: str
    client_order_id: str
    status: str            # "accepted" | "filled" | "partially_filled" | "rejected" | "pending"
    filled_quantity: Decimal
    avg_fill_price: Optional[Decimal]
    raw: dict              # broker-native response, for audit

class BrokerError(Exception): ...
class BrokerAuthError(BrokerError): ...        # creds invalid / OAuth expired
class BrokerConnectivityError(BrokerError): ...# transient, retry
class BrokerOrderRejected(BrokerError): ...    # broker said no (insufficient funds, etc.)
class BrokerSessionExpired(BrokerError): ...   # browser cookie died — needs re-login

class Broker(ABC):
    """Uniform broker interface. Adapters MUST be idempotent on client_order_id."""

    name: str            # "alpaca" | "ibkr" | "schwab" | "robinhood_browser" | ...
    supports_fractional: bool
    supports_extended_hours: bool
    is_browser_driven: bool  # True for Robinhood/Fidelity adapters

    @abstractmethod
    async def health_check(self) -> bool: ...

    @abstractmethod
    async def get_account(self) -> dict:
        """Returns: cash, buying_power, equity, day_trade_count."""

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_quote(self, symbol: str) -> Quote: ...

    @abstractmethod
    async def place_order(self, req: OrderRequest) -> OrderResult:
        """MUST be idempotent on req.client_order_id."""

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> bool: ...

    @abstractmethod
    async def get_order(self, broker_order_id: str) -> OrderResult: ...

    @abstractmethod
    async def list_orders(self, status: Optional[str] = None, since: Optional[int] = None) -> list[OrderResult]: ...
```

### Design principles

1. **Async everywhere.** Even the sync IBKR API and synchronous `schwab-py` get wrapped in async (`asyncio.to_thread`).
2. **`Decimal`, never `float`.** Money math in float = bugs in production.
3. **Idempotent `place_order`.** Every call carries a `client_order_id` derived from the proposal ID. Retries are safe.
4. **Errors are typed.** The Executor handles `BrokerSessionExpired` (notify user, pause strategy) very differently from `BrokerConnectivityError` (retry with backoff).
5. **Read-only methods (`get_quote`, `get_positions`) live on the same interface.** Both Researcher (read) and Executor (write) use the same adapter — fewer ways to drift.
6. **`is_browser_driven` flag** lets the Executor route through extra confirmation/retry logic for fragile paths.
7. **No "trading strategy" or "risk" concepts in this interface.** Those live above the broker layer. The broker just executes orders.

### Browser-driven brokers as a subtype

```python
class BrowserBroker(Broker):
    """Base for Robinhood / Fidelity adapters that drive a Chrome session.

    Adds session-management hooks the framework calls when cookies die.
    """
    is_browser_driven = True

    @abstractmethod
    async def ensure_session(self) -> None:
        """Verify the Chrome profile is logged in. Raise BrokerSessionExpired if not."""

    @abstractmethod
    async def screenshot(self) -> bytes:
        """Capture current page for audit log on every order."""
```

The `BrowserBroker` runs through **Claude-for-Chrome** (or `browser-use` as a fallback library). Every `place_order` takes a before/after screenshot and stores it in the audit log. When the session dies the adapter raises `BrokerSessionExpired` and the user gets a Slack message: "Robinhood session expired — please re-login at <local URL>."

**Critical caveat (LOW confidence):** Robinhood's official "Agentic Trading" product (2025) and third-party MCP servers like `trayd-mcp` mean the browser-fallback approach may be obsoleted by a sanctioned API path for Robinhood specifically. Validate during Phase 1 research before committing engineering time to a Selenium-style Robinhood adapter.

---

## State & Data Flow

### Where state lives

| State | Store | Why |
|-------|-------|-----|
| User accounts, strategies, hard caps | SQLCipher table (encrypted) | Mutable structured config |
| Broker credentials, OAuth tokens, browser cookies | SQLCipher blob columns | Sensitive; isolated per user |
| Trade proposals (pending/approved/rejected) | SQLCipher | State machine — must survive restart |
| Executed trades, fills, P&L history | SQLCipher (append-only) | Authoritative trade record |
| Positions / portfolio | **Broker is source of truth**; SQLite caches last-fetched snapshot | Don't reinvent broker accounting |
| Audit log (every decision, LLM call, broker call) | SQLCipher table (append-only) | Compliance + debugging |
| Cost ledger (Claude token spend per user/day) | SQLCipher | Budget enforcement |
| Agent conversation memory | Claude SDK JSONL session files | SDK handles this natively; one session per strategy |
| Ad-hoc user guidance | SQLCipher `strategy_guidance` table, fed into research prompt as context | Survives restart; auditable |

### End-to-end data flow: "market open → trade → report"

```
1. SCHEDULER (08:30 ET market pre-open)
       │
       ▼
2. STRATEGY RUNTIME spawns asyncio task for (user=chris, strategy=ai-infra-bull)
       │
       │  loads strategy spec + recent guidance from DB
       │  loads current positions via Broker.get_positions()
       │  checks cost budget — if exceeded, downgrades research depth
       ▼
3. CLAUDE SDK query() with RESEARCHER subagent
       │   ┌── tool: get_market_movers       (Alpaca/yfinance)
       │   ├── tool: get_news_for_symbol     (Finnhub)
       │   ├── tool: get_fundamentals        (SEC EDGAR)
       │   └── tool: web_research            (Claude-for-Chrome)
       │
       │  Produces RESEARCH BRIEF: {tickers_of_interest, sentiment, catalysts}
       ▼
4. TRADER subagent (separate context window)
       │   inputs: research brief, current positions, hard caps, recent trades
       │
       │   ┌── tool: get_quote               (read-only Broker.get_quote)
       │   └── tool: get_position_value      (read-only Broker.get_positions)
       │
       │  Produces TRADE PROPOSAL(S): [{symbol, side, qty, limit, rationale}, ...]
       ▼
5. PROPOSAL WRITER (deterministic)
       │  validates: symbol exists, qty > 0, within hard caps
       │  writes row to trade_proposals (status=PENDING)
       │  writes audit_log entry with full rationale
       ▼
6. REPORTER sends Slack DM with Approve/Reject buttons + rationale
       │  Strategy Runtime task EXITS (does not block)
       │
       ──── (time passes — minutes to hours) ────
       │
7. APPROVAL RECEIVER (Slack webhook OR web POST)
       │  verifies signature, looks up proposal by client_order_id
       │  ┌── if APPROVED  → enqueue Executor
       │  ├── if REJECTED  → mark proposal, notify, audit
       │  └── if TIMEOUT (30min default) → auto-reject, notify
       ▼
8. EXECUTOR (deterministic, NO LLM)
       │  re-validates hard caps against CURRENT positions + cash
       │  ┌── if caps now violated  → reject with reason, notify user
       │  └── otherwise → Broker.place_order(...)
       │
       │  handles errors:
       │   - BrokerConnectivityError → retry w/ exponential backoff (3 tries)
       │   - BrokerSessionExpired    → pause strategy, notify, audit
       │   - BrokerOrderRejected     → mark trade as rejected, notify, audit
       │   - OK → record fill, update audit log
       ▼
9. REPORTER sends execution confirmation
       │  Slack: "✅ Bought 50 NVDA @ $XXX.XX — $YYYY"
       │  Dashboard live-updates
       │  End-of-day digest accumulates
       ▼
10. END OF DAY: daily digest email + Slack daily P&L card
```

### Idempotency throughout

- Every proposal has a `client_order_id` (UUID).
- The Executor passes that ID to the broker on `place_order`.
- If the Executor crashes mid-call, restart re-reads `trade_proposals` where `status=APPROVED AND broker_order_id IS NULL` and either completes or queries the broker for the existing order.

---

## Concurrency Model

**Single asyncio event loop, one task per active strategy run.**

```python
# Conceptual; the real version uses APScheduler + a TaskRegistry
async def main():
    scheduler = AsyncIOScheduler()
    scheduler.start()
    fastapi_app = build_app()  # Slack + dashboard + approval routes
    await asyncio.gather(
        uvicorn.run(fastapi_app),
        # Strategy runs are spawned as scheduler jobs into the same loop
    )
```

### Per-user isolation without per-user processes

- Each strategy run executes inside its own `async def` task with **its own DB session, its own Claude SDK session_id, and its own Broker adapter instance** scoped to that user's credentials.
- A `UserContext` object is the explicit handle passed everywhere:

```python
@dataclass(frozen=True)
class UserContext:
    user_id: str
    db: AsyncDBSession         # bound to user_id by query filters
    broker: Broker              # constructed with user's decrypted creds
    sdk_session_id: Optional[str]  # resumed from DB
    cost_budget_remaining: Decimal
    audit: AuditLogger          # tagged with user_id on every write
```

- `Broker` adapters are **constructed per-run** so there's no shared state between users at the broker level. (For IBKR, which uses a TCP socket to IB Gateway, you pool the connection but tag every request with the user's account ID.)

### Scheduled triggers across users

- APScheduler holds one job per (user, strategy, trigger). Job stores in the same SQLCipher DB → survives restart.
- Jobs are keyed `run-{user_id}-{strategy_id}-{trigger}` so duplicate scheduling is impossible.
- A global semaphore (`max_concurrent_runs = 5`) prevents runaway concurrency from N users all triggering at market open.

### Event-driven triggers

- Market data / news webhooks land on a FastAPI route → push to an asyncio queue → a dispatcher fans them out to matching strategies. Per-user backpressure: if a user's queue exceeds N items, drop oldest and log.

---

## HITL Approval Loop Architecture

**This is the most subtle part of the system.** Get it wrong and either (a) Claude burns tokens waiting, or (b) approvals get lost on restart.

### The pattern: state-managed interruption, not blocking await

**Wrong pattern (do not do this):**
```python
# DO NOT do this
proposal = await trader.propose()
approval = await slack.wait_for_button_click(proposal)  # blocks for hours
if approval:
    await broker.place_order(...)
```

This burns Claude context, dies on restart, and breaks any HTTP timeout.

**Right pattern (state-managed):**
```python
# Phase 1: research + propose (sync flow)
proposal = await trader.propose()
proposal_id = await db.save_proposal(proposal, status="PENDING")
await reporter.send_proposal_with_buttons(proposal_id)
return  # strategy task ENDS here

# Phase 2 (separate, triggered by webhook): approval
@app.post("/slack/actions")
async def handle_slack_action(payload):
    proposal_id = payload.action_value
    action = payload.action_id  # "approve" or "reject"
    await db.update_proposal_status(proposal_id, action)
    if action == "approve":
        await task_queue.enqueue(execute_proposal, proposal_id)
```

### Approval channels

| Channel | Mechanism | When |
|---------|-----------|------|
| **Slack DM** | Bolt interactive message with Approve / Reject / Edit buttons | Default for all proposals (per PROJECT.md — Chris lives in Slack) |
| **Web dashboard** | `/approvals` page with same buttons + rationale + market snapshot | Fallback / for users without Slack |
| **No email approval** | Email digest links to dashboard, never embeds approve buttons | Email-based approval is too easy to phish / spoof |

### Timeout behavior

- Default timeout: **30 minutes** for market-hours proposals; **until-next-open** for after-hours proposals.
- Implementation: when a proposal is written, the scheduler creates a one-shot job `expire_proposal(proposal_id)` at `now + timeout`.
- On expiry: if still `PENDING`, flip to `EXPIRED`, notify the user, audit. Strategy gets the result on its next run.
- Per-strategy override: `approval_timeout_seconds` in strategy config.

### Auto-execute mode (post-promotion)

When a strategy is promoted past HITL:
- Proposal is written, but immediately auto-approved if it passes hard caps.
- All auto-approvals still go through the same audit pipeline.
- Daily digest highlights every auto-executed trade — user can still revoke autonomy.
- Hard caps are **always** enforced server-side regardless of autonomy level.

---

## Credential Management & Isolation

### Decision: SQLCipher-encrypted blobs in the main SQLite DB

**Rejected alternatives:**

| Approach | Why not |
|----------|---------|
| OS Keychain (Keychain Access on Mac, Credential Manager on Windows) | Breaks cross-platform parity; the same code can't read creds on both OSes without per-OS branching. Also: silent failures when the service runs without a logged-in user session. |
| `.env` file per user | Plaintext on disk. Unacceptable for broker keys + OAuth tokens. |
| HashiCorp Vault | Massive over-engineering for self-hosted "+ a few people." Adds a whole dependency. |
| Cloud KMS (AWS/Azure) | Contradicts the "no cloud for v1" constraint. |

### Chosen design

- **One SQLCipher database** for everything. Key passphrase entered at service start (or read from `GEKKO_DB_KEY` env var if Chris prefers).
- Passphrase derives a key via `PBKDF2-SHA256` (SQLCipher default, 256k iterations).
- Sensitive fields stored as **blob columns**, additionally encrypted at the application layer with `libsodium` (`nacl.secret.SecretBox`) keyed off a **per-user data key** that itself is encrypted with the master key. This gives "per-user crypto isolation" — a future export-user-data feature can re-key just that user's blobs.

```
master_key (in memory only, derived from passphrase)
    │
    ▼
per_user_data_key (random, generated on user creation, stored encrypted-with-master)
    │
    ▼
broker_credentials (encrypted blob)
oauth_tokens     (encrypted blob)
browser_cookies  (encrypted blob)
```

### Operational rules

- Master key **never logged**, never written to disk in plaintext, never persisted in process memory beyond what `libsodium`'s `SecretBox` needs.
- Logger has a redaction filter — any value matching credential heuristics (looks like a JWT, looks like `sk-...`, etc.) gets `<REDACTED>`.
- Credential reads go through a single `CredentialVault` class; no broker adapter ever reads the encrypted blob directly.
- On user delete, all per-user blobs are zeroed (`UPDATE ... SET blob = randomblob(length(blob))`) before the row is dropped.

### Schwab OAuth specifics

Schwab tokens are **brutal in production**:
- Access tokens: ~30 minutes (auto-refresh).
- Refresh tokens: **7 days** (not 90 — common misconception). After 7 days, the user must re-authenticate via OAuth redirect.

Implication: build a `OAuthCoordinator` that runs hourly, refreshes any token nearing expiry, and posts a Slack DM to the user 24h before refresh-token expiry: "Your Schwab connection expires tomorrow — re-auth here: \<local URL\>." Without this, the agent silently dies for that broker every 7 days.

---

## Observability

### Minimum acceptable telemetry

| Concern | Implementation | Confidence |
|---------|----------------|------------|
| **Structured audit log** | `audit_log` table; every row = one event with `timestamp`, `user_id`, `event_type`, `actor` (`scheduler`/`researcher`/`trader`/`executor`/`human`), `payload_json`, `correlation_id` | HIGH |
| **Trade-decision rationale** | The Trader subagent's full output (research brief + reasoning + proposal JSON) is persisted with the proposal row. Tied to the Claude SDK session ID. | HIGH |
| **Error/exception reporting** | `structlog` JSON logs + Sentry (free tier handles personal scale fine) | HIGH |
| **Cost tracking** | `cost_ledger` table: `(user_id, date, input_tokens, output_tokens, cost_usd)` updated after every `query()` call. Daily and per-strategy caps enforced before next run. | MEDIUM (needs Claude SDK usage hook — verify SDK exposes token counts in result) |
| **Trade performance** | Daily snapshot of positions + P&L per user per strategy → time-series for dashboard | HIGH |
| **Broker health** | Per-broker rolling success rate + latency histogram. If success < 90% over 1h → circuit-breaker open, pause that broker, notify | HIGH |
| **Browser-broker screenshots** | Every order on a `BrowserBroker` captures before/after screenshots; stored on disk, path in audit log | HIGH |

### Correlation ID flow

Every strategy run starts with a fresh UUID `run_id`. It propagates as:
- `correlation_id` on every `audit_log` row
- `client_order_id = f"{run_id}-{proposal_seq}"` on every broker order
- A field on the Slack proposal message metadata so the approval webhook can re-join the trail

This means a single grep recovers the full story of any trade end-to-end.

### What NOT to instrument (yet)

- Distributed tracing (OpenTelemetry, Jaeger). Single process, single machine. Overkill.
- Prometheus metrics + Grafana. Personal scale; structured logs + a basic SQL query on `audit_log` suffices.

---

## Failure Modes & Recovery

This is **real money**. Each failure mode must have a designed-in response, not "we'll handle it when it happens."

| Failure | Detection | Response | Recovery |
|---------|-----------|----------|----------|
| **Claude API down** | `anthropic.APIError`, 5xx, timeout > 60s | Circuit-break Claude for 5 min; defer scheduled runs; if a run is in-flight when this hits, abort cleanly (no partial proposal) | Resume on next scheduled trigger; if outage > 1h, Slack alert to Chris |
| **Broker API down (Alpaca/IBKR/Schwab)** | `BrokerConnectivityError`, repeated 5xx | Retry with exponential backoff (3x, 1s → 8s); if still failing, raise to Executor → mark proposal `STALLED`, notify user | When broker recovers, Executor cron re-tries all `STALLED` proposals within their TTL |
| **Browser-broker session expired** | `BrokerSessionExpired` raised by `ensure_session()` | Pause that user's strategies on that broker; Slack DM with re-auth link; audit | User re-logs in via local web UI; re-auth flow refreshes the Chrome profile cookies |
| **Market closed / holiday** | Before each run, `MarketCalendar.is_open(now)` check (alpaca-py has this) | If market closed and strategy is not "after-hours-okay" → skip run, log, no LLM cost | Next scheduled run on next market day |
| **Network drops mid-trade** | Executor's `place_order` hangs / TCP reset | Per-call timeout (15s); on timeout, **do not retry blind** — call `Broker.list_orders(since=run_start)` to find the order by `client_order_id`. If present → record fill. If absent → safe to retry | The idempotency key makes retry safe regardless |
| **Machine reboot** | On startup, scan for `IN_FLIGHT` proposals | Each proposal status has a recovery handler: `PENDING` → check expiry, re-arm timeout; `APPROVED` (no broker_order_id) → query broker by `client_order_id`; `APPROVED` (broker_order_id present) → query order status, sync | Worst case: a few minutes of catch-up at boot |
| **Hard cap violation detected after approval but before execution** | Executor's pre-flight cap re-check (positions/cash may have moved) | Reject the proposal with reason; notify user; audit | Strategy will reconsider on next run |
| **LLM hallucination (proposes non-existent ticker)** | Proposal Writer validates symbol via broker `get_quote` before persisting | Reject upstream — never reaches user as a proposal; log to audit; metric "rejected_proposals_invalid_symbol" | Researcher gets feedback for the next run via memory |
| **Cost budget exceeded** | Cost ledger gate before each `query()` | Skip run; Slack DM "daily budget reached, $X spent, paused until reset" | Resets at midnight ET per user |
| **Crash during research (mid-`query()`)** | Asyncio exception handler at strategy-runtime level | Mark run as `CRASHED`, no proposal written; full traceback to audit + Sentry | No state corruption; next scheduled run starts fresh |
| **Two trade proposals for same symbol within same run** | Trader subagent prompt forbids; Proposal Writer dedupes by (run_id, symbol) | Keep first, log second as duplicate-suppressed | None needed |

### Graceful degradation ladder

When systems are stressed:
1. Cost approaching cap → reduce research depth (skip web research tool)
2. Cost at cap → skip the next scheduled run
3. Broker degraded → continue research but mark proposals as "executable when broker recovers"
4. Claude degraded → pause all runs but keep the dashboard / Slack approval pipeline live so in-flight proposals can still complete

---

## Build Order — Thin Vertical Slice First

The **most dangerous mistake** in this domain is to build the components horizontally — finish "the broker layer" before any agent exists, or vice versa. You won't know if it works until everything is wired, by which point you've designed yourself into a corner.

**Build a working end-to-end loop for ONE narrow case, then expand.**

### Phase 1: The Thinnest Possible Slice (paper trading, one user, no autonomy)

**Goal:** A single user can set up Alpaca paper-trading credentials, define a trivial strategy in plain English, get a trade proposal in Slack, click Approve, see a paper trade execute, and get a confirmation.

Components needed:
- `Broker` ABC + `AlpacaBroker` adapter (paper endpoint only)
- `CredentialVault` (SQLCipher + libsodium) — single-user mode okay
- Minimal `Researcher` subagent (just `get_quote` + maybe Finnhub news for one symbol)
- Minimal `Trader` subagent (proposes one trade based on hard-coded "if research says bullish, buy 1 share")
- Proposal Writer + `trade_proposals` table
- Slack Bolt webhook receiver + Approve/Reject buttons
- Approval Receiver → Executor → `AlpacaBroker.place_order`
- Audit log table + structlog
- Bare-bones APScheduler with a single manual-trigger run
- No web dashboard, no email, no autonomy, no multi-user, no IBKR, no Schwab, no browser brokers, no event triggers

**Definition of done:** Chris can run the binary, configure one Alpaca paper account, type a strategy in the chat, manually trigger a run, get a Slack proposal, approve, and see a paper fill in the audit log.

This proves the agent loop, the broker abstraction, the HITL pattern, and the audit chain in one slice.

### Phase 2: Trust Infrastructure

- Hard caps enforcement (max position, max daily loss, max trades/day)
- Cost ledger + daily budget gate
- Robust error handling and recovery for all failure modes above
- Audit log query UI (CLI is fine)
- Real-money Alpaca (live endpoint, **still HITL**)
- Schwab adapter (because OAuth is the next-hardest thing and best to get out of the way)

### Phase 3: Multi-User

- User table, per-user credential isolation
- Multi-tenant scheduler
- Slack user-routing
- Optional: simple web dashboard for cross-user health view (Chris is admin)

### Phase 4: Web Dashboard

- Strategy editor (form-based tuning)
- Portfolio view, trade history, audit browser
- Approval UI as fallback to Slack
- Strategy guidance ("look at energy this week")

### Phase 5: Autonomy (Trust Ladder)

- Per-strategy promotion (HITL → auto-execute-within-caps)
- Auto-approval path with full audit
- Revocation UI
- Anomaly detection ("this auto-trade is 3σ outside your normal pattern — pausing for confirmation")

### Phase 6: IBKR + Browser-Driven Brokers

- IBKR adapter (TCP socket to IB Gateway; expect pain on Mac/Windows parity)
- `BrowserBroker` base class
- Robinhood adapter (re-evaluate vs official Agentic Trading API first)
- Fidelity adapter (definitely browser-driven; expect maintenance burden)
- Per-broker circuit breakers
- Screenshot storage and audit

### Phase 7: Event Triggers + Advanced Research

- News webhook ingestion
- Price-move alert ingestion
- Earnings calendar triggers
- Premium data sources (only if free tier insufficient)

### Phase 8: Polish

- Email digests
- Mobile-friendly dashboard
- Backtesting harness (re-run a strategy against historical data — surprisingly hard, defer aggressively)

---

## Recommended Project Structure

```
gekko/
├── pyproject.toml
├── gekko/
│   ├── __init__.py
│   ├── __main__.py                 # entry point: starts FastAPI + APScheduler
│   ├── config.py                   # pydantic settings, env vars
│   │
│   ├── core/                       # domain types — no I/O, no framework deps
│   │   ├── types.py                # OrderRequest, Position, Quote, TradeProposal
│   │   ├── errors.py               # BrokerError hierarchy etc.
│   │   └── user_context.py
│   │
│   ├── brokers/                    # broker abstraction + adapters
│   │   ├── base.py                 # Broker ABC, BrowserBroker ABC
│   │   ├── alpaca.py
│   │   ├── ibkr.py
│   │   ├── schwab.py
│   │   ├── robinhood_browser.py
│   │   ├── fidelity_browser.py
│   │   └── registry.py             # broker_for(user, broker_name) factory
│   │
│   ├── agent/                      # Claude Agent SDK orchestration
│   │   ├── runtime.py              # run_strategy(user_id, strategy_id)
│   │   ├── researcher.py           # subagent definition + prompt
│   │   ├── trader.py               # subagent definition + prompt
│   │   ├── proposal_writer.py      # deterministic validation
│   │   └── tools/                  # in-process MCP tools
│   │       ├── market_data.py
│   │       ├── news.py
│   │       ├── fundamentals.py
│   │       ├── web_research.py
│   │       └── portfolio.py
│   │
│   ├── execution/                  # post-approval execution path
│   │   ├── executor.py             # NO LLM here
│   │   ├── risk.py                 # hard-cap enforcement
│   │   └── recovery.py             # boot-time reconciliation
│   │
│   ├── approval/                   # HITL
│   │   ├── proposals.py            # state machine
│   │   ├── slack_handler.py        # Bolt routes
│   │   ├── web_handler.py          # FastAPI routes
│   │   └── timeout.py              # expire_proposal job
│   │
│   ├── reporter/
│   │   ├── slack.py
│   │   ├── email.py
│   │   └── templates/
│   │
│   ├── scheduler/
│   │   ├── jobs.py                 # APScheduler job factory
│   │   └── triggers.py             # cron + event-driven
│   │
│   ├── vault/                      # credential management
│   │   ├── master_key.py           # passphrase-based key derivation
│   │   ├── credentials.py          # encrypt/decrypt + per-user-key
│   │   └── redaction.py            # logger filter
│   │
│   ├── db/
│   │   ├── engine.py               # SQLCipher + SQLAlchemy async
│   │   ├── models.py               # tables
│   │   └── migrations/             # alembic
│   │
│   ├── audit/
│   │   ├── logger.py               # write to audit_log
│   │   └── reader.py               # query helpers for CLI/dashboard
│   │
│   ├── dashboard/                  # FastAPI + HTMX (Phase 4+)
│   │   ├── app.py
│   │   ├── routes/
│   │   └── templates/
│   │
│   └── cli/
│       └── admin.py                # manual ops: add user, trigger run, query audit
│
└── tests/
    ├── unit/
    ├── integration/                # uses Alpaca paper endpoint
    └── e2e/                        # full slice tests
```

### Why this structure

- **`core/` has no dependencies on other modules** — pure domain types. This is the dependency root.
- **`brokers/` only depends on `core/`** — adapters are leaves of the dependency graph; you can build and test them in isolation.
- **`agent/`, `execution/`, `approval/` are siblings, all depending on `core/` and using `brokers/`** — no circular dependencies.
- **`vault/` is dependency-free** (other than libsodium); credentials are decrypted at the edge and passed via `UserContext` as plain bytes/strings to the broker adapters.
- **`audit/` is called by everything but depends on nothing** — pure write-only sink.

---

## Architectural Patterns

### Pattern 1: Dependency injection via `UserContext`

**What:** Every cross-module function takes a `UserContext` as its first non-self argument. The context bundles the user-scoped DB session, broker, SDK session ID, and audit logger.

**When to use:** Every code path that does anything on behalf of a user.

**Trade-offs:** Slight verbosity (one extra parameter everywhere) vs. eliminating an entire class of multi-tenancy bugs (using user A's broker for user B's order).

**Example:**
```python
async def execute_proposal(ctx: UserContext, proposal_id: str) -> OrderResult:
    proposal = await ctx.db.get_proposal(proposal_id)
    assert proposal.user_id == ctx.user_id, "tenant leak"  # belt and braces
    await ctx.audit.log("execute_start", proposal_id=proposal_id)
    result = await ctx.broker.place_order(proposal.to_order_request())
    await ctx.audit.log("execute_complete", broker_order_id=result.broker_order_id)
    return result
```

### Pattern 2: Subagent specialization with shared MCP tools

**What:** Researcher and Trader are separate Claude Agent SDK subagents — each with its own context window, system prompt, and tool allow-list. They communicate by passing structured artifacts (research brief, proposal JSON) through Python, not by sharing context.

**When to use:** Whenever two reasoning steps have different "what should I be thinking about" framings. Researcher is open-ended exploration; Trader is constrained decision-making.

**Trade-offs:** Slightly more orchestration code vs. context pollution from a single mega-prompt. Subagents also let you use different models (Researcher on Sonnet, Trader on Opus, say) when cost-vs-quality tradeoffs matter.

**Example:**
```python
research_brief = await sdk.query(
    agent="researcher",
    prompt=build_research_prompt(strategy, guidance, positions),
    tools=["market_data", "news", "fundamentals", "web_research"],
    session_id=ctx.sdk_session_id,
)
proposals = await sdk.query(
    agent="trader",
    prompt=build_trader_prompt(strategy, research_brief, positions, caps),
    tools=["get_quote", "get_position_value"],  # narrower tool surface
    session_id=ctx.sdk_session_id,
)
```

### Pattern 3: State machine for trade proposals

**What:** Every proposal has an explicit status: `PENDING → APPROVED → EXECUTING → FILLED` or one of `REJECTED`, `EXPIRED`, `STALLED`, `CRASHED`. Transitions are atomic DB updates; the system can be killed at any state and resume.

**When to use:** Any long-running, multi-step workflow that crosses process boundaries (user interaction, broker call). This is the standard pattern for durable workflows.

**Trade-offs:** More code than a single `async def execute_workflow()`. But the entire system survives reboots and gives you free observability.

### Pattern 4: Deterministic Executor (no LLM in the trade-execution path)

**What:** Once a proposal is approved, the code that talks to the broker is plain imperative Python. No "let Claude decide how to retry" or "let Claude check the cap." The LLM is in the **proposing** path, not the **executing** path.

**When to use:** Any path with irreversible real-money side effects.

**Trade-offs:** None worth caring about. Determinism in the execution path is non-negotiable.

### Pattern 5: Read-side and write-side separation on brokers

**What:** Both Researcher and Executor use `Broker`, but Researcher only ever calls read methods (`get_quote`, `get_positions`). Enforce this with **tool allow-lists at the MCP layer** — the Researcher subagent literally doesn't have `place_order` in its toolbox.

**When to use:** Any time an LLM has access to capabilities that include irreversible actions.

**Trade-offs:** None. This is a safety property, not a design preference.

---

## Anti-Patterns

### Anti-Pattern 1: "Let the LLM call the broker directly"

**What people do:** Expose `place_order` as a tool to the Researcher/Trader subagent. Let Claude decide when to trade.

**Why it's wrong:** Removes the human gate. Removes the deterministic cap check. Removes idempotency control. One prompt injection from a news article and your account is empty.

**Do this instead:** The agent **proposes**, the human (or a deterministic policy) **approves**, and the deterministic Executor **executes**. The broker write methods are not LLM-accessible.

### Anti-Pattern 2: Blocking `await` for human approval

**What people do:** `approval = await wait_for_approval(proposal_id)` — block the strategy run until the user clicks.

**Why it's wrong:** Burns Claude context, breaks on restart, breaks on HTTP timeout, dies if the user goes on vacation.

**Do this instead:** Persist the proposal, return from the strategy run, let a webhook resume the workflow via a fresh task.

### Anti-Pattern 3: Per-user processes / one Python process per user

**What people do:** Spawn a separate Python process per user "for isolation."

**Why it's wrong:** OS-level overhead for no real benefit at this scale. Loses shared connection pools to data providers, complicates the scheduler, makes the audit log harder.

**Do this instead:** One process, explicit `UserContext` everywhere, per-user-keyed DB rows. If a user ever needs OS isolation (running untrusted strategy code, say), revisit.

### Anti-Pattern 4: Using `float` for money

**What people do:** `position.market_value = 12345.67`

**Why it's wrong:** Floating-point binary representation cannot exactly represent `0.10`. Sum a thousand fills and you'll see `$0.00000001` errors that confuse reconciliation and look like bugs.

**Do this instead:** `Decimal` everywhere money is involved. The Broker interface mandates it. Database column is `NUMERIC`/`TEXT` with parsing on read.

### Anti-Pattern 5: Broker logic in the Strategy Runtime

**What people do:** Special-case "if broker == 'robinhood' then add a retry" inside the agent code.

**Why it's wrong:** Couples agent logic to broker quirks. Every new broker reopens the agent file.

**Do this instead:** All broker-specific quirks live in the broker adapter. The Strategy Runtime only sees the `Broker` ABC and typed errors.

### Anti-Pattern 6: Storing OAuth tokens unencrypted "because it's local"

**What people do:** "It's my Mac Mini, who's going to read it?"

**Why it's wrong:** Backup drives. Sync clients. Cloud-backed Time Machine. A stolen laptop. A malicious user share. Real-money credentials must be encrypted at rest, period.

**Do this instead:** SQLCipher master encryption + libsodium per-user encryption as described above.

### Anti-Pattern 7: Skipping the audit log because "it's slow"

**What people do:** Sample audit logs, or skip them in hot paths.

**Why it's wrong:** When something goes wrong with real money — and it will — the audit log is the only source of truth for what the agent thought and did.

**Do this instead:** Async writes to a `audit_log` table on every meaningful event. SQLite with WAL handles thousands of writes/sec; this is not the bottleneck.

---

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| **Alpaca (broker + market data)** | REST + WebSocket via `alpaca-py` | Paper and live use same SDK with different keys; free real-time data on paper |
| **IBKR** | TCP socket to IB Gateway (or TWS) via `ib_async` | Requires Gateway running locally; persistent connection; surprising amounts of state |
| **Schwab** | REST + OAuth via `schwab-py` | Token rotation is the operational burden; 7-day refresh-token limit |
| **Claude API** | Claude Agent SDK Python | One in-process MCP server hosts all tools; subagents are configured at query time |
| **Slack** | Bolt async + FastAPI adapter, signing-secret verification | Use HTTP mode (not Socket Mode) for production-like behavior; webhook endpoints on local FastAPI |
| **Finnhub / Alpha Vantage** | REST | Free tier rate-limit aware; cache responses |
| **SEC EDGAR** | REST (no auth) | Be polite (rate-limit, identify your user agent) |
| **Claude-for-Chrome** | Driven by the Claude SDK; persistent Chrome user-data-dir per user | The fragile path — wrap every call in try/except with screenshot capture |
| **Sentry** | `sentry-sdk` | Free tier is plenty; redact PII and secrets in `before_send` hook |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Scheduler ↔ Strategy Runtime | Function call (job target) | Job target is `run_strategy(user_id, strategy_id)` |
| Strategy Runtime ↔ Claude SDK | `query()` call with subagent config | Pass session ID for memory continuity |
| Researcher/Trader ↔ MCP tools | In-process MCP — same Python process | No serialization overhead; use `@tool` decorator |
| Trader → Proposal Writer | Python function call | Validated against pydantic schema |
| Proposal Writer → Reporter | Async function call | Reporter posts to Slack, returns |
| Reporter → Slack | HTTPS POST with signing | Slack returns 200 on accept; interactive responses come back on a different webhook |
| Slack webhook → Approval Receiver | HTTPS POST (FastAPI route) | Verify signature; ack within 3s; offload work to background task |
| Approval Receiver → Executor | Task queue (asyncio queue, in-process) | Executor consumes; deterministic |
| Executor → Broker | Async method call on `Broker` adapter | Adapter handles auth, retries, rate-limit |
| All components → Audit | Async write to `audit_log` table | Best-effort; never fails the caller; logged via structlog also |

---

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| **0-5 users (target)** | As designed: one process, SQLite, asyncio. Nothing to change. |
| **5-20 users** | Tune APScheduler max-workers; consider WAL checkpoint tuning on SQLite; possibly move browser-broker drivers to subprocesses (more memory pressure with multiple Chrome profiles) |
| **20+ users** | Move SQLite → Postgres (the schema is portable; `aiosqlite` ↔ `asyncpg` is a contained change). Consider splitting Slack/web frontend from agent backend. |
| **Multi-machine (not currently planned)** | Promote APScheduler to its Postgres-backed mode, separate scheduler/worker processes; move browser-driver workers to dedicated machines |

### First bottleneck

**Claude API rate limits and cost** are the first bottleneck — long before CPU/memory. Mitigations: per-user cost budgets, research-depth knobs, smarter prompt construction (cache market data so research prompts are smaller).

### Second bottleneck

**Browser-driver fragility.** A single Robinhood page-structure change breaks one or more adapters. Per-broker circuit breaker + screenshot logging + easy disable flag are essential before going broad on browser brokers.

---

## Sources

### Claude Agent SDK

- [Claude Agent SDK for Python (PyPI)](https://pypi.org/project/claude-agent-sdk/) — Verified
- [anthropics/claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python) — Verified
- [Subagents in the SDK — Claude API Docs](https://platform.claude.com/docs/en/agent-sdk/subagents) — Verified HIGH confidence
- [Give Claude custom tools — Claude Code Docs](https://code.claude.com/docs/en/agent-sdk/custom-tools) — Verified
- [MCP in the SDK — Claude Docs](https://docs.claude.com/en/docs/agent-sdk/mcp) — Verified
- [Session Management and Forking — DeepWiki](https://deepwiki.com/anthropics/claude-agent-sdk-python/6.1-session-management-and-forking) — MEDIUM confidence (3rd-party docs)
- [Multiagent sessions — Claude API Docs](https://platform.claude.com/docs/en/managed-agents/multi-agent) — HIGH confidence

### Trading agent architecture

- [TradingAgents: Multi-Agents LLM Financial Trading Framework (arXiv)](https://arxiv.org/html/2412.20138v1) — HIGH confidence (peer-reviewed-style preprint)
- [FinMem: Performance-Enhanced LLM Trading Agent (arXiv)](https://arxiv.org/pdf/2311.13743) — HIGH confidence
- [Toward Expert Investment Teams: Multi-Agent LLM System (arXiv)](https://arxiv.org/html/2602.23330v1) — HIGH confidence
- [TradeTrap: Are LLM-based Trading Agents Truly Reliable? (arXiv)](https://arxiv.org/pdf/2512.02261) — HIGH confidence

### Broker APIs

- [Alpaca-py SDK](https://alpaca.markets/sdks/python/) — Verified HIGH
- [alpacahq/alpaca-py](https://github.com/alpacahq/alpaca-py) — Verified HIGH
- [ib-api-reloaded/ib_async (replaces ib_insync)](https://github.com/ib-api-reloaded/ib_async) — Verified HIGH
- [schwab-py documentation — Authentication](https://schwab-py.readthedocs.io/en/latest/auth.html) — Verified HIGH (confirms 7-day refresh-token window)
- [Charles Schwab Developer Portal — OAuth](https://developer.schwab.com/user-guides/get-started/authenticate-with-oauth) — Authoritative
- [Robinhood Agentic Trading overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/) — Authoritative
- [trayders/trayd-mcp](https://github.com/trayders/trayd-mcp) — MEDIUM (community MCP server for Robinhood)

### HITL approval patterns

- [Implementing Human-in-the-Loop Approval Workflows for AI Agent SaaS Actions — Truto Blog](https://truto.one/blog/implementing-human-in-the-loop-approval-workflows-for-consequential-saas-api-actions/) — MEDIUM
- [Human-in-the-Loop AI Agent — Temporal docs](https://docs.temporal.io/ai-cookbook/human-in-the-loop-python) — HIGH (canonical pattern reference)
- [Slack Bolt Python — FastAPI async adapter](https://docs.slack.dev/tools/bolt-python/reference/adapter/fastapi/async_handler.html) — Verified HIGH
- [bolt-python/examples/fastapi/app.py](https://github.com/slackapi/bolt-python/blob/main/examples/fastapi/app.py) — Verified

### Encryption & credentials

- [SQLCipher — Zetetic](https://www.zetetic.net/sqlcipher/) — Authoritative
- [sqlcipher/sqlcipher (GitHub)](https://github.com/sqlcipher/sqlcipher) — Authoritative
- [Encrypted SQLite Databases with Python and SQLCipher — Charles Leifer](https://charlesleifer.com/blog/encrypted-sqlite-databases-with-python-and-sqlcipher/) — MEDIUM
- [Architecting Secure Multi-Tenant Data Isolation — Medium](https://medium.com/@justhamade/architecting-secure-multi-tenant-data-isolation-d8f36cb0d25e) — MEDIUM

### Scheduling & resilience

- [APScheduler User Guide](https://apscheduler.readthedocs.io/en/3.x/userguide.html) — HIGH
- [APScheduler — Flexible Task Scheduling](https://apscheduler.com/) — HIGH
- [Building Fault-Tolerant Python Services with Circuit Breakers — johal.in](https://www.johal.in/building-fault-tolerant-python-services-circuit-breaker-implementation-with-resilience-patterns-2/) — MEDIUM
- [Building Resilient Python Applications with Tenacity — amitavroy.com](https://amitavroy.com/articles/building-resilient-python-applications-with-tenacity-smart-retries-for-a-fail-proof-architecture) — MEDIUM

### Audit logging

- [Event Sourcing & Audit Trail Design for Trading Systems — Yukti](https://durgaanalytics.com/event_sourcing_audit_trading) — MEDIUM
- [Is the audit log a proper architecture driver for Event Sourcing? — Event-Driven.io](https://event-driven.io/en/audit_log_event_sourcing/) — MEDIUM
- [Event Sourcing Pattern — Microsoft Azure Architecture Center](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing) — HIGH

### Build order / vertical slice

- [Vertical Slice Architecture — Wikipedia](https://en.wikipedia.org/wiki/Vertical_slice) — Reference
- [What is a Vertical Slice? — monday.com](https://monday.com/blog/rnd/vertical-slice/) — MEDIUM
- [Crypto Trading Bot: Architecture and Roadmap — Vitalii Honchar](https://vitalii-honchar.medium.com/crypto-trading-bot-architecture-and-roadmap-f3e26cf9956a) — MEDIUM

---

## Open Questions / Discussion Points for Chris

1. **Master-key UX:** Passphrase on service start (interactive) vs `GEKKO_DB_KEY` env var (auto-start convenience but risk of leakage). My recommendation is **passphrase-on-start for v1**, env-var as a documented but discouraged option. Worth your call.
2. **Claude SDK session-per-strategy vs session-per-run:** SDK sessions can be resumed. Should a strategy's "memory" persist across runs (learning from prior research) or should each run start fresh? Tradeoff is context-drift over time vs. forgetting useful prior reasoning. My instinct: **fresh research session per run, persistent guidance/notes table that gets injected into each prompt.**
3. **Robinhood approach:** Browser-driven adapter vs Robinhood's official Agentic Trading product vs `trayd-mcp`. The official product changes the whole calculus — if Robinhood now offers a sanctioned API path, the browser fallback is wasted effort for that broker specifically. Needs validation in Phase 1 before Phase 6 begins.
4. **Dashboard tech:** FastAPI + HTMX (boring, single-process, minimal JS) vs Next.js (richer UX, separate process). Strong default: **HTMX**. But if you want a "real" web app later, the separation matters.
5. **Paper-trading-only constraint for early phases:** Should there be a hard machine-level switch ("this Gekko install can only do paper trading") for development boxes? Reduces risk of accidentally sending live orders during dev. Easy to add; worth doing.

---

*Architecture research for: Autonomous LLM-powered trading agent (Project Gekko)*
*Researched: 2026-06-08*
