---
phase: 1
slug: foundation-vertical-slice-alpaca-paper-slack-hitl
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-06-08
updated: 2026-06-08
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution. Phase 1 builds a Walking Skeleton — the validation strategy must prove the end-to-end loop works while keeping per-task feedback latency low.

> **Updated 2026-06-08**: Per-Task Verification Map populated with the 9 plans + their task IDs created by `/gsd-plan-phase 1`.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | `pytest 8.x` (Python 3.12 compatible) with `pytest-asyncio`, `pytest-mock`, `respx` (async HTTP mocking), `freezegun` (time control) |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]`) — created by Plan 01-01 Task 2 |
| **Quick run command** | `uv run pytest tests/unit -q --no-header -x` |
| **Full suite command** | `uv run pytest tests/ -q --no-header` (unit + integration) |
| **Estimated runtime** | ~5-10s unit / ~60-90s full (integration includes real Alpaca paper round-trip via cassette OR live paper key) |

**Integration test mode:**
- Default: cassette-replay via `respx` / saved websocket frames — no network, fast, deterministic
- Opt-in live: env `GEKKO_TEST_LIVE_ALPACA=1` runs against real Alpaca paper account (used once per wave, not per commit)

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/unit -q --no-header -x` (must be < 10s)
- **After every plan wave:** Run `uv run pytest tests/ -q --no-header` (full suite, including integration cassettes)
- **Before `/gsd-verify-work`:** Full suite green + the walking-skeleton demo script (`01-SKELETON.md` §Demo Script) executes end-to-end with one real Alpaca paper round-trip
- **Max feedback latency:** 10s for unit, 90s for full suite

---

## Per-Task Verification Map

Each row maps a task to its automated verification command. Task IDs are `{plan_id}-T{task_number}` (e.g., `01-04-T3` = Plan 01-04, Task 3).

### Wave 0 — Scaffolding (Plans 01-01, 01-02)

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01-T1 | 01-01 | 0 | (package legitimacy gate) | T-01-SC | All [ASSUMED] packages human-verified before install | manual (blocking-human) | (checkpoint) | n/a | ⬜ pending |
| 01-01-T2 | 01-01 | 0 | (scaffold) | — | uv sync resolves; ruff/mypy/pytest configs present | unit | `uv sync && uv run ruff check . && uv run pytest --collect-only` | ❌ W0 | ⬜ pending |
| 01-01-T3 | 01-01 | 0 | (scaffold) | — | Every gekko.* package imports without error | unit | `uv run python -c "import gekko, gekko.core, gekko.schemas, gekko.db, gekko.brokers, gekko.audit, gekko.agent, gekko.agent.tools, gekko.execution, gekko.approval, gekko.reporter, gekko.scheduler, gekko.slack, gekko.dashboard, gekko.vault"` | ❌ W0 | ⬜ pending |
| 01-01-T4 | 01-01 | 0 | (env audit) | T-01-01 | `gekko doctor` reports PRESENT/MISSING; never echoes values; `gekko --help` shows all commands | unit | `uv run pytest tests/unit/test_cli.py tests/unit/test_smoke.py -q` | ❌ W0 | ⬜ pending |
| 01-02-T1 | 01-02 | 0 | AUTH-04 | T-01-02-01 | structlog credential-redaction processor scrubs Bearer/sk-/sk-ant-/PK.../xoxb-/xapp-/xoxa- + key-named values (api_key/secret/passphrase/token/authorization) | unit | `uv run pytest tests/unit/test_logging_redaction.py -q` | ❌ W0 | ⬜ pending |
| 01-02-T2 | 01-02 | 0 | (config) | T-01-02-02, T-01-02-03 | Pydantic Settings: SecretStr for all secrets; ValidationError on missing required; repr() does not leak | unit | `uv run pytest tests/unit/test_config.py -q` | ❌ W0 | ⬜ pending |
| 01-02-T3 | 01-02 | 0 | (fixtures) | — | tests/conftest.py provides temp_sqlcipher_db, sample_strategy, frozen_time, cassette_dir, mock_alpaca_client, mock_slack_client, mock_claude_sdk, configured_logging, clean_settings_env | unit | `uv run pytest --collect-only -q` (zero collection errors) | ❌ W0 | ⬜ pending |

### Wave 1 — DB + Audit + Core + Broker + Schemas (Plans 01-03, 01-04, 01-05, 01-06)

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-03-T1 | 01-03 | 1 | AUTH-03 | T-01-03-01..05 | SQLCipher PRAGMA key fires first on every connection; cipher_compatibility=4 + WAL + foreign_keys; verify_passphrase raises WrongPassphraseError; passphrase never in repr(engine) | unit | `uv run pytest tests/unit/test_db_engine.py -q` | ❌ W0 | ⬜ pending |
| 01-03-T2 | 01-03 | 1 | (data model) | T-01-03-06, 07 | 6 P1 tables all have user_id; status / event_type / scope CheckConstraints; snapshot versioning works | unit | `uv run pytest tests/unit/test_db_models.py -q` | ❌ W0 | ⬜ pending |
| 01-03-T3 | 01-03 | 1 | AUTH-03 (cross-platform) | T-01-03-03, 04 | alembic upgrade head succeeds on fresh encrypted DB; wrong passphrase after migration rejected | integration | `uv run pytest tests/integration/test_sqlcipher_passphrase.py -q -m integration` | ❌ W0 | ⬜ pending |
| 01-04 (full plan) | 01-04 | 1 | AUDT-01, AUDT-02 | T-01-04-* | Canonical JSON deterministic; SHA-256 chain genesis=0*64; append_event serialized via asyncio.Lock; walk_chain detects tampered payload / prev_hash / deletion; chain is per-user scoped | unit | `uv run pytest tests/unit/test_audit_canonical.py tests/unit/test_audit_chain.py tests/unit/test_audit_verify.py -q` | ❌ W0 | ⬜ pending |
| 01-05-T1 | 01-05 | 1 | EXEC-01, EXEC-02 | T-01-05-03, 04 | Decimal-only money math (grep gate bans float in brokers/, execution/, core/money.py); compute_client_order_id deterministic with normalized qty/side/ticker | unit | `uv run pytest tests/unit/test_money_math.py tests/unit/test_client_order_id.py -q` | ❌ W0 | ⬜ pending |
| 01-05-T2 | 01-05 | 1 | BROK-A-01 (constructor guard) | T-01-05-01 | Brokerage ABC enforces interface; AlpacaBroker(paper=False) raises BrokerConfigError; secondary probe rejects live-shaped account | unit | `uv run pytest tests/unit/test_brokerage_abc.py tests/unit/test_alpaca_constructor_guard.py -q` | ❌ W0 | ⬜ pending |
| 01-05-T3 | 01-05 | 1 | EXEC-07, BROK-A-03..05 | T-01-05-02 | Async methods wrap alpaca-py sync calls via to_thread; place_order maps LIMIT/MARKET/STOP; HTTP 422 duplicate handled via get_order_by_client_order_id (Pitfall 4) | unit | `uv run pytest tests/unit/test_alpaca_constructor_guard.py tests/unit/test_money_math.py::test_float_banned_in_money_paths -q` | ❌ W0 | ⬜ pending |
| 01-05-T4 | 01-05 | 1 | BROK-A-01, BROK-A-03..06, EXEC-07 | T-01-05-02 | Full Alpaca paper round-trip: place limit, query by client_order_id, observe fill via TradingStream, cancel; cassette mode default + live opt-in via GEKKO_TEST_LIVE_ALPACA=1 | integration | `uv run pytest tests/integration/test_alpaca_paper_round_trip.py -q -m integration` | ❌ W0 | ⬜ pending |
| 01-06-T1 | 01-06 | 1 | STRAT-04, STRAT-05, STRAT-06 | T-01-06-06 | Strategy Pydantic minimal v1 fields (D-01); HardCaps bounded; watchlist normalized; schedule_time IANA-tz validated; snapshot versioning via next_version(); plain-English diff via generate_strategy_diff | unit | `uv run pytest tests/unit/test_strategy_schema.py tests/unit/test_strategy_versioning.py -q` | ❌ W0 | ⬜ pending |
| 01-06-T2 | 01-06 | 1 | (Researcher->Decision contract; forward-compat to P4) | T-01-06-01, 05 | EvidenceSnippet source_type allowlist; ResearchBrief forward-compatible (extra='allow'); model_dump_json round-trips | unit | `uv run pytest tests/unit/test_research_brief_schema.py -q` | ❌ W0 | ⬜ pending |
| 01-06-T3 | 01-06 | 1 | REPT-04, RES-08 | T-01-06-02 | TradeProposal enforces 3-5 evidence + 1+ alternatives + confidence 0..1 + client_order_id 32-char; NoActionProposal first-class with factors_considered; EventPayload discriminated union | unit | `uv run pytest tests/unit/test_proposal_schema.py -q` | ❌ W0 | ⬜ pending |

### Wave 2 — Agent + Slack + Executor (Plans 01-07, 01-08)

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-07-T1 | 01-07 | 2 | (SDK alpha-churn re-verify) | T-01-07-SC | Re-verify Claude Agent SDK AgentDefinition/tool-restriction API before implementation | manual (blocking-human) | (checkpoint) | n/a | ⬜ pending |
| 01-07-T2 | 01-07 | 2 | RES-05 | T-01-07-06 | BudgetTracker: soft warning at 12 calls / 8K tokens / 60s; hard halt at 2x via BudgetExceeded | unit | `uv run pytest tests/unit/test_budget_tracker.py -q` | ❌ W0 | ⬜ pending |
| 01-07-T3 | 01-07 | 2 | RES-01, RES-02, RES-03, RES-04 | T-01-07-05, 08 | Researcher tools: alpaca_data + yahooquery fallback; finnhub graceful degradation; SEC EDGAR User-Agent header; web_fetch source-allowlist enforced | unit | `uv run pytest tests/unit/test_research_tools.py -q` | ❌ W0 | ⬜ pending |
| 01-07-T4 | 01-07 | 2 | STRAT-01 (system prompts) | T-01-07-03, 04 | Researcher/Decision AgentDefinitions; Decision tools restricted to [propose_trade, propose_no_action]; brief embedded inside `<RESEARCH_BRIEF>` delimiters | smoke | `uv run python -c "from gekko.agent.researcher import RESEARCHER, build_researcher_prompt; from gekko.agent.decision import DECISION, build_decision_prompt; from gekko.agent.tools.propose_trade import propose_trade; from gekko.agent.tools.propose_no_action import propose_no_action; assert DECISION.tools == ['propose_trade','propose_no_action']"` | ❌ W0 | ⬜ pending |
| 01-07-T5 | 01-07 | 2 | REPT-04, STRAT-01 | T-01-07-01, 02, 07 | ProposalWriter validates Pydantic; computes client_order_id; rejects hallucinated ticker; appends decision+proposal audit events with structured rationale per D-15; idempotent by decision_id; normalize_decimals before append_event (Pitfall 6) | unit | `uv run pytest tests/unit/test_proposal_writer.py tests/unit/test_rationale_capture.py -q` | ❌ W0 | ⬜ pending |
| 01-07-T6 | 01-07 | 2 | STRAT-01, STRAT-03, RES-08 | T-01-07-01..04 | trigger_strategy_run end-to-end with mock SDK: 2 audit events written; 1 proposal row; active Guidance injected into researcher prompt; compile_strategy_from_chat returns Strategy | integration | `uv run pytest tests/integration/test_agent_runtime.py -q -m integration` | ❌ W0 | ⬜ pending |
| 01-08-T1 | 01-08 | 2 | HITL-01 | T-01-08-06 | Block Kit card includes ticker/company/sector/action/size/rationale/evidence/quote/paper-banner; Approve+Reject+Edit-Size+Escalate buttons; REG-01 footer; build_fill_confirmation + build_no_action_message helpers | unit | `uv run pytest tests/unit/test_slack_block_kit.py -q` | ❌ W0 | ⬜ pending |
| 01-08-T2 | 01-08 | 2 | EXEC-10 | T-01-08-04 | is_market_open NYSE schedule via pandas_market_calendars handles weekday/weekend/holidays/half-days; tzdata Windows gotcha covered | unit | `uv run pytest tests/unit/test_market_hours.py -q` | ❌ W0 | ⬜ pending |
| 01-08-T3 | 01-08 | 2 | HITL-04 | T-01-08-01, 03, 07, 08 | Slack app + slash command + Approve/Reject/Edit/Escalate handlers; ack() first await (Pitfall 3); cross-user mismatch refused; P3 stubs log feature.deferred | unit | `uv run pytest tests/unit/test_approval_proposals.py -q` | ❌ W0 | ⬜ pending |
| 01-08-T4 | 01-08 | 2 | EXEC-10, HITL-04 (executor + fill) | T-01-08-02, 05 | Executor is deterministic Python (no claude_agent_sdk import); market-hours guard; place_order with deterministic client_order_id; TradingStream fill callback writes fill event + transitions proposal status; full chain test (5 events, intact) | integration | `uv run pytest tests/integration/test_slack_approval_to_executor.py -q -m integration` | ❌ W0 | ⬜ pending |

### Wave 3 — CLI + Scheduler + Dashboard + E2E Demo (Plan 01-09)

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-09-T1 | 01-09 | 3 | REG-02 | T-01-09-02 | gekko init prompts agreement and requires "I agree"; aborts on rejection or passphrase mismatch; User row written with agreement_acknowledged_at; CLI commands real (init/serve/run/doctor/strategy/audit); passphrase vault closes _GET_PASSPHRASE() indirection | unit | `uv run pytest tests/unit/test_user_agreement.py -q && uv run gekko --help` | ❌ W0 | ⬜ pending |
| 01-09-T2 | 01-09 | 3 | CADENCE-02 | T-01-09-06 | APScheduler 3.x AsyncIOScheduler + SQLAlchemyJobStore; daily fire CronTrigger with IANA tz; survives restart (job persisted); tzdata Windows handling validated | integration | `uv run pytest tests/integration/test_scheduler_persistence.py -q -m integration` | ❌ W0 | ⬜ pending |
| 01-09-T3 | 01-09 | 3 | STRAT-02, REG-01, REG-03, REG-04 | T-01-09-03, 08, 09 | FastAPI dashboard routes (/, /strategies, /strategies/{name}/edit, POST /strategies/{name}, POST /trigger/{name}, /healthz, /slack/events); paper banner + REG-01 footer on every page; queries filter by current user (REG-04); **HTMX vendored at /static/htmx.min.js (no third-party CDN); SHA-384 in VENDOR.md; SRI lint gate enforces integrity+crossorigin on any future external script; CSP meta tag defense-in-depth** | unit | `uv run pytest tests/unit/test_dashboard_templates_sri.py -q` | ❌ W0 | ⬜ pending |
| 01-09-T4 | 01-09 | 3 | walking-skeleton wave gate | — | End-to-end test executes SKELETON §Demo Script: 5 events chained (decision -> proposal -> approval -> order_submitted -> fill); walk_chain returns []; cassette-replay default | integration | `uv run pytest tests/integration/test_trigger_run_end_to_end.py -q -m integration` | ❌ W0 | ⬜ pending |
| 01-09-T5 | 01-09 | 3 | HITL-01, BROK-A-06, AUTH-03 (cross-platform), REG-02, CADENCE-02 (Windows tzdata), supply-chain | T-01-09-01, 02, 05, 07, 08 | Manual demo per SKELETON §Demo Script: real Slack DM with Block Kit card, real Alpaca paper fill via websocket, Windows wrong-passphrase rejection, REG-02 agreement UX, browser source-view confirms same-origin htmx + CSP | manual (blocking-human) | (checkpoint) | n/a | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `pyproject.toml` declares `pytest 8.x`, `pytest-asyncio`, `pytest-mock`, `respx`, `freezegun` as dev deps (Plan 01-01 Task 2)
- [x] `pyproject.toml` `[tool.pytest.ini_options]` configures `testpaths = ["tests"]`, `asyncio_mode = "auto"`, `markers = ["integration: hits external services unless cassettes are used"]` (Plan 01-01 Task 2)
- [x] `tests/conftest.py` — shared fixtures: `temp_sqlcipher_db`, `sample_strategy`, `frozen_time`, `cassette_dir`, `mock_alpaca_client`, `mock_slack_client`, `mock_claude_sdk` (Plan 01-02 Task 3 stubs; Plans 01-03+ deepen)
- [x] `tests/fixtures/cassettes/` directory with recorded Alpaca paper round-trip cassette (Plan 01-05 Task 4 — recorded once with `GEKKO_TEST_LIVE_ALPACA=1`)
- [x] `tests/fixtures/strategies/` directory (Plan 01-01 Task 3)
- [x] `uv run pytest --collect-only` runs cleanly — no errors (Plan 01-01 Task 2)
- [x] `tests/unit/test_smoke.py::test_imports` verifies every `src/gekko/` module imports without error (Plan 01-01 Task 4)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions | Owning Task |
|----------|-------------|------------|-------------------|-------------|
| Real Slack DM with Block Kit proposal card appears in user's Slack | HITL-01 | Slack delivery requires a real Slack workspace + signed webhook URL; CI cannot replay this end-to-end | Run `gekko serve` locally with `cloudflared tunnel run gekko-dev`, trigger a strategy via `/gekko run ai-infra-bull`, verify the card arrives within 60s with all fields populated | 01-09-T5 |
| Real Alpaca paper fill confirmation via TradingStream websocket | BROK-A-06 | Websocket replay is brittle; live paper round-trip is the proof | Run the walking-skeleton demo script (01-SKELETON.md §Demo Script) end-to-end with a real Alpaca paper account during market hours | 01-09-T5 |
| SQLCipher wrong-passphrase rejection on Windows | AUTH-03 (cross-platform parity) | OS-specific behavior; cross-platform parity is a P1 risk per research §SQLCipher | On Windows, run `gekko serve` with a deliberately wrong passphrase, confirm WrongPassphraseError with clear message; repeat on macOS | 01-09-T5 |
| `pyproject.toml` `tzdata` works for Windows zoneinfo | CADENCE-02 | Windows-specific gotcha (research §Gotcha 2); without `tzdata`, APScheduler `CronTrigger(timezone="America/New_York")` fails silently | Install Gekko on a fresh Windows machine, create a strategy with a `schedule_time`, confirm the scheduled fire executes without "No time zone found" error | 01-09-T5 |
| User agreement acknowledged on first-run init | REG-02 | UX/legal verification, not just a code path | Run `gekko init` on a fresh install, confirm the one-page agreement is shown and "I agree" gate is enforced | 01-09-T5 |
| Browser confirms same-origin script loading + CSP meta tag | supply-chain | Browser-level verification of CDN-avoidance (the automated SRI lint gate is necessary but not sufficient — browser-side CSP enforcement also matters) | Open `http://127.0.0.1:8000/strategies` in a browser; View Source; confirm HTMX is loaded from `/static/htmx.min.js`; confirm `Content-Security-Policy` meta tag declares `script-src 'self'` | 01-09-T5 |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies — populated by planner during PLAN.md generation (2026-06-08)
- [x] Sampling continuity: no 3 consecutive tasks without automated verify (each plan has at least one automated test gate)
- [x] Wave 0 covers all MISSING references (pytest install in Plan 01-01; conftest in 01-02; cassettes referenced in 01-05 Task 4 + 01-09 Task 4)
- [x] No watch-mode flags in test commands
- [x] Feedback latency < 10s unit / < 90s full suite
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** populated; awaiting execution
