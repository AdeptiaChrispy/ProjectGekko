---
phase: 5
slug: trust-ladder-per-strategy-promotion-portfolio-caps
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-26
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x + pytest-asyncio |
| **Config file** | pyproject.toml (existing) |
| **Quick run command** | `.venv/Scripts/python.exe -m pytest -q -x` |
| **Full suite command** | `.venv/Scripts/python.exe -m pytest -q` |
| **Estimated runtime** | ~60 seconds |

---

## Sampling Rate

- **After every task commit:** Run quick run command (scoped to the touched test module)
- **After every plan wave:** Run full suite command
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

> Filled in by the planner against the final task IDs. Highest-risk invariants (from RESEARCH.md ## Validation Architecture) that MUST have automated verification:

| Invariant | Requirement | Test Type | Status |
|-----------|-------------|-----------|--------|
| Auto-execute is impossible unless promotion criteria are met (clean streak) | TRUST-01 / TRUST-06 | unit + AST/grep gate | ⬜ pending |
| Portfolio caps reject orders that per-strategy caps would have allowed | TRUST-02 | unit (property-based on aggregate exposure) | ⬜ pending |
| Capital ceiling stacks with max_position_pct + portfolio caps in OrderGuard | TRUST-03 | unit | ⬜ pending |
| Anomaly demotes (back to propose-only) BEFORE max_daily_loss_usd halts trading | TRUST-04 | unit (threshold ordering) | ⬜ pending |
| LIVE+auto still passes the Phase-2 first-live dual-channel gate (no bypass) | TRUST-01 / TRUST-03 | unit | ⬜ pending |
| Enabling auto when criteria unmet is blocked with explanation, not silent | TRUST-05 | unit + behavior | ⬜ pending |
| Anomaly-demotion DM bypasses quiet hours; auto-exec informational DM respects them | TRUST-04 | unit | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] Test stubs for the clean-streak scanner (events-log backward scan)
- [ ] Test stubs for portfolio-cap + capital-ceiling OrderGuard checks
- [ ] Test stubs for the auto-execute branch and the anomaly evaluator
- [ ] Reuse existing `tests/conftest.py` fixtures (per-user session-factory shim, in-memory SQLite)

*Existing pytest infrastructure (Phases 1–4) covers framework + fixtures; only new test modules are needed.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Dashboard promote/demote confirmation UX | TRUST-01 | HTMX visual + confirm-step flow | Promote a streak-qualified strategy via dashboard; verify explicit confirm; one-click demote takes effect next cycle |
| Slack DM delivery (anomaly bypass + informational) | TRUST-04 | external Slack side-effect | Trigger anomaly in paper; verify urgent DM bypasses quiet hours; verify auto-exec informational DM is suppressed during quiet hours |

*Most invariants have automated verification; the above are UI/external-surface checks.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
