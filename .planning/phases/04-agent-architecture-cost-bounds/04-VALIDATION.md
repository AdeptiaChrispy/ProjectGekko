---
phase: 4
slug: agent-architecture-cost-bounds
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-23
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `04-RESEARCH.md` §Validation Architecture.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (existing) |
| **Config file** | `pytest.ini` (existing) |
| **Quick run command** | `.venv/Scripts/python.exe -m pytest tests/unit/ -x -q` |
| **Full suite command** | `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/integration/` |
| **Estimated runtime** | ~30–60s (unit); integration adds more |

> Test-env note (project memory): run pytest via `.venv/Scripts/python.exe`; the full suite hangs at process exit (exit 124 ≠ failure); scope runs to relevant files and confirm any failure reproduces at the pre-change commit before treating it as a regression. The repo `pytest.ini` sets `-x` (stop-on-first-failure) — clear with `-o addopts=""` when isolating.

---

## Sampling Rate

- **After every task commit:** Run `.venv/Scripts/python.exe -m pytest tests/unit/ -x -q`
- **After every plan wave:** Run the full suite (minus integration)
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** ~60 seconds

---

## Per-Task Verification Map

| Requirement | Behavior | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|-------------|----------|------------|-----------------|-----------|-------------------|-------------|--------|
| COST-01 | 80% threshold → degrade; 100% → halt | T-04 (cost talk-past) | Deterministic pre-query() gate; LLM cannot reason past it | unit | `pytest tests/unit/test_cost_ceiling.py -x` | ❌ W0 | ⬜ pending |
| COST-01 | Halt returns `skipped_cost_halt` (not queued) | — | Scheduled cycle skipped, not deferred | unit | `pytest tests/unit/test_cost_ceiling.py::test_halt_returns_skipped -x` | ❌ W0 | ⬜ pending |
| COST-01 | Reset at user-tz midnight (DST-correct) | — | Reuses Phase-3 timezone setting (no 2nd field) | unit | `pytest tests/unit/test_cost_ceiling.py::test_tz_midnight_reset -x` | ❌ W0 | ⬜ pending |
| COST-01 | D-05: Decision agent NEVER uses Haiku model | T-04 (model-downgrade) | Haiku is triage-only; trade Decision stays full model | AST gate | `pytest tests/unit/test_decision_prompt_isolation.py::test_decision_never_haiku -x` | ❌ W0 | ⬜ pending |
| COST-02 | `GET /spend` → today total + per-strategy + 7-day | T-04 (auth) | Route on auth-gated router (require_session) | unit | `pytest tests/unit/test_spend_route.py -x` | ❌ W0 | ⬜ pending |
| COST-03 | Settings POST saves `daily_cost_ceiling_usd` | — | Server-side validation; Decimal USD | unit | `pytest tests/unit/test_settings_route.py::test_ceiling_saved -x` | ❌ W0 | ⬜ pending |
| COST-04 | One Slack DM at 80%, no repeat on later skipped cycles | — | `cost_alert_80_sent_date` guard | unit | `pytest tests/unit/test_cost_ceiling.py::test_single_dm_80 -x` | ❌ W0 | ⬜ pending |
| COST-04 | One Slack DM at 100%, no repeat | — | `cost_alert_100_sent_date` guard | unit | `pytest tests/unit/test_cost_ceiling.py::test_single_dm_100 -x` | ❌ W0 | ⬜ pending |
| COST-04 | Haiku triage gate skips "thin" cycles | — | Triage query() is disposable; never authors a trade | unit | `pytest tests/unit/test_cost_ceiling.py::test_triage_gate_skips -x` | ❌ W0 | ⬜ pending |
| COST-05 | `llm_cost` event per query() call with Decimal USD | — | normalize_decimals before append_event | unit | `pytest tests/unit/test_cost_ledger.py -x` | ❌ W0 | ⬜ pending |
| SC-2 | Suspicious-content pattern → `suspicious_content` audit event | T-04 (prompt-injection) | Logged; neutralization already via D-40 + OrderGuard universe | unit | `pytest tests/unit/test_suspicious_content.py -x` | ❌ W0 | ⬜ pending |
| SC-2 | Existing AST isolation gate remains green (regression) | T-04 | Decision boundary unchanged | AST gate | `pytest tests/unit/test_decision_prompt_isolation.py -x` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky · "❌ W0" = file created in Wave 0*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_cost_ceiling.py` — COST-01/COST-04 ceiling check + tier transitions + tz-reset + single-DM guards + triage gate
- [ ] `tests/unit/test_cost_ledger.py` — COST-05 ledger write + Decimal money math + `tokens_to_usd`
- [ ] `tests/unit/test_pricing.py` — pricing constants (Sonnet $3/$15, Haiku $1/$5 per MTok) + `tokens_to_usd()` correctness
- [ ] `tests/unit/test_spend_route.py` — COST-02 dashboard spend route (today/per-strategy/7-day)
- [ ] `tests/unit/test_settings_route.py` (extend) — COST-03 ceiling config field save/validate
- [ ] `tests/unit/test_suspicious_content.py` — SC-2 suspicious-content event gap
- [ ] `test_decision_prompt_isolation.py` (extend) — add `test_decision_never_haiku` AST assertion (D-05 invariant)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real cost accrues to ledger across live cycles | COST-05 | Needs real Claude API spend over multiple cycles | Run several real `/gekko run` cycles; confirm `gekko audit dump` shows `llm_cost` events with non-zero USD; dashboard Spend total matches sum |
| 80%/100% Slack DMs fire on real spend | COST-04 | Needs real spend crossing thresholds + live Slack | Lower ceiling to a tiny value, run cycles until 80%/100% crossed; confirm exactly one DM each |
| tz-midnight reset over a real day boundary | COST-01 | Wall-clock day boundary | Observe ledger/ceiling reset after the user-tz midnight; or manual clock override |
| Dashboard Spend view renders live | COST-02 | Needs running ASGI stack + browser | Open `/spend`; confirm today-vs-ceiling bar, per-strategy rows, 7-day trend render |

---

## Security Domain

Security enforcement is ON (ASVS L1, block_on=high). Each PLAN.md MUST carry a `<threat_model>` block. Phase-4-specific threats to model:
- **Cost talk-past (T-04 class):** the ceiling gate must be deterministic and fire BEFORE any `query()` — the LLM must not be able to reason past it. Verify the gate is not reachable/influenceable by model output.
- **Model-downgrade safety:** the trade Decision must never run on Haiku (D-05) — lock with an AST/test gate like the existing isolation/place_order gates.
- **Prompt-injection (carried):** SC-2 suspicious-content event closes the logging half; neutralization (D-40 prompt boundary + OrderGuard universe rejection) already verified in Phase 2/3 security audit.
- **Auth:** `/spend` and the ceiling Settings field are on the auth-gated router (`require_session`), consistent with Plan 03-08.
- **Money math:** USD as Decimal through `normalize_decimals`; no float drift in the ledger.

---

*Phase: 04-agent-architecture-cost-bounds*
*Validation strategy created: 2026-06-23 (derived from 04-RESEARCH.md)*
