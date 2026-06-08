---
phase: 1
slug: foundation-vertical-slice-alpaca-paper-slack-hitl
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-08
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution. Phase 1 builds a Walking Skeleton — the validation strategy must prove the end-to-end loop works while keeping per-task feedback latency low.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | `pytest 8.x` (Python 3.12 compatible) with `pytest-asyncio`, `pytest-mock`, `respx` (async HTTP mocking), `freezegun` (time control) |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]`) — Wave 0 installs |
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

This table is populated by the planner during PLAN.md generation. Each task in every plan must have a row here with either an automated command or a Wave 0 dependency. Pre-populated entries cover the highest-value tests; the planner will add task-specific rows.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| (planner-populated) | — | 0 | (env audit) | — | Python 3.12 + uv present; Alpaca paper + Slack + Anthropic keys validated | manual | `uv run gekko doctor` (added in Wave 0) | ❌ W0 | ⬜ pending |
| (planner-populated) | — | 0 | (scaffold) | — | `gekko --help` shows commands | unit | `uv run pytest tests/unit/test_cli.py::test_help_smoke -q` | ❌ W0 | ⬜ pending |
| (planner-populated) | — | 1 | EXEC-01 | — | Decimal money math, float banned in `gekko/brokers/`, `gekko/execution/`, `gekko/core/money.py` | unit | `uv run pytest tests/unit/test_money_math.py -q` | ❌ W0 | ⬜ pending |
| (planner-populated) | — | 1 | EXEC-02 | Knight Capital | Deterministic `client_order_id = sha256(strategy_id\|decision_id\|side\|qty\|ticker)[:32]`; same inputs → same id | unit | `uv run pytest tests/unit/test_client_order_id.py -q` | ❌ W0 | ⬜ pending |
| (planner-populated) | — | 1 | AUDT-01, AUDT-02 | Audit tampering | Append-only `events` table; SHA-256 hash chain over canonical `{event_type,payload,ts,user_id}`; `walk_chain()` detects break | unit | `uv run pytest tests/unit/test_audit_chain.py -q` | ❌ W0 | ⬜ pending |
| (planner-populated) | — | 1 | AUTH-03, AUTH-04 | Credential leak | SQLCipher whole-DB encryption; wrong passphrase → `BrokerConfigError`; structlog redacts API keys & Bearer tokens at processor | unit + integration | `uv run pytest tests/unit/test_db_engine.py tests/unit/test_logging_redaction.py -q` | ❌ W0 | ⬜ pending |
| (planner-populated) | — | 1 | BROK-A-01, BROK-A-03..06 | Paper-vs-live mix-up | `AlpacaBroker(paper=True)` only; live key rejected at construction; full paper round-trip (place → query → stream fill) | integration | `uv run pytest tests/integration/test_alpaca_paper_round_trip.py -q` (cassette by default, live with `GEKKO_TEST_LIVE_ALPACA=1`) | ❌ W0 | ⬜ pending |
| (planner-populated) | — | 1 | EXEC-10 | After-hours order | Market-hours guard via `pandas_market_calendars`; orders rejected outside RTH unless explicitly allowed | unit | `uv run pytest tests/unit/test_market_hours.py -q` | ❌ W0 | ⬜ pending |
| (planner-populated) | — | 2 | STRAT-01..06 | — | Strategy create/load/edit via NL chat → Pydantic; snapshot-row versioning; plain-English diff | unit | `uv run pytest tests/unit/test_strategy_versioning.py -q` | ❌ W0 | ⬜ pending |
| (planner-populated) | — | 2 | RES-01..05, RES-08 | Prompt injection (P4 hardens) | Research tools return structured `ResearchBrief`; per-cycle budget tracker enforces soft 12/8K/60s with 2x grace | unit | `uv run pytest tests/unit/test_research_tools.py tests/unit/test_budget_tracker.py -q` | ❌ W0 | ⬜ pending |
| (planner-populated) | — | 2 | HITL-01, HITL-04 | — | Block Kit proposal card includes ticker/company/sector/action/size/rationale/evidence/quote/paper-banner; Approve/Reject route to handlers | unit | `uv run pytest tests/unit/test_slack_block_kit.py -q` | ❌ W0 | ⬜ pending |
| (planner-populated) | — | 2 | REPT-04 | — | Every trade execution writes structured rationale (evidence[], confidence, alternatives) to `events.payload_json` | unit | `uv run pytest tests/unit/test_rationale_capture.py -q` | ❌ W0 | ⬜ pending |
| (planner-populated) | — | 2 | CADENCE-02 | NTP drift (P7) | APScheduler 3.x AsyncIOScheduler + SQLAlchemyJobStore survives restart; daily fire at configured tz time | integration | `uv run pytest tests/integration/test_scheduler_persistence.py -q` | ❌ W0 | ⬜ pending |
| (planner-populated) | — | 2 | REG-01..04 | Adviser-line | UI surfaces frame Gekko as "personal execution tooling"; first-run prompts user agreement (REG-02); per-user isolated install (REG-03); no central perf dashboard (REG-04) | unit + manual | `uv run pytest tests/unit/test_user_agreement.py -q` + UI inspection on `gekko serve` | ❌ W0 | ⬜ pending |
| (planner-populated) | — | 2 | walking-skeleton | — | End-to-end demo script (01-SKELETON.md §Demo Script) executes; 5 events chained: decision → proposal → approval → order_submitted → fill | integration | `uv run pytest tests/integration/test_trigger_run_end_to_end.py -q` (cassette-replay default) | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `pyproject.toml` declares `pytest 8.x`, `pytest-asyncio`, `pytest-mock`, `respx`, `freezegun` as dev deps
- [ ] `pyproject.toml` `[tool.pytest.ini_options]` configures `testpaths = ["tests"]`, `asyncio_mode = "auto"`, `markers = ["integration: hits external services unless cassettes are used"]`
- [ ] `tests/conftest.py` — shared fixtures: `temp_sqlcipher_db`, `sample_strategy`, `frozen_time`, `cassette_dir`, `mock_alpaca_client`, `mock_slack_client`, `mock_claude_sdk`
- [ ] `tests/fixtures/cassettes/` directory with recorded Alpaca paper round-trip cassette (recorded once with `GEKKO_TEST_LIVE_ALPACA=1`)
- [ ] `tests/fixtures/strategies/` directory with sample strategy YAMLs for tests
- [ ] `uv run pytest --collect-only` runs cleanly — no errors — confirms framework installs and `tests/` is discoverable
- [ ] `tests/unit/test_smoke.py::test_imports` verifies every `src/gekko/` module imports without error (catches broken `__init__.py` early)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real Slack DM with Block Kit proposal card appears in user's Slack | HITL-01 | Slack delivery requires a real Slack workspace + signed webhook URL (cloudflared / ngrok); CI cannot replay this end-to-end | Run `gekko serve` locally with `cloudflared tunnel run gekko-dev`, trigger a strategy via `/gekko run ai-infra-bull`, verify the card arrives in the user's DM within 60s with all fields populated |
| Real Alpaca paper fill confirmation via TradingStream websocket | BROK-A-06 | Websocket replay is brittle; live paper round-trip is the proof | Run the walking-skeleton demo script (01-SKELETON.md §Demo Script) end-to-end with a real Alpaca paper account |
| SQLCipher wrong-passphrase rejection on Windows | AUTH-03 | OS-specific behavior; cross-platform parity is a P1 risk per research §SQLCipher | On Windows, run `gekko serve` with a deliberately wrong passphrase, confirm `BrokerConfigError` (or equivalent) with a clear message and no DB read; repeat on macOS |
| `pyproject.toml` declares `tzdata` for Windows zoneinfo | CADENCE-02 | Windows-specific gotcha (research §Gotcha 2); without `tzdata`, APScheduler `CronTrigger(timezone="America/New_York")` fails silently at first fire | Install Gekko on a fresh Windows machine, create a strategy with a `schedule_time`, confirm the scheduled fire executes without "No time zone found" error |
| User agreement acknowledged on first-run init | REG-02 | UX/legal verification, not a code path | Run `gekko init` on a fresh install, confirm the one-page agreement is shown and the user must explicitly type "I agree" (or equivalent) before strategy creation is unlocked |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies — populated by planner during PLAN.md generation
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (pytest install, conftest, cassettes)
- [ ] No watch-mode flags in test commands
- [ ] Feedback latency < 10s unit / < 90s full suite
- [ ] `nyquist_compliant: true` set in frontmatter (after planner populates per-task rows)

**Approval:** pending
