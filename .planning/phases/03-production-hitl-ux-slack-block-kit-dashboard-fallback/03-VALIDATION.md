---
phase: 3
slug: production-hitl-ux-slack-block-kit-dashboard-fallback
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-17
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from 03-RESEARCH.md §Validation Architecture; Phase 1+2 cassette-based test convention preserved.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio + respx + freezegun (all already installed via Phase 1 + 2) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` (Phase 1 wired) |
| **Quick run command** | `uv run pytest tests/unit/test_*phase3*.py -x` |
| **Full suite command** | `uv run pytest -x` |
| **Estimated runtime** | ~5s quick / ~60s full (incl. cassettes) |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/unit/test_*phase3*.py -x` (~5s)
- **After every plan wave:** Run `uv run pytest tests/unit -x && uv run pytest tests/integration -x` (~60s)
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

Test files mapped to the 5 requirement IDs. AST gates (last 5 rows) enforce invariants the planner must preserve across plans.

| Test | Req | Test Type | Automated Command | File Exists | Status |
|------|-----|-----------|-------------------|-------------|--------|
| `test_slack_action_dedup.py::test_first_click_first_write` | HITL-02 | unit | `pytest tests/unit/test_slack_action_dedup.py::test_first_click_first_write -x` | ❌ W0 | ⬜ pending |
| `test_slack_action_dedup.py::test_second_click_duplicate` | HITL-02 | unit | `pytest tests/unit/test_slack_action_dedup.py::test_second_click_duplicate -x` | ❌ W0 | ⬜ pending |
| `test_slack_retry_header.py` | HITL-02 | unit | `pytest tests/unit/test_slack_retry_header.py -x` | ❌ W0 | ⬜ pending |
| `test_dedup_race.py` (cassette) | HITL-02 | integration | `pytest tests/integration/test_dedup_race.py -x` | ❌ W0 | ⬜ pending |
| `test_expire_stale_proposals.py::test_basic_sweep` (freezegun) | HITL-03 | unit | `pytest tests/unit/test_expire_stale_proposals.py::test_basic_sweep -x` | ❌ W0 | ⬜ pending |
| `test_expire_stale_proposals.py::test_skips_unexpired` | HITL-03 | unit | `pytest tests/unit/test_expire_stale_proposals.py::test_skips_unexpired -x` | ❌ W0 | ⬜ pending |
| `test_expire_stale_proposals.py::test_awaiting_2nd_channel_expires` (A7) | HITL-03 | unit | `pytest tests/unit/test_expire_stale_proposals.py::test_awaiting_2nd_channel_expires -x` | ❌ W0 | ⬜ pending |
| `test_sweep_persistence.py` (subprocess restart) | HITL-03 | integration | `pytest tests/integration/test_sweep_persistence.py -x` | ❌ W0 | ⬜ pending |
| `test_proposal_writer_timeout.py` | HITL-03 | unit | `pytest tests/unit/test_proposal_writer_timeout.py -x` | ❌ W0 | ⬜ pending |
| `test_chat_update_expired.py` (respx) | HITL-03 | unit | `pytest tests/unit/test_chat_update_expired.py -x` | ❌ W0 | ⬜ pending |
| `test_quiet_hours_predicate.py::test_overnight_in_window` | HITL-05 | unit | `pytest tests/unit/test_quiet_hours_predicate.py::test_overnight_in_window -x` | ❌ W0 | ⬜ pending |
| `test_quiet_hours_predicate.py::test_outside_window` | HITL-05 | unit | `pytest tests/unit/test_quiet_hours_predicate.py::test_outside_window -x` | ❌ W0 | ⬜ pending |
| `test_quiet_hours_predicate.py::test_strategy_override` | HITL-05 | unit | `pytest tests/unit/test_quiet_hours_predicate.py::test_strategy_override -x` | ❌ W0 | ⬜ pending |
| `test_quiet_hours_predicate.py::test_dst_spring_forward` (freezegun) | HITL-05 | unit | `pytest tests/unit/test_quiet_hours_predicate.py::test_dst_spring_forward -x` | ❌ W0 | ⬜ pending |
| `test_quiet_hours_predicate.py::test_dst_fall_back` | HITL-05 | unit | `pytest tests/unit/test_quiet_hours_predicate.py::test_dst_fall_back -x` | ❌ W0 | ⬜ pending |
| `test_dm_bypass_categories.py` | HITL-05 | unit | `pytest tests/unit/test_dm_bypass_categories.py -x` | ❌ W0 | ⬜ pending |
| `test_dm_routine_suppressed.py` | HITL-05 | unit | `pytest tests/unit/test_dm_routine_suppressed.py -x` | ❌ W0 | ⬜ pending |
| `test_scheduler_quiet_hours.py` | HITL-05 | integration | `pytest tests/integration/test_scheduler_quiet_hours.py -x` | ❌ W0 | ⬜ pending |
| `test_dashboard_login.py::test_get_login` | DASH-04 | unit | `pytest tests/unit/test_dashboard_login.py::test_get_login -x` | ❌ W0 | ⬜ pending |
| `test_dashboard_login.py::test_post_login_success` | DASH-04 | unit | `pytest tests/unit/test_dashboard_login.py::test_post_login_success -x` | ❌ W0 | ⬜ pending |
| `test_dashboard_login.py::test_post_login_wrong_passphrase` | DASH-04 | unit | `pytest tests/unit/test_dashboard_login.py::test_post_login_wrong_passphrase -x` | ❌ W0 | ⬜ pending |
| `test_dashboard_approvals.py::test_unauth_redirects` | DASH-04 | unit | `pytest tests/unit/test_dashboard_approvals.py::test_unauth_redirects -x` | ❌ W0 | ⬜ pending |
| `test_dashboard_approvals.py::test_lists_pending` | DASH-04 | unit | `pytest tests/unit/test_dashboard_approvals.py::test_lists_pending -x` | ❌ W0 | ⬜ pending |
| `test_dashboard_approve_flow.py` | DASH-04 | integration | `pytest tests/integration/test_dashboard_approve_flow.py -x` | ❌ W0 | ⬜ pending |
| `test_dashboard_edit_size.py::test_drift_rejected` | DASH-04 | unit | `pytest tests/unit/test_dashboard_edit_size.py::test_drift_rejected -x` | ❌ W0 | ⬜ pending |
| `test_dashboard_edit_size_happy.py` | DASH-04 | integration | `pytest tests/integration/test_dashboard_edit_size_happy.py -x` | ❌ W0 | ⬜ pending |
| `test_proposal_card_shared_partial.py` (snapshot) | DASH-04 | unit | `pytest tests/unit/test_proposal_card_shared_partial.py -x` | ❌ W0 | ⬜ pending |
| `test_daily_pnl_aggregation.py` | REPT-01 | unit | `pytest tests/unit/test_daily_pnl_aggregation.py -x` | ❌ W0 | ⬜ pending |
| `test_daily_pnl_respects_quiet.py` (asserts deferred when 16:30 ET in quiet window; fires when outside) | REPT-01 | unit | `pytest tests/unit/test_daily_pnl_respects_quiet.py -x` | ❌ W0 | ⬜ pending |
| `test_severity_tier_dm.py` | REPT-01 | unit | `pytest tests/unit/test_severity_tier_dm.py -x` | ❌ W0 | ⬜ pending |
| `test_executor_error_dms_coverage.py` (audit MarketClosed + BrokerOrderError) | REPT-01 | unit | `pytest tests/unit/test_executor_error_dms_coverage.py -x` | ❌ W0 | ⬜ pending |
| `test_quiet_hours_dm_gate.py` (AST gate per Pitfall 9) | All | unit | `pytest tests/unit/test_quiet_hours_dm_gate.py -x` | ❌ W0 | ⬜ pending |
| `test_transition_status_callers.py` (AST gate) | All | unit | `pytest tests/unit/test_transition_status_callers.py -x` | ❌ W0 | ⬜ pending |
| `test_edit_size_not_direct_broker.py` (AST gate) | HITL-02 | unit | `pytest tests/unit/test_edit_size_not_direct_broker.py -x` | ❌ W0 | ⬜ pending |
| `test_expiry_no_sdk_import.py` (AST gate — sweep is non-LLM) | HITL-03 | unit | `pytest tests/unit/test_expiry_no_sdk_import.py -x` | ❌ W0 | ⬜ pending |
| `test_dashboard_middleware_order.py` (AST gate) | DASH-04 | unit | `pytest tests/unit/test_dashboard_middleware_order.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Phase 3 introduces ~30 new test files (no framework install needed — pytest + asyncio + respx + freezegun all pre-installed via Phase 1 + 2). All test stubs land in Wave 0; bodies fill in during their owning task.

**Unit test stubs:**
- [ ] `tests/unit/test_slack_action_dedup.py` — first-write / duplicate behavior, IntegrityError handling
- [ ] `tests/unit/test_slack_retry_header.py` — X-Slack-Retry-Num gating of ephemeral
- [ ] `tests/unit/test_expire_stale_proposals.py` — sweep correctness with freezegun
- [ ] `tests/unit/test_quiet_hours_predicate.py` — overnight wrap + DST corner cases (spring-forward + fall-back)
- [ ] `tests/unit/test_dm_bypass_categories.py` — kill/error/first-live always fire
- [ ] `tests/unit/test_dm_routine_suppressed.py` — routine fill suppression during quiet hours
- [ ] `tests/unit/test_dashboard_login.py` — passphrase prompt + cookie mint
- [ ] `tests/unit/test_dashboard_approvals.py` — index renders + auth redirect
- [ ] `tests/unit/test_dashboard_edit_size.py` — drift error fragment, happy-path modal close
- [ ] `tests/unit/test_proposal_card_shared_partial.py` — Slack-card schema parity
- [ ] `tests/unit/test_daily_pnl_aggregation.py` — query + render
- [ ] `tests/unit/test_daily_pnl_respects_quiet.py` — quiet-hours bypass
- [ ] `tests/unit/test_severity_tier_dm.py` — emoji prefix
- [ ] `tests/unit/test_executor_error_dms_coverage.py` — line 454 + line 654 covered
- [ ] `tests/unit/test_chat_update_expired.py` — respx mock of Slack chat.update
- [ ] `tests/unit/test_proposal_writer_timeout.py` — Strategy.proposal_timeout_minutes stamped on expires_at at T0

**AST-gate test stubs (per Pitfall 9 + Phase 2 02-03 / 02-06 grep-gate convention):**
- [ ] `tests/unit/test_quiet_hours_dm_gate.py` — every `_send_slack_dm` call site classified (routine vs bypass)
- [ ] `tests/unit/test_transition_status_callers.py` — every caller catches CHECK violation (no silent swallow)
- [ ] `tests/unit/test_edit_size_not_direct_broker.py` — edit-size goes through state machine + executor, never direct `place_order`
- [ ] `tests/unit/test_expiry_no_sdk_import.py` — sweep import chain does NOT import `claude_agent_sdk` (deterministic Python firewall, no LLM)
- [ ] `tests/unit/test_dashboard_middleware_order.py` — `SessionMiddleware` registered BEFORE banner-state middleware in `create_app` (Starlette reverse-order execution rule)

**Integration test stubs:**
- [ ] `tests/integration/test_dedup_race.py` — cassette: approve + edit race
- [ ] `tests/integration/test_sweep_persistence.py` — subprocess restart + sweep coalesce
- [ ] `tests/integration/test_dashboard_approve_flow.py` — full HTMX cycle ending in executor dispatch
- [ ] `tests/integration/test_dashboard_edit_size_happy.py` — HTMX modal end-to-end happy path
- [ ] `tests/integration/test_scheduler_quiet_hours.py` — APScheduler trigger_strategy_run skipped in window

**Fixture additions to `tests/conftest.py`:**
- [ ] `quiet_hours_user` — User row with `quiet_hours_start=time(22,0), quiet_hours_end=time(7,0), timezone='America/New_York'`
- [ ] `expired_proposal` — Proposal with `status='PENDING'`, `expires_at` in the past
- [ ] `dedup_row_factory` — convenience factory for `slack_action_dedup` rows

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real Slack dup-click survives at-least-once delivery | HITL-02 | Wall-clock + real Slack retry; cannot mock Slack's actual retry timing in a cassette | End-of-phase demo: click Approve twice rapidly in real Slack; observe one execution + one ephemeral "already approved" toast |
| 60s sweep latency observed on real wall clock | HITL-03 | Cannot mock APScheduler clock and Slack chat.update at the same time deterministically | End-of-phase demo: create proposal with `expires_at=utcnow()+30s`, observe card swap to EXPIRED within ≤90s of due time + receive DM |
| Quiet hours predicate crosses a real DST boundary | HITL-05 | freezegun + ZoneInfo together is fragile across DST; real clock is the truth | Defer to Spring 2027 OR run a one-shot CI job with system date set to 2027-03-13 (DST spring-forward day) |
| Dashboard `/approvals` end-to-end in browser with cookie session | DASH-04 | HTMX behavior + cookie attributes + real Slack-down scenario; can't be cassette-replayed | End-of-phase demo: stop Slack Socket Mode, navigate to `http://localhost:8000/login`, enter passphrase, approve from `/approvals`, observe execution + fill DM (Slack restored after) |
| Daily P&L DM fires at 16:30 ET on a real trading day | REPT-01 | APScheduler CronTrigger cannot be deterministically jumped to 16:30 across timezones in a unit test | Observe one real-day DM at 16:30 America/New_York; verify content includes today's fills, gross P&L, open positions, errors |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (~30 new test files + 3 conftest fixtures)
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] AST gates enforce invariants the planner must preserve (5 listed)
- [ ] `nyquist_compliant: true` set in frontmatter
- [ ] Plan-checker grep confirms every plan task has `<acceptance_criteria>` pointing at a row in the Per-Task Verification Map (table above)

**Approval:** pending
