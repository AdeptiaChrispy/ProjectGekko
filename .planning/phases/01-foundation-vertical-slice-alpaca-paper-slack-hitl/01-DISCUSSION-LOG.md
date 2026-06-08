# Phase 1: Foundation & Vertical Slice (Alpaca Paper + Slack HITL) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-08
**Phase:** 1-Foundation & Vertical Slice (Alpaca Paper + Slack HITL)
**Areas discussed:** Strategy shape & versioning, Trigger UX, Agent architecture, Audit log schema

---

## Gray Area Selection

| Option | Description | Selected |
|--------|-------------|----------|
| Strategy shape & versioning | How the structured strategy doc is represented, NL chat compilation, version history | ✓ |
| Trigger UX (Slack / CLI / dashboard) | Which surface(s) handle the "manual trigger" | ✓ |
| Agent architecture (1-agent vs 2-agent now) | Build Researcher + Decision separation in P1, or refactor in P4 | ✓ |
| Audit log record schema | Table layout, rationale capture, hash chain mechanics, tax-export columns | ✓ |

**User's choice:** All four areas — full deep-dive.

---

## Strategy Shape & Versioning

### Q1: Strategy fields v1

| Option | Description | Selected |
|--------|-------------|----------|
| Minimal v1 | name + thesis + watchlist + per-strategy hard caps (size, daily loss, trades/day, sector exposure) | ✓ |
| Minimal + explicit exclude list | Adds a separate 'avoid these tickers/sectors' field | |
| Minimal + per-position risk | Adds stop-loss %, take-profit %, max holding period per position | |

**User's choice:** Minimal v1 (Recommended).
**Notes:** Exclude list and per-position risk noted as deferred ideas — promote later if proven load-bearing.

### Q2: Diff view when tweaking a strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Plain-English summary | 'You changed max-position from 5% to 7% and added healthcare to watchlist' | ✓ |
| Side-by-side JSON | Full structured diff, precise but dense | |
| Both | Plain-English by default with 'show raw diff' expander | |

**User's choice:** Plain-English summary (Recommended).
**Notes:** Raw-diff view added to deferred ideas in case it's wanted later.

### Q3: Save model

| Option | Description | Selected |
|--------|-------------|----------|
| Explicit save | Edits are draft until user clicks 'Save as new version' | ✓ |
| Auto-save draft + manual publish | Edits auto-save as draft; publish creates the new version | |
| Every edit creates a new version | No drafts; every change becomes history | |

**User's choice:** Explicit save (Recommended).

### Q4: NL chat mode

| Option | Description | Selected |
|--------|-------------|----------|
| Both modes | Chat can create a new strategy OR refine an existing one; user picks | ✓ |
| New strategies only | Chat always produces a new strategy; existing edits via form | |
| Refine only | Chat always operates on the currently-selected strategy | |

**User's choice:** Both modes (Recommended).

---

## Trigger UX

### Q1: Primary trigger surface

| Option | Description | Selected |
|--------|-------------|----------|
| Slack slash command | /gekko run <strategy>. Recommended (Slack already the HITL surface) | |
| CLI command | gekko run <strategy> from terminal | |
| Dashboard button | Pick from dropdown, click 'run now' | |
| All three | Slack + CLI + dashboard from day one | ✓ |

**User's choice:** All three.
**Notes:** Diverged from the single-surface recommendation. Justified by D-06 — once the trigger function is factored, each surface is a thin wrapper, and all three serve distinct workflows (Slack=primary HITL surface, CLI=operator path, dashboard=discoverable path).

### Q2: Strategy selection on trigger

| Option | Description | Selected |
|--------|-------------|----------|
| Name-based | /gekko run ai-infra — specific strategy by name | ✓ |
| Run-all default | No name = run all enabled in parallel; name targets one | |
| Interactive picker | /gekko run with no name returns a Block Kit picker | |

**User's choice:** Name-based (Recommended).
**Notes:** Interactive picker noted as a P3 polish item if needed.

### Q3: Schedule support in P1

| Option | Description | Selected |
|--------|-------------|----------|
| One fixed time per day | e.g., 'run my-strategy at 10am ET daily'. Proves APScheduler integration | ✓ |
| Strict manual-only | APScheduler wired but not used until P4 | |

**User's choice:** One fixed time per day (Recommended).

### Q4: no_action verbosity

| Option | Description | Selected |
|--------|-------------|----------|
| Slack message + brief rationale | 'Reviewed at 10am ET, no action — too elevated vs thesis. ~$0.12.' | ✓ |
| Silent unless action | Only ping Slack on proposals | |
| Configurable per strategy | silent_no_action: bool field; default verbose | |

**User's choice:** Slack message + brief rationale (Recommended).
**Notes:** Per-strategy configurability added to deferred ideas — promote if verbose runs prove noisy.

---

## Agent Architecture

### Q1: Split or unified for P1

| Option | Description | Selected |
|--------|-------------|----------|
| Split from day one | Researcher (read-only) → research brief → Decision agent. Subagents in Agent SDK | ✓ |
| Single agent for P1 | One Claude session does research + decision; refactor in P4 | |
| Hybrid (one session, two prompts) | Same session, explicit research-then-decision prompts | |

**User's choice:** Split from day one (Recommended).
**Notes:** Avoids the painful retrofit research warned about. P4's work becomes hardening (prompt-injection defense, source allowlist, cost bounds) of an already-existing split.

### Q2: Decision agent output shape

| Option | Description | Selected |
|--------|-------------|----------|
| Structured tool call + no_action first-class | propose_trade(...) or propose_no_action(...) with tool-use schema enforcement | ✓ |
| Free-form + place_order tool call | Prose rationale, agent calls place_order at the end if deciding to trade | |
| Plan-then-execute split | Decision returns a plan; non-LLM step turns it into the order proposal | |

**User's choice:** Structured tool call + no_action first-class (Recommended).

### Q3: Supporting evidence per proposal

| Option | Description | Selected |
|--------|-------------|----------|
| Top 3-5 snippets + confidence + alternatives | Snippets w/ sources, confidence 0-1, alternatives considered/rejected | ✓ |
| Free-form prose rationale only | A paragraph of 'why' | |
| Confidence + sources-consulted only | Which sources used + confidence; no snippet-level evidence | |

**User's choice:** Top 3-5 snippets + confidence + alternatives (Recommended).
**Notes:** This is the v2-retrospective-differentiator architectural decision. Cannot be retrofitted from free-form prose — has to be structured from day one.

### Q4: Per-cycle research budget enforcement

| Option | Description | Selected |
|--------|-------------|----------|
| Hard cap on all three (calls, tokens, time) | 12 calls / 8K tokens / 60s — whichever hits first halts | |
| Hard cap on tool calls; tokens/time advisory | Strict on count (drives cost); tokens/time logged but not enforced | |
| Soft warning + 2x grace | Warn at limit; halt at 2x | ✓ |

**User's choice:** Soft warning + 2x grace.
**Notes:** Diverged from the Recommended "hard cap on all three." Defensibility documented in D-13: the *daily* cost ceiling (P4) is hard-halt at 100% — runaway scenarios are caught by the daily layer. Re-evaluate during Phase 4 if per-cycle soft routinely causes daily-cap hits.

---

## Audit Log Schema

### Q1: Table layout

| Option | Description | Selected |
|--------|-------------|----------|
| Single events table + JSON payload | events(id, ts, user_id, strategy_id, event_type, payload_json, prev_hash, row_hash) | ✓ |
| One table per event type | Separate decisions, proposals, approvals, orders, fills tables | |
| Hybrid (events table + typed views) | Single chain table + SQL views per type | |

**User's choice:** Single events table + JSON payload (Recommended).

### Q2: Reasoning trail capture

| Option | Description | Selected |
|--------|-------------|----------|
| Full structured rationale per decision | Evidence snippets w/ sources, confidence, alternatives, prompt/model in JSON | ✓ |
| Compact: decision + 200-char 'why' | Just a short prose 'why' | |
| Sidecar: rationale in separate decisions table, referenced by event ID | Audit log compact; rich rationale in a side table | |

**User's choice:** Full structured rationale per decision (Recommended).

### Q3: Hash chain mechanics

| Option | Description | Selected |
|--------|-------------|----------|
| SHA-256 of canonical subset, enforced in app code | sha256(prev_hash, event_type, payload_json, ts, user_id) | ✓ |
| SHA-256 of full row | Hash entire row including auto-fields | |
| SQLite trigger writes the hash | Database trigger computes hash on INSERT | |

**User's choice:** SHA-256 of canonical subset, app-code enforced (Recommended).

### Q4: Tax-export CSV format

| Option | Description | Selected |
|--------|-------------|----------|
| Brokerage-standard | date, time, ticker, action, qty, price, gross_amount, fees, account_id, strategy_name | ✓ |
| Schwab/TurboTax pinned format | Match a specific vendor exactly | |
| Verbose (add rationale & confidence columns) | Brokerage-standard + per-trade rationale + agent confidence | |

**User's choice:** Brokerage-standard (Recommended).

---

## Claude's Discretion

The user did not specify and downstream agents have flexibility on:

- Exact directory layout / module boundaries (research/planner)
- Test framework (pytest is obvious default)
- `ResearchBrief` Pydantic shape (research will draft; must remain backward-compatible across P4 hardening)
- Slack Bolt FastAPI adapter vs. standalone (research will pick)
- Migration tool (`alembic` is the default)
- Lint/format tooling (`ruff` + `mypy` is the 2026 standard)
- Genesis `prev_hash` value (convention: `"0" * 64`)

## Deferred Ideas

Captured for later phases:

- Block Kit interactive picker for `/gekko run` with no name — P3 polish if needed
- Strategy exclude list as a structured field — embed in thesis prose for now
- Per-position risk parameters (stop-loss %, take-profit %, max holding period) — likely P5
- `silent_no_action` per-strategy field — add if verbose runs prove noisy
- Raw-diff JSON view alongside plain-English diff
- Schema migration framework lock (planner decides)
