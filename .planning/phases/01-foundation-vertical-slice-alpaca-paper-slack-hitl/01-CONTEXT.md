# Phase 1: Foundation & Vertical Slice (Alpaca Paper + Slack HITL) - Context

**Gathered:** 2026-06-08
**Status:** Ready for planning

<domain>
## Phase Boundary

A working end-to-end loop on Alpaca paper: user authors a plain-English strategy, manually (or scheduled-daily) triggers a research run, the agent researches → proposes a trade → Slack Block Kit card → user approves → paper trade executes on Alpaca, every step recorded in an append-only audit log with row-hash chain. Multi-user-ready data model with `user_id` plumbing throughout, even though only one user is configured at runtime. SQLCipher whole-database encryption with passphrase-on-start.

**33 requirements in scope** (STRAT-01..06, RES-01..05, RES-08, EXEC-01, EXEC-02, EXEC-07, EXEC-10, HITL-01, HITL-04, BROK-A-01, BROK-A-03..06, AUTH-03, AUTH-04, AUDT-01, AUDT-02, REPT-04, REG-01..04, CADENCE-02). See `.planning/REQUIREMENTS.md` for full text.

**Out of scope for P1** (lives in later phases): OrderGuard cap-enforcement layer (P2), real-money trading (P2), production HITL UX hardening — idempotent buttons, quiet hours, timeout=REJECT, edit-size, dashboard approval fallback (P3), two-tier cost ceiling enforcement (P4), trust-ladder promotion (P5), full web dashboard (P6), supervisor + heartbeat (P7), other brokers (P8/P9), one-command installer + first-run wizard (P9).

</domain>

<decisions>
## Implementation Decisions

### Strategy Shape & Versioning

- **D-01: Minimal v1 strategy fields.** A strategy contains: `name`, plain-English `thesis`, `watchlist` (list of tickers), per-strategy hard caps (`max_position_pct`, `max_daily_loss_usd`, `max_trades_per_day`, `max_sector_exposure_pct`). Smallest shape that is both safe and useful. Exclude-lists and per-position risk parameters (stop-loss, take-profit, max-holding-period) are deferred to later phases — not blocked, just not P1.
- **D-02: Plain-English diff view for strategy edits.** When the user tweaks a strategy, the diff display is a human-readable summary ("You changed max-position from 5% to 7% and added healthcare to watchlist"). No JSON staring. Raw-diff view can be added later if needed.
- **D-03: Explicit save creates new versions.** Edits are draft until the user clicks "Save as new version." Prevents accidental versions and keeps history clean. No auto-save-draft / publish-button workflow.
- **D-04: NL chat supports both new and refine modes.** Chat can either create a new strategy from scratch OR refine the currently-selected strategy. User picks at the start of chat. Most flexible.
- **D-05: Strategy stored as a Pydantic model + persisted as a JSON column in a versioned `strategies` table.** Each save inserts a new row (snapshot rows, not delta log) keyed by `(user_id, strategy_name, version)`. Simpler queries, no replay needed to materialize state. Storage cost is trivial at the v1 scale.

### Trigger UX

- **D-06: All three trigger surfaces ship in P1 — Slack slash command, CLI, dashboard button.** Each surface is a thin wrapper around the same `trigger_strategy_run(user_id, strategy_name)` function — once that function exists, adding surfaces is cheap. Slack `/gekko run <strategy>` is the primary user-facing path; CLI `gekko run <strategy>` is the operator path; dashboard button is the discoverable path.
- **D-07: Name-based strategy selection.** Triggers always specify a strategy by name (`/gekko run ai-infra`). No "run all enabled" default — too easy to bulk-fire by accident. No interactive picker in P1 (Block Kit picker is a P3 polish item if needed).
- **D-08: Daily fixed-time schedule per strategy alongside manual triggers.** Each strategy has an optional `schedule_time` field (e.g., `10:00 America/New_York`); APScheduler with SQLite job store fires it daily. Proves APScheduler integration in P1 without P4's full cadence configurability (event triggers, continuous loops). Manual triggers always work in addition.
- **D-09: Verbose `no_action` reporting.** When the Decision agent proposes no trade, Slack still gets a brief rationale ("Reviewed ai-infra at 10am ET, no action — NVDA price too elevated vs thesis. Spent ~$0.12."). User always knows the agent ran, why nothing happened, and what it cost.

### Agent Architecture (research/decision split from day one)

- **D-10: Researcher and Decision agents split from day one** — using Claude Agent SDK subagents. Researcher subagent has read-only tools (market data, news, EDGAR, web research) and zero access to order placement or credentials. It produces a structured "research brief" Pydantic doc. The Decision subagent consumes only that brief (no shared raw context) and emits the trade proposal. Slightly more code in P1, prevents a painful refactor in Phase 4 — research strongly recommends this and the P4 work becomes hardening of an existing split rather than rewriting.
- **D-11: Decision agent emits structured tool calls; `no_action` is first-class.** Two tool-use schemas: `propose_trade(ticker, side, qty, rationale, confidence, evidence[], alternatives_considered[])` OR `propose_no_action(rationale, factors_considered[])`. Tool-use schema enforced — no JSON parsing failures. The Decision agent cannot return free-form text as its final output.
- **D-12: Rich structured evidence per proposal.** Each `propose_trade` call attaches the top 3-5 evidence snippets the Decision agent actually used (with source URLs), a confidence score (0-1), and the alternatives it considered and rejected. This is the structured-rationale differentiator that makes v2's reasoning-retrospective dashboard possible — and it's a one-shot architectural decision (cannot be retrofitted from free-form prose).
- **D-13: Per-cycle research budget is soft + 2x grace; per-day ceiling (P4) is the hard backstop.** Per-cycle: warning at 12 tool calls / 8K research tokens / 60s wall time; halt at 2x any limit. This is intentionally softer than the research recommendation — chosen because the *daily* cost ceiling in P4 is the catastrophic-loss prevention layer (hard halt at 100% of daily cap), and over-tight per-cycle caps can mis-fire on legitimate complex strategies. The two-layer design (soft per-cycle + hard per-day) is the safety net.

### Audit Log Schema

- **D-14: Single `events` table with `event_type` discriminator and JSON `payload` column.** Columns: `id, ts, user_id, strategy_id, event_type, payload_json, prev_hash, row_hash`. `event_type` covers `decision`, `proposal`, `approval`, `rejection`, `order_submitted`, `fill`, `kill_switch`, `cap_rejection`, `error`. Queryable via SQLite JSON paths. Simplest layout that supports the row-hash chain cleanly across all event types.
- **D-15: Full structured rationale embedded in the event payload.** When a `decision` or `proposal` event is logged, its `payload_json` includes the full evidence snippets, confidence, alternatives, prompt model, and research-brief reference. This is required for the v2 retrospective differentiator and cannot be retrofitted from free-form prose — invest now.
- **D-16: SHA-256 hash chain enforced in application code.** Each new event computes `row_hash = sha256(prev_hash || canonical_json(event_type, payload_json, ts, user_id))`. Application layer (not SQLite trigger) writes the hash so it remains portable and debuggable. A periodic "walk the chain" verification job confirms integrity. SHA-256 over canonical subset (not full row) so the chain survives schema-additive migrations.
- **D-17: Tax-export CSV uses brokerage-standard column set.** `date, time, ticker, action, qty, price, gross_amount, fees, account_id, strategy_name`. Imports cleanly into TurboTax, H&R Block, FreeTaxUSA, and most CPAs. No vendor-pinning. Rationale columns are NOT in the tax export (different audience) — they live in the audit log and in the v2 retrospective dashboard.

### Foundational Decisions (carried forward from PROJECT.md / SUMMARY.md, locked here for downstream agents)

- **D-18: Python 3.12, single-process modular monolith on Claude Agent SDK (v0.2.93+).** No microservices, no Celery/Redis, no event bus. Modules with clean Pydantic interfaces.
- **D-19: SQLite (WAL) + SQLCipher whole-database encryption + passphrase-on-start.** Master passphrase prompted on service start; no env-var fallback. Per-user-isolated database file (one SQLCipher DB per user, not shared multi-tenant). DuckDB (Phase 6+) for analytical reads.
- **D-20: `Decimal` for all money math at the order-placement layer; `float` is banned by lint rule.** Deterministic `client_order_id = sha256(f"{strategy_id}|{decision_id}|{side}|{qty}|{ticker}")[:32]` for broker idempotency.
- **D-21: Per-user isolated deployment.** Each user installs Gekko on their own machine — no shared multi-tenant runtime. `user_id` plumbed through every data row, every function signature, every log entry, for future-proofing and data portability.
- **D-22: APScheduler with SQLite job store; jobs survive process restarts.** No external scheduler dependency.
- **D-23: `slack-bolt` for Slack integration; FastAPI for the dashboard; HTMX + Tailwind + Jinja2 for the UI.**
- **D-24: `alpaca-py` (official SDK, current version pinned at install).** Paper credentials are the only credentials used in P1. Live keys are physically rejected by the orchestrator until P2.
- **D-25: `structlog` JSON logging; logs never contain credentials, raw broker responses, or Slack tokens (redacted at the structlog processor).**

### Claude's Discretion

The following implementation details are left to research/planning phases — Chris did not specify and downstream agents have flexibility:

- Exact directory layout / module boundaries (research will propose; planner will lock)
- Test framework choice (pytest is the obvious default; planner can confirm)
- Specific Pydantic model for the `ResearchBrief` shape passed Researcher → Decision (research will draft; the shape must be backward-compatible across P4 hardening)
- Slack Bolt FastAPI adapter vs. standalone Bolt app (research will pick based on whether the FastAPI dashboard and Slack share an event loop)
- Schema migration tool (`alembic` is default; planner can confirm)
- Lint/format tooling (`ruff` + `mypy` is the standard 2026 stack; planner can confirm)
- The exact `prev_hash` value used for the genesis (first) event — convention is all-zero SHA-256 ("0" * 64) but planner can lock

### Pushback Worth Noting

- **Per-cycle budget choice (D-13):** Research recommended hard caps on tool calls + tokens + time at the per-cycle layer. Chris chose soft + 2x grace. The defensibility is the two-layer model (soft per-cycle, hard daily); the risk is that a complex strategy can spend 2x its per-cycle budget before halting. Re-evaluate during Phase 4 (cost-bounds phase) — if per-day ceilings are routinely hit because per-cycle is too loose, tighten per-cycle to hard caps.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project context
- `.planning/PROJECT.md` — Project intent, requirements, key decisions, constraints
- `.planning/REQUIREMENTS.md` — 108 v1 requirements (33 mapped to Phase 1)
- `.planning/STATE.md` — Project state, carried-forward decisions, open questions
- `.planning/ROADMAP.md` — Full 9-phase roadmap, Phase 1 success criteria

### Research outputs (load all five — cross-cutting consensus + dimension-specific detail)
- `.planning/research/SUMMARY.md` — Consolidated findings, cross-cutting consensus, recommended phase order
- `.planning/research/STACK.md` — Library + version choices (Claude Agent SDK 0.2.93+, `alpaca-py`, `slack-bolt`, FastAPI, HTMX, SQLite/SQLCipher, APScheduler, `structlog`)
- `.planning/research/FEATURES.md` — Feature inventory, table-stakes safety controls, the rationale-as-structured-artifact differentiator
- `.planning/research/ARCHITECTURE.md` — Component decomposition, Broker abstraction interface spec, HITL pattern, credential vault design, build order
- `.planning/research/PITFALLS.md` — Catastrophic failure modes (Knight Capital duplicate orders, hallucinated tickers, off-by-magnitude sizing, prompt injection, multi-user credential leakage, paper-vs-live mix-up)

### External documentation (research-cited)
- Claude Agent SDK docs: https://docs.anthropic.com/en/api/agent-sdk — subagents, custom tools, persistent sessions, HITL checkpoints
- `alpaca-py` docs: https://alpaca.markets/sdks/python/ — paper/live keys, order placement, websocket fills, client_order_id
- `slack-bolt` Python docs: https://docs.slack.dev/tools/bolt-python/ — Block Kit, slash commands, interactivity, FastAPI adapter
- APScheduler docs: https://apscheduler.readthedocs.io/en/3.x/userguide.html — SQLite job store, cron triggers, restart survivability
- SQLCipher docs: https://www.zetetic.net/sqlcipher/ — whole-database encryption, passphrase-on-start, key derivation

### Not yet existing (will be created during planning/execution)
- `pyproject.toml` — Python project metadata, dependencies, tool configuration (Phase 1 deliverable)
- `Brokerage` ABC — `src/gekko/brokers/base.py` (Phase 1 deliverable, hardened in Phase 2)
- `ResearchBrief` Pydantic model — `src/gekko/agent/schemas.py` (Phase 1 deliverable)
- Audit log writer — `src/gekko/audit/log.py` (Phase 1 deliverable)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

None — Phase 1 is the foundation phase. No code exists yet. Greenfield.

### Established Patterns

None from the codebase (it's empty), but project-level patterns from PROJECT.md / SUMMARY.md / decisions above:

- `Decimal` for money math, never `float`
- Deterministic `client_order_id` for broker idempotency
- Per-user-tagged data rows (`user_id` everywhere)
- Append-only audit log with row-hash chain
- Pydantic for schemas (strategy, research brief, trade proposal)
- `structlog` JSON logging with credential redaction at the processor layer
- Claude Agent SDK subagents for research/decision split (P1 establishes the split; P4 hardens it)

### Integration Points

Phase 1 is the integration point — every later phase plugs into structures Phase 1 lays down:

- `Brokerage` ABC will be extended by IBKR, Schwab, Robinhood, Fidelity adapters in P8/P9
- Audit-log event types will grow (cap_rejection from OrderGuard in P2, dashboard_approval from P6, etc.)
- Strategy schema will gain fields (exclude list, per-position risk) in later phases — keep it forward-compatible
- The Researcher/Decision split scaffolded here gets prompt-injection defense, source allowlist, and two-tier cost ceiling layered on in P4

</code_context>

<specifics>
## Specific Ideas

- **OpenClaw.ai and NeMo-Claw were considered and rejected during research** — both verified as wrong shape / wrong ecosystem. Claude Agent SDK is the path. (See SUMMARY.md "Cross-Cutting Consensus".)
- **Robinhood now has an official Agentic Trading API** (2025 launch) — affects Phase 9 design. Phase 1 should remain Alpaca-only; Phase 9 must re-validate the Robinhood API before building a browser adapter.
- **Schwab refresh tokens expire at 7 days, not 90** — Phase 8 must build a proactive refresh coordinator. Phase 1 is unaffected (Alpaca only).
- **The structured-rationale differentiator** (full evidence + confidence + alternatives in the audit log payload) is a one-shot architectural decision. If Phase 1 stores free-form rationale text, the v2 retrospective dashboard cannot be retrofitted. **Phase 1 must capture structured rationale from the first event.**
- **Knight Capital ($440M in 45 minutes, 2012)** is the canonical failure mode the safety architecture defends against. The deterministic `client_order_id` + "never auto-retry a POST, always query existing order first" pattern lives in P1 (EXEC-02) so P2's OrderGuard can layer on it without retrofit.

</specifics>

<deferred>
## Deferred Ideas

Captured during Phase 1 discussion for later phases — do not lose them, do not act on them now.

- **Block Kit interactive picker for `/gekko run` with no name** — P3 polish item if name-only feels too rigid in practice. Currently rejected for P1 (D-07).
- **Strategy exclude list as a structured field** — deferred from P1 (D-01); user can embed "avoid X" in the plain-English thesis for now. Promote to its own field in a later phase if it proves load-bearing.
- **Per-position risk parameters (stop-loss %, take-profit %, max holding period)** — deferred from P1 (D-01); not blocked, just not v1. Likely lands in P5 (trust ladder) since position-level risk caps interact with per-strategy caps.
- **`silent_no_action` per-strategy field** — D-09 went with verbose-always for P1 simplicity. If verbose runs prove noisy in practice, add the configurable field in a polish phase.
- **Schema-additive migrations / `alembic` setup details** — Phase 1 deliverable but exact migration framework choice left to planner.
- **Raw-diff JSON view alongside plain-English diff** — D-02 went with plain-English only; raw-diff can be added later if needed.

</deferred>

---

*Phase: 1-Foundation & Vertical Slice (Alpaca Paper + Slack HITL)*
*Context gathered: 2026-06-08*
