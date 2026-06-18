---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
plan: 01
subsystem: schema-substrate
tags: [alembic, orm, pydantic, proposal-state-machine, slack-reporter]
dependency_graph:
  requires: []
  provides:
    - alembic-0004-migration
    - slack_action_dedup-orm
    - proposal-expires-at-column
    - slack-message-coords-columns
    - state-transitions-expired-edge
    - expire_proposal-helper
    - proposal-timeout-default-constant
    - post-run-result-ts-persistence
  affects:
    - migrations/versions/0004_p3_hitl_ux.py
    - src/gekko/db/models.py
    - src/gekko/schemas/strategy.py
    - src/gekko/agent/proposal_writer.py
    - src/gekko/approval/proposals.py
    - src/gekko/reporter/slack.py
    - tests/conftest.py
tech_stack:
  added: []
  patterns:
    - Frozen-vocabulary Alembic migration (_FROZEN_* tuples + batch_alter_table)
    - Keyword-only async helper for best-effort DB side effects
    - STATE_TRANSITIONS frozenset extension (data-driven state machine)
    - freezegun for deterministic timestamp assertions
key_files:
  created:
    - migrations/versions/0004_p3_hitl_ux.py
    - tests/unit/test_p3_alembic_round_trip.py
    - tests/unit/test_p3_schema_additions.py
    - tests/unit/test_proposal_writer_timeout.py
    - tests/unit/test_proposal_state_machine_expired.py
    - tests/unit/test_post_run_result_persists_ts.py
    - tests/unit/test_slack_action_dedup.py (stub)
    - tests/unit/test_expire_stale_proposals.py (stub)
    - tests/unit/test_quiet_hours_predicate.py (stub)
    - tests/unit/test_dm_bypass_categories.py (stub)
    - tests/unit/test_dm_routine_suppressed.py (stub)
    - tests/unit/test_dashboard_login.py (stub)
    - tests/unit/test_dashboard_approvals.py (stub)
    - tests/unit/test_dashboard_edit_size.py (stub)
    - tests/unit/test_proposal_card_shared_partial.py (stub)
    - tests/unit/test_daily_pnl_aggregation.py (stub)
    - tests/unit/test_daily_pnl_respects_quiet.py (stub)
    - tests/unit/test_severity_tier_dm.py (stub)
    - tests/unit/test_executor_error_dms_coverage.py (stub)
    - tests/unit/test_chat_update_expired.py (stub)
    - tests/unit/test_quiet_hours_dm_gate.py (stub)
    - tests/unit/test_transition_status_callers.py (stub)
    - tests/unit/test_edit_size_not_direct_broker.py (stub)
    - tests/unit/test_expiry_no_sdk_import.py (stub)
    - tests/unit/test_dashboard_middleware_order.py (stub)
    - tests/unit/test_slack_retry_header.py (stub)
    - tests/integration/test_dedup_race.py (stub)
    - tests/integration/test_sweep_persistence.py (stub)
    - tests/integration/test_dashboard_approve_flow.py (stub)
    - tests/integration/test_dashboard_edit_size_happy.py (stub)
    - tests/integration/test_scheduler_quiet_hours.py (stub)
    - tests/integration/test_p3_walking_skeleton.py (stub)
  modified:
    - src/gekko/db/models.py
    - src/gekko/schemas/strategy.py
    - src/gekko/agent/proposal_writer.py
    - src/gekko/approval/proposals.py
    - src/gekko/reporter/slack.py
    - tests/conftest.py
decisions:
  - "Alembic 0004 uses frozen-vocabulary _FROZEN_*_PRE/_POST tuples at migration top so the migration is state-independent (same pattern as 0002/0003)"
  - "expires_at stamped as server clock (datetime.now(UTC)) AFTER TradeProposal.model_validate — LLM cannot influence the value (T-03-01-05)"
  - "_persist_slack_message_coords is best-effort; D-53 fallback is DM-only chat.update — the Slack card already landed"
  - "SlackActionDedup.__repr__ excludes slack_trigger_id per T-03-01-03 (AUTH-04 REDACT pattern)"
  - "Alembic round-trip test skips on Windows due to SQLCipher cross-process file-lock (same caveat as Plan 02-01)"
metrics:
  duration_minutes: 95
  completed: "2026-06-18"
  tasks_completed: 4
  files_modified: 8
  files_created: 31
---

# Phase 3 Plan 1: Schema Substrate Summary

**One-liner:** Alembic 0004 migration landing SlackActionDedup table, quiet_hours columns, Proposal.expires_at+slack-message-coords, extended status/event-type CHECK vocabularies with full ORM+Pydantic mirrors and ProposalWriter server-clock expiry stamping.

## Tasks Completed

| Task | Commit | Description |
|------|--------|-------------|
| 1 — Wave 0 stubs + conftest | abe7d07 | ~30 stub test files + 3 conftest fixtures (quiet_hours_user, expired_proposal, dedup_row_factory) |
| 2 — Alembic 0004 + ORM mirror | f91d2b7 | Migration 0004_p3_hitl_ux.py + SlackActionDedup ORM class + extended _PROPOSAL_STATUSES/_EVENT_TYPES + new User/Proposal columns + Strategy Pydantic extension |
| 3 — ProposalWriter + STATE_TRANSITIONS + tests | fa6d4c7 | PROPOSAL_TIMEOUT_DEFAULT_MIN=30, expires_at stamping, PENDING->EXPIRED edge, expire_proposal helper, 4 unit test files filled |
| 4 — post_run_result ts+channel persistence | f543af2 | Capture chat_postMessage response, add _persist_slack_message_coords helper, fill test_post_run_result_persists_ts.py (3 cases) |

## What Was Built

### Alembic 0004 Migration (`migrations/versions/0004_p3_hitl_ux.py`)

Single reversible migration covering:
- New `slack_action_dedup` table per D-45: 9 columns, 2 CHECK constraints, 2 UNIQUE indexes (uq_dedup_slack, uq_dedup_dashboard)
- `users` gains `quiet_hours_start`, `quiet_hours_end`, `timezone` nullable columns (D-47/D-49)
- `proposals` gains `expires_at`, `slack_message_ts`, `slack_message_channel` nullable columns (D-51/D-53/D-61)
- `ck_proposal_status` extended with `EXPIRED` (+1 value, 9 total)
- `ck_event_type` extended with `expiration`, `dedup_click`, `edit_size`, `daily_pnl` (+4 values, 17 total)
- `downgrade()` drops table first (FK ordering), then reverses columns and CHECK constraints

### ORM + Pydantic Mirrors (`src/gekko/db/models.py`, `src/gekko/schemas/strategy.py`)

- `SlackActionDedup(Base)` class near StrategyMetadata; `__repr__` excludes `slack_trigger_id` (T-03-01-03)
- `_PROPOSAL_STATUSES` extended with `"EXPIRED"` (9 values)
- `_EVENT_TYPES` extended with 4 new values (17 total)
- `User` + `Proposal` columns added matching migration schema
- `Strategy` Pydantic: `quiet_hours_start`, `quiet_hours_end`, `proposal_timeout_minutes` optional fields with `Field(gt=0)` and HH:MM regex validator

### ProposalWriter + State Machine

- `PROPOSAL_TIMEOUT_DEFAULT_MIN: int = 30` constant in `proposal_writer.py`
- `expires_at` stamped via `datetime.now(UTC) + timedelta(minutes=...)` AFTER `TradeProposal.model_validate` — no LLM influence path (T-03-01-05)
- `STATE_TRANSITIONS` gains `("PENDING", "EXPIRED")` — sweep-side expiry edge for plan 03-03
- `expire_proposal()` async convenience wrapper with D-50 audit event payload shape (`reason`, `expired_at`, `configured_timeout_minutes`)

### Slack Reporter (`src/gekko/reporter/slack.py`)

- `post_run_result()` now captures `chat_postMessage` response instead of discarding it
- New `_persist_slack_message_coords(user_id, *, proposal_id, ts, channel)` best-effort async helper
- Persists `slack_message_ts` + `slack_message_channel` on the Proposal row
- Failure is swallowed with `log.warning(...)` — DM already landed, missing coords only degrades plan 03-04 chat.update fallback (D-53)
- Closes plan-checker BLOCKER #1 iteration 1

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] SyntaxWarning: invalid escape sequence in strategy.py docstring**
- **Found during:** Task 3 test run
- **Issue:** `^\d{2}:\d{2}$` in a non-raw docstring string produces SyntaxWarning in Python 3.12
- **Fix:** Changed `\d` to `\\d` in the docstring
- **Files modified:** `src/gekko/schemas/strategy.py`
- **Commit:** fa6d4c7

**2. [Rule 1 - Bug] TradeProposal uses decision_id not proposal_id**
- **Found during:** Task 4 first test run
- **Issue:** `post_run_result` called `tp.proposal_id` but `TradeProposal` only has `decision_id` (they are the same UUID per writer's 1:1 mapping, but the attribute name differs)
- **Fix:** Changed `tp.proposal_id` to `tp.decision_id` in the `_persist_slack_message_coords` call site
- **Files modified:** `src/gekko/reporter/slack.py`
- **Commit:** f543af2 (same task commit)

**3. [Rule 1 - Bug] _persist_slack_message_coords needs keyword-only parameters**
- **Found during:** Task 4 test run
- **Issue:** Called with `proposal_id=tp.decision_id, ts=..., channel=...` but function signature was positional; mock side_effect received keyword args and rejected them
- **Fix:** Changed function signature to `(user_id, *, proposal_id, ts, channel)` with `*` keyword separator
- **Files modified:** `src/gekko/reporter/slack.py`
- **Commit:** f543af2 (same task commit)

### Pre-existing Failures (Out of Scope)

- `tests/unit/test_cli.py::test_doctor_missing_envvar_exits_nonzero` — Phase 1 CLI test failure predating all Phase 3 work (commit be1771f). Logged to deferred-items.
- `tests/unit/test_config.py::test_missing_anthropic_key_raises_validation_error` — Phase 1 Pydantic Settings test failure (commit c1af2ef). Logged to deferred-items.

## Known Stubs

~28 Wave 0 test files contain `pytest.skip("Wave 0 stub — populated in Plan 03-XX")` bodies. These are intentional forward-declarations and do not block the plan's goals. Each owning plan (03-02 through 03-08) fills the bodies.

## Threat Surface Scan

No new network endpoints or auth paths introduced. `_persist_slack_message_coords` opens a per-user SQLCipher session using the existing `get_passphrase()` vault pattern — same trust boundary as all other DB writers in the codebase. No new threat flags.

## Self-Check

Files created/modified verified:
- `migrations/versions/0004_p3_hitl_ux.py` — exists
- `src/gekko/db/models.py` — exists, contains `class SlackActionDedup`
- `src/gekko/schemas/strategy.py` — exists, contains `quiet_hours_start`
- `src/gekko/agent/proposal_writer.py` — exists, contains `PROPOSAL_TIMEOUT_DEFAULT_MIN`
- `src/gekko/approval/proposals.py` — exists, contains `("PENDING", "EXPIRED")`
- `src/gekko/reporter/slack.py` — exists, contains `_persist_slack_message_coords`
- `tests/unit/test_post_run_result_persists_ts.py` — exists, 3 tests pass

Commits verified:
- abe7d07 — Task 1
- f91d2b7 — Task 2
- fa6d4c7 — Task 3
- f543af2 — Task 4

## Self-Check: PASSED
