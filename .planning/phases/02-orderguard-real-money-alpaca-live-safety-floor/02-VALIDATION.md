---
phase: 2
slug: orderguard-real-money-alpaca-live-safety-floor
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-15
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]`) — established in Phase 1 Plan 01-01 |
| **Quick run command** | `uv run pytest tests/unit -x -q` |
| **Full suite command** | `uv run pytest -x` |
| **Estimated runtime** | ~30s unit / ~90s full (Phase 1 baseline; Phase 2 adds ~10s for OrderGuard + ~15s for kill-switch integration) |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/unit -x -q`
- **After every plan wave:** Run `uv run pytest -x`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds (unit suite ceiling)

---

## Per-Task Verification Map

> Populated by gsd-planner during Step 8. Each task in every PLAN.md MUST appear here with: Task ID, REQ-ID, threat reference (if any), expected secure behavior, test type (unit/integration/cassette/walking-skeleton/manual), automated command, file-exists status (Wave 0 stubs vs already-present).

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| {to be filled by planner} | | | | | | | | | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Phase 2 inherits Phase 1's test infrastructure (`tests/conftest.py`, `tests/unit/`, `tests/integration/`, `cassettes/`). Wave 0 adds:

- [ ] `tests/unit/test_orderguard.py` — stubs for EXEC-04 (universe + hard caps + qty×price 2%), EXEC-05 (paper/live pairing), EXEC-11 (PDT + T+1 BLOCK), EXEC-09 (wash-sale FLAG)
- [ ] `tests/unit/test_kill_switch.py` — stubs for EXEC-06 (5s SLA + persistence + cancel semantics)
- [ ] `tests/unit/test_rate_limit_backoff.py` — stubs for EXEC-08 (tenacity-decorated GETs only; place_order POST grep-gate)
- [ ] `tests/unit/test_prompt_injection_minimums.py` — stubs for RES-06/07 (`<untrusted_content>` wrapping + source allowlist)
- [ ] `tests/integration/test_first_live_gate.py` — stubs for HITL-06 (Slack + dashboard BOTH required for first live trade)
- [ ] `tests/integration/test_promote_paper_to_live.py` — walking-skeleton-style for success criterion 5 (full HITL flow with all guards firing)
- [ ] `cassettes/alpaca_live_order_placed.yaml` — cassette for live-mode order round-trip (live env creds stubbed; OrderGuard validates pairing)
- [ ] `cassettes/alpaca_429_rate_limit.yaml` — cassette for EXEC-08 backoff path
- [ ] `tests/conftest.py` — extend with `account_mode` fixture (paper vs live), `kill_state` fixture, `live_credential_pair` fixture

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| First real-money trade end-to-end via Slack DM + dashboard dual approval | HITL-06, Success Criterion 3 + 5 | Requires real Alpaca live credentials, real Slack workspace, real dashboard cookie session — cannot be replayed in CI | Operator promotes a paper strategy to live ($1 limit order), receives Slack DM with `[LIVE]` red banner, opens dashboard, sees dual-channel-required banner, clicks confirm in BOTH surfaces, observes order placed + filled + audit chain intact across `decision → proposal → approval → live_confirmation_pending → live_confirmation_received → order_submitted → fill` (7-event chain) |
| Kill switch 5-second SLA via Slack `/gekko kill CONFIRM` | EXEC-06, Success Criterion 2 | SLA timing requires real network + real Alpaca cancel_orders round-trip + real Slack ack latency — cassette can't measure wall-clock | Operator triggers `/gekko kill CONFIRM` in Slack while 2+ open orders exist; timer starts on slash-command receipt; stopwatch ends when dashboard shows all orders cancelled + `kill_state.active=true`. MUST be ≤5s in 9/10 trials (one outlier allowed for network jitter) |
| Kill switch persistence across restart | EXEC-06, D-36 | Requires actual process restart with `kill_state.active=true` already in DB | While kill is active, operator stops `gekko serve`, restarts; on boot the runtime MUST (a) refuse all new place_order calls before they reach OrderGuard, (b) DM operator that kill is still active, (c) NOT auto-clear the flag |
| Red-banner visibility on Slack + dashboard for live mode | EXEC-05, Success Criterion 4 | Visual inspection — automated DOM scrape on dashboard CAN be done but Slack rendering can't | Operator views a live-mode strategy's HITL card in Slack (red banner with `[LIVE — REAL MONEY]` mrkdwn-safe header) + opens dashboard strategy page (red banner CSS + screen-reader-readable text per UI-SPEC) |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (9 test files + 2 cassettes + conftest extensions above)
- [ ] No watch-mode flags (CI must run with `-x` not `--ff`)
- [ ] Feedback latency < 30s (unit suite)
- [ ] `place_order` grep-gate test exists in `tests/unit/test_orderguard.py` asserting `place_order` is NOT decorated with tenacity (EXEC-03 / Pitfall 4 — POSTs never blind-retry)
- [ ] `nyquist_compliant: true` set in frontmatter (after planner fills Per-Task Verification Map)

**Approval:** pending — set after gsd-planner populates the Per-Task Verification Map in Step 8 and gsd-plan-checker verifies coverage in Step 10.
