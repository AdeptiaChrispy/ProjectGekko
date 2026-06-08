# Walking Skeleton — Project Gekko

**Phase:** 1
**Generated:** 2026-06-08

## Capability Proven End-to-End

A user can install Gekko on their machine, enter their SQLCipher passphrase, run `gekko init` to create a paper-mode strategy named "ai-infra-bull", trigger it via `/gekko run ai-infra-bull` in Slack, watch the Researcher subagent fetch one piece of evidence (an Alpaca quote + one Finnhub news headline), see the Decision subagent emit a `propose_trade` tool call, receive a Slack Block Kit card with Approve / Reject buttons, click Approve, see an Alpaca paper limit order placed with a deterministic `client_order_id`, receive the fill confirmation via the `TradingStream` websocket, and read the full event chain (`decision → proposal → approval → order_submitted → fill`) in the SHA-256-chained `events` table.

## Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language / runtime | Python 3.12 | D-18 locked; entire broker + agent ecosystem is Python-native |
| Orchestration | Claude Agent SDK 0.2.93+ with Researcher + Decision subagents from day one | D-10; one-shot architectural decision (cannot be retrofitted later) |
| Data layer | SQLCipher whole-DB encryption (passphrase-on-start) via `sqlcipher3-wheels` + SQLAlchemy 2.x | D-19; cross-platform parity for Mac + Windows |
| Schema migrations | Alembic | Standard; planner confirms |
| Web stack | FastAPI + HTMX + Tailwind + Jinja2 | D-23; minimal JS payload for "me + a few people" scale |
| Slack | `slack-bolt` async, HTTP mode via FastAPI adapter (`AsyncSlackRequestHandler`) | D-23; shares ASGI app with dashboard |
| Broker | `alpaca-py` paper only; live key rejected in `AlpacaBroker` constructor | D-24, REG-03 |
| Scheduler | APScheduler 3.x AsyncIOScheduler + SQLAlchemyJobStore on the app SQLite DB | D-22, CADENCE-02 |
| Logging | structlog JSON with credential-redaction processor | D-25, AUTH-04 |
| Audit log | Single `events` table; SHA-256 hash chain in app code; canonical subset `{event_type, payload, ts, user_id}` | D-14, D-15, D-16 |
| Money math | `Decimal` everywhere; `float` banned in `gekko/brokers/`, `gekko/execution/`, `gekko/core/money.py` by ruff rule | D-20, EXEC-01 |
| Deterministic broker idempotency | `client_order_id = sha256(f"{strategy_id}\|{decision_id}\|{side}\|{qty}\|{ticker}")[:32]` | D-20, EXEC-02 |
| CLI | `typer` — `gekko init`, `gekko serve`, `gekko run <strategy>` | Claude's Discretion; standard 2026 CLI tooling |
| Project mgmt | `uv` with `pyproject.toml` src-layout | STACK.md; standard 2026 Python tooling |
| Directory layout | `src/gekko/` with feature submodules (`core/`, `schemas/`, `db/`, `brokers/`, `agent/`, `audit/`, `execution/`, `approval/`, `reporter/`, `scheduler/`, `slack/`, `dashboard/`) | Research output |
| Deployment target | Local install on Mac Mini or Windows machine (D-21) | No cloud for v1 |

## Stack Touched in Phase 1

- [x] Project scaffold (`pyproject.toml` with all P1 deps; `uv`; `ruff` + `mypy` + `pytest` configs; src-layout)
- [x] CLI routing — `gekko init`, `gekko serve`, `gekko run <strategy>` (real)
- [x] Web routing — FastAPI app with `/`, `/strategies`, `/strategies/{name}/edit`, `/trigger/{name}`, `/slack/events` (minimal)
- [x] Database — SQLCipher-encrypted SQLite with Alembic schema for `users`, `strategies`, `guidance`, `proposals`, `events`, plus APScheduler's `apscheduler_jobs` table; real read AND real write (strategy create, event append, proposal insert/update, fill update)
- [x] UI — minimal HTMX-driven strategy list + edit form + "Trigger Run" button (interactive element wired to FastAPI)
- [x] Agent — Claude Agent SDK Researcher + Decision subagents with structured `ResearchBrief` and `propose_trade` / `propose_no_action` tools (real Claude calls)
- [x] Broker — `alpaca-py` paper round-trip (`get_account`, `place_order` with deterministic `client_order_id`, `get_order_by_client_order_id`, `TradingStream` websocket fills) — REAL
- [x] Slack — `slack-bolt` async with `/gekko run X` slash command, Block Kit proposal card, Approve/Reject action handlers — REAL Slack DM
- [x] Audit — `append_event()` with SHA-256 hash chain; `walk_chain()` verification function
- [x] Scheduler — APScheduler with SQLite job store; daily-time per-strategy job that calls `trigger_strategy_run`
- [x] Logging — structlog JSON with credential redaction processor wired into FastAPI + slack-bolt + asyncio
- [x] Deployment — `gekko serve` runs locally with documented `cloudflared tunnel run gekko-dev` for Slack webhooks; `pyproject.toml` declares `[project.scripts] gekko = "gekko.cli:app"`

## Absolute Minimum File Set (~24 files, sized for 2-3 days of execution)

1. `pyproject.toml`
2. `alembic.ini` + `migrations/env.py` + `migrations/versions/0001_initial.py`
3. `src/gekko/__init__.py`
4. `src/gekko/__main__.py`
5. `src/gekko/cli.py`
6. `src/gekko/config.py`
7. `src/gekko/logging_config.py`
8. `src/gekko/core/types.py`, `core/money.py`, `core/errors.py`, `core/ids.py`
9. `src/gekko/schemas/strategy.py`, `schemas/research.py`, `schemas/proposal.py`, `schemas/event.py`
10. `src/gekko/db/engine.py`, `db/models.py`, `db/session.py`
11. `src/gekko/audit/log.py`, `audit/canonical.py`, `audit/verify.py`
12. `src/gekko/brokers/base.py`, `brokers/alpaca.py`, `brokers/stream.py`
13. `src/gekko/execution/executor.py`, `execution/market_hours.py`
14. `src/gekko/agent/runtime.py`, `agent/researcher.py`, `agent/decision.py`, `agent/budget.py`, `agent/proposal_writer.py`
15. `src/gekko/agent/tools/alpaca_data.py`, `tools/finnhub_news.py`, `tools/edgar.py`, `tools/web_fetch.py`, `tools/propose_trade.py`, `tools/propose_no_action.py`
16. `src/gekko/approval/slack_handler.py`, `approval/proposals.py`
17. `src/gekko/reporter/slack.py`
18. `src/gekko/scheduler/jobs.py`
19. `src/gekko/slack/app.py`, `slack/commands.py`
20. `src/gekko/dashboard/app.py`, `dashboard/routes.py`
21. `src/gekko/dashboard/templates/base.html.j2`, `templates/strategies_list.html.j2`, `templates/strategy_edit.html.j2`
22. `tests/conftest.py`
23. Key unit tests: `tests/unit/test_audit_chain.py`, `test_client_order_id.py`, `test_money_math.py`, `test_strategy_versioning.py`
24. Key integration tests: `tests/integration/test_trigger_run_end_to_end.py`, `test_alpaca_paper_round_trip.py`

## First-to-Nth Task Ordering (the walking-skeleton spine)

The planner should structure Phase 1 as **3 waves** of ~7 tasks each. The order is chosen to minimize "I built X but I can't test it because Y doesn't exist yet" frustration.

### Wave 0 — Environment & Scaffolding (smoke-runnable end of wave)

1. Verify Python 3.12, `uv`, Alpaca paper account, Slack app, Anthropic API key are all available — env audit
2. `pyproject.toml` with all P1 deps; `ruff` + `mypy` + `pytest` configs
3. `src/gekko/__init__.py` + `__main__.py` + `cli.py` stub (just so `gekko --help` works)
4. `src/gekko/config.py` — Pydantic Settings reads `GEKKO_*` env vars
5. `src/gekko/logging_config.py` — structlog with credential redaction
6. `tests/conftest.py` with shared fixtures
7. `pytest --collect-only` runs cleanly — wave gate

### Wave 1 — Data Layer + Audit Chain + Broker ABC + Alpaca paper round-trip (smoke-runnable end of wave)

1. `src/gekko/db/engine.py` — SQLAlchemy + SQLCipher PRAGMA event handler + wrong-passphrase detection
2. `src/gekko/db/models.py` — `users`, `strategies`, `guidance`, `proposals`, `events` tables
3. `alembic.ini` + initial migration
4. `src/gekko/audit/canonical.py` + `audit/log.py` + `audit/verify.py` — append_event() + walk_chain()
5. `src/gekko/core/ids.py` — deterministic client_order_id
6. `src/gekko/brokers/base.py` — `Brokerage` ABC (load-bearing interface; comments must reference future P2/P8/P9 extensions)
7. `src/gekko/brokers/alpaca.py` — AlpacaBroker (paper-only; constructor asserts paper)
8. `src/gekko/brokers/stream.py` — TradingStream fill listener
9. Integration test: place limit order against Alpaca paper, observe fill via websocket, verify event chain in DB — wave gate

### Wave 2 — Slack + Agent + Walking Skeleton end-to-end

1. `src/gekko/schemas/strategy.py` + `schemas/research.py` + `schemas/proposal.py` — all Pydantic contracts
2. `src/gekko/agent/budget.py` — per-cycle BudgetTracker
3. `src/gekko/agent/tools/*` — research tools (alpaca_data, finnhub_news, edgar, web_fetch with allowlist) + decision tools (propose_trade, propose_no_action)
4. `src/gekko/agent/researcher.py` + `agent/decision.py` — AgentDefinition system prompts
5. `src/gekko/agent/proposal_writer.py` — deterministic validation + DB write
6. `src/gekko/agent/runtime.py` — `trigger_strategy_run(user_id, strategy_name)` orchestrator (the centerpiece)
7. `src/gekko/reporter/slack.py` — Block Kit card builder
8. `src/gekko/slack/app.py` + `slack/commands.py` — slack-bolt AsyncApp + slash command
9. `src/gekko/approval/slack_handler.py` — Approve/Reject handlers; calls Executor
10. `src/gekko/execution/executor.py` + `execution/market_hours.py` — deterministic paper-order placement; market-hours guard
11. `src/gekko/scheduler/jobs.py` — APScheduler daily fire
12. `src/gekko/dashboard/app.py` + `dashboard/routes.py` + minimal templates
13. `src/gekko/cli.py` — `gekko init`, `gekko serve`, `gekko run <strategy>` real entry points
14. End-to-end smoke test: full walking-skeleton demo script — wave gate

## What's Real vs Minimal in the Skeleton

| Layer | Real | Minimal | Stubbed |
|---|---|---|---|
| Project mgmt | uv + pyproject.toml + ruff + mypy + pytest, all wired | — | — |
| CLI | `gekko init`, `gekko serve`, `gekko run <strategy>` work | No `gekko status`, `gekko kill`, etc. (P2/P3) | — |
| Database | Real SQLCipher whole-DB encryption with passphrase-on-start | Single user; one DB file at `~/.gekko/{user_id}.db` | Per-row Fernet layer deferred (whole-DB encryption is enough for P1) |
| Audit chain | Real SHA-256 hash chain; `walk_chain()` verifier | Single event_type discriminators for P1 events | No browsable audit UI (P6) |
| Broker | Real `alpaca-py` paper round-trip + websocket fills + deterministic client_order_id | Paper only; live constructor-rejected | Cancel/rate-limit hardening deferred (P2) |
| Agent (Researcher) | Real Claude Agent SDK call with structured `ResearchBrief` output | One ticker, ~3-5 evidence snippets; web_fetch uses httpx + allowlist (not browser-use) | Source-allowlist enforcement minimal; full prompt-injection defense P4 |
| Agent (Decision) | Real Claude Agent SDK subagent emitting `propose_trade` or `propose_no_action` tool call | Two-tool schema | Fresh-context sanity check deferred (P4); cost ceiling deferred (P4) |
| Slack | Real Slack workspace, real Block Kit card, real Approve/Reject buttons, real DM | One button-press path; duplicates may execute (P3 fixes) | Edit-size, escalate-to-dashboard, quiet hours all stubbed with "Coming in P3" tooltips |
| Scheduler | Real APScheduler with SQLite job store; daily fire works | One schedule_time per strategy | Event triggers, market-calendar-aware skip deferred (P7) |
| Dashboard | Real FastAPI + HTMX scaffold; strategy list + edit + trigger button | No charting, no portfolio view (P6) | Magic-link auth deferred (P6) |
| Logging | Real structlog JSON with credential redaction | — | Log rotation, Sentry deferred (P7) |
| Hard caps | Strategy stores `max_position_pct` etc., visible in UI | NOT enforced in P1 (lives in P2 OrderGuard) | OrderGuard layer is P2 |
| Multi-user | Data model is multi-user-ready (`user_id` everywhere) | Only one user installed | Multi-user UI is P6 |

## Demo Script — Proves End-to-End in ~5 minutes

```bash
# 1. Install
cd ~/code/project-gekko
uv sync
uv run alembic upgrade head  # creates encrypted DB at ~/.gekko/{user_id}.db

# 2. First-run init (creates user, prompts for passphrase, captures Slack/Alpaca/Anthropic credentials)
uv run gekko init
# Prompts:
#   - SQLCipher passphrase (set once)
#   - Anthropic API key (stored in OS env or encrypted in DB)
#   - Slack bot/signing/app tokens
#   - Alpaca paper API key + secret
#   - Slack user_id (for DMing you)
#   - Finnhub API key (optional)
# Acknowledge the one-page user agreement (REG-02)

# 3. Create a strategy via NL chat (one-shot CLI prompt for P1; full chat UI lives in P6)
uv run gekko strategy create \
  --name ai-infra-bull \
  --thesis "I'm bullish on AI infrastructure; favor large-cap names with strong cash flow." \
  --watchlist NVDA,AMD,AVGO \
  --max-position-pct 0.05 \
  --max-daily-loss-usd 200 \
  --max-trades-per-day 3 \
  --max-sector-exposure-pct 0.25

# 4. Start the service (in another terminal or as a background process)
uv run gekko serve
# Output: "Listening on http://127.0.0.1:8000  •  Slack webhook at /slack/events"
# (In dev: cloudflared tunnel run gekko-dev to expose /slack/events publicly for Slack)

# 5. Trigger from CLI (alternative paths: Slack /gekko run ai-infra-bull, or click Trigger in dashboard)
uv run gekko run ai-infra-bull
# Output: "Triggered ai-infra-bull (run_id=...) — watch Slack for the proposal."

# 6. Watch Slack DM arrive (the Block Kit proposal card with Approve/Reject buttons)
# Card shows: PAPER banner, ticker, side, qty, limit price, rationale, top 3-5 evidence snippets,
# alternatives considered, confidence

# 7. Click Approve in Slack
# Within seconds: Alpaca paper order placed, fill confirmation arrives via TradingStream websocket,
# Slack DM updates: "Paper order filled: BUY 5 NVDA @ $1,234.56 — strategy=ai-infra-bull"

# 8. Verify the audit chain
uv run gekko audit verify --user-id <your-uid>
# Output: "Chain intact across 5 events: decision → proposal → approval → order_submitted → fill"

uv run gekko audit dump --user-id <your-uid> --limit 5
# Output: 5 JSON event rows with prev_hash + row_hash linked
```

If every step above passes, the walking skeleton is alive — and every later phase has its foundation in place.

## Out of Scope (Deferred to Later Slices)

- **OrderGuard cap-enforcement layer** (Phase 2) — hard caps configurable in P1 strategy schema but NOT enforced at order time
- **Real-money Alpaca live trading** (Phase 2) — paper-only in P1; constructor rejects live keys
- **Production HITL UX hardening** — idempotent buttons, quiet hours, timeout=REJECT, edit-size button, escalate-to-dashboard, stale-proposal expiry (Phase 3)
- **Agent architecture hardening** — prompt-injection defense via source allowlist + sanitized external content, two-tier cost ceiling, fresh-context sanity check (Phase 4)
- **Trust ladder** — `propose-only` ↔ `auto-within-caps` flip, portfolio-level caps, capital scaling rung, anomaly demotion (Phase 5)
- **Magic-link auth + full dashboard** — portfolio view, trade history, audit browser, ad-hoc guidance UI, CSV export (Phase 6)
- **Supervised service** — launchd / NSSM, heartbeat / dead-man-switch, NTP check, log rotation, market-calendar-aware skips (Phase 7)
- **Additional brokers** — IBKR + Schwab (Phase 8), Robinhood + Fidelity browser-fallback (Phase 9)
- **Deployment packaging** — one-command install on Mac + Windows, first-run wizard (Phase 9)
- **Full web research** — Claude-for-Chrome / browser-use integration (P1 uses minimal `httpx`-based allowlisted fetch)
- **Hard caps enforcement** — caps are stored on strategy but NOT enforced at order time in P1

## Subsequent Slice Plan

Each later phase adds one vertical slice on top of the skeleton without altering its core architectural decisions:

- **Phase 2:** OrderGuard layer wraps `AlpacaBroker.place_order` — adds idempotency double-check, universe-whitelist, hard-cap enforcement, qty×price sanity, paper-vs-live credential pairing, kill switch, market-hours / PDT / settlement awareness. Adds `BROK-A-02` live Alpaca with a separate-channel first-live confirmation. The walking skeleton's `Brokerage` ABC + deterministic `client_order_id` are unchanged.
- **Phase 3:** Production HITL UX — idempotent Slack buttons (Redis-free, app-level atomic check-and-set), quiet hours, timeout=REJECT, edit-size, escalate-to-dashboard, stale-proposal expiry. The walking skeleton's Slack action handlers are extended in place; the `proposals` table gains `expires_at` and `idempotency_key`.
- **Phase 4:** Agent architecture hardening — source-allowlist enforcement on `web_fetch`, untrusted-content sanitization on Researcher evidence, fresh-context sanity check before any `propose_trade` is persisted, two-tier cost ceiling (80%/100%) hooked into `BudgetTracker`. The walking skeleton's Researcher/Decision split is the same; only the tool implementations and budget layer are hardened.
- **Phase 5:** Trust ladder — `trust_level` field added to strategy snapshot; portfolio-level caps; auto-within-caps execution path (skips HITL but still audits); anomaly demotion daemon. The walking skeleton's `trigger_strategy_run` gains a branching after Decision: if `auto-within-caps` AND caps OK, fast-path to Executor.
- **Phase 6:** Magic-link auth via `fastapi-users` + full dashboard (portfolio, history, audit browser, ad-hoc guidance, paper/live banner, CSV export). The walking skeleton's minimal HTMX templates are the starting point; new pages added without restructuring.
- **Phase 7:** Supervisor (launchd / NSSM) + heartbeat + NTP + log rotation + market-calendar-aware scheduler skips. The walking skeleton's APScheduler stays; calendar awareness is added to the trigger function.
- **Phase 8:** IBKR + Schwab adapters — both implement `Brokerage` ABC. The walking skeleton's adapter pattern is the integration point; no changes to executor / orchestrator / audit.
- **Phase 9:** Browser-fallback brokers (Robinhood + Fidelity) — `BrowserBroker` subclass adds session management + screenshot evidence. One-command install + first-run wizard finalize the deployment story.

---

*Phase 1 walking skeleton for: Project Gekko, Foundation & Vertical Slice (Alpaca Paper + Slack HITL)*
*Generated: 2026-06-08*
