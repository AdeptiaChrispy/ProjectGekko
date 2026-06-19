---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
plan: "11"
subsystem: approval
tags:
  - edit-size
  - cap-validation
  - slack-modal
  - dashboard-htmx
  - gap-closure
dependency_graph:
  requires:
    - "03-05"  # original edit-size modal implementation
    - "02-02"  # OrderGuard hard caps
  provides:
    - _check_edit_size_caps helper (sync, Decimal-exact)
    - Cap-based operator edit-size gate (Slack + dashboard)
  affects:
    - src/gekko/approval/actions.py
    - src/gekko/approval/slack_handler.py
    - src/gekko/dashboard/routes.py
    - src/gekko/dashboard/templates/edit_size_modal.html.j2
    - 03-UI-SPEC.md Surface 1
    - 03-CONTEXT.md D-54
tech_stack:
  added: []
  patterns:
    - Decimal-exact cap math (no float)
    - Sync helper + async caller (equity fetched by caller, not helper)
    - asyncio.wait_for timeout for Slack 3s ack window
    - Fail-open (equity=0) when broker unavailable
key_files:
  created:
    - tests/unit/test_edit_size_caps.py
  modified:
    - src/gekko/approval/actions.py
    - src/gekko/approval/slack_handler.py
    - src/gekko/dashboard/routes.py
    - src/gekko/dashboard/templates/edit_size_modal.html.j2
    - .planning/phases/03-production-hitl-ux-slack-block-kit-dashboard-fallback/03-UI-SPEC.md
    - .planning/phases/03-production-hitl-ux-slack-block-kit-dashboard-fallback/03-CONTEXT.md
decisions:
  - "_check_edit_size_caps replaces _drift_check as the sole operator-edit gate on both Slack and dashboard paths"
  - "Fail-open when broker returns equity=0 (paper account); OrderGuard re-checks at execute_proposal time"
  - "asyncio.wait_for(broker.get_account(), timeout=2.5) to respect Slack 3-second ack window"
  - "Sync helper takes account_equity as parameter; callers fetch equity async before calling"
metrics:
  duration: "35min"
  completed: "2026-06-19"
  tasks: 2
  files: 6
---

# Phase 03 Plan 11: Edit-Size Cap Redesign Summary

Cap-based operator edit-size validation replacing the 2% drift gate on both Slack and dashboard paths.

## What Was Built

The edit-size modal previously rejected operator resizes (even 47→50 shares, ~6% change) using the agent's 2% drift-vs-target-notional check. This made the feature unusable for any operator adjustment. Plan 03-11 replaces this with `_check_edit_size_caps` — a validation against the strategy's absolute risk bounds (`max_position_pct * account_equity`).

### Task 1: `_check_edit_size_caps` helper (TDD)

New synchronous helper in `src/gekko/approval/actions.py`:

- `_check_edit_size_caps(qty, ref_price, strategy, account_equity) -> tuple[bool, str]`
- Cap 0: qty ≤ 0 → `(False, "Quantity must be at least 1 share.")`
- Cap 1: `new_notional > strategy.hard_caps.max_position_pct * account_equity` → `(False, "That's above your max of $X (~N shares) — pick a smaller number.")`
- Fail-open when `account_equity == 0` (paper account before first funding)
- All arithmetic is Decimal-exact (no float)
- `_drift_check` remains unchanged — it is still the agent output-consistency guard (D-27)
- 6 unit tests in `tests/unit/test_edit_size_caps.py`: all pass

### Task 2: Wiring + template + spec updates

**slack_handler.py (`handle_edit_size_view_submission`):**
- Replaced `_drift_check` with `_check_edit_size_caps`
- Fetches account equity via `asyncio.wait_for(broker.get_account(), timeout=2.5)` — respects Slack 3-second ack window
- Loads strategy from DB for hard caps
- Falls back to `equity=0` (fail-open) on broker timeout or construction failure
- `response_action='errors'` returns plain-language cap message

**slack_handler.py (`handle_edit_size`):**
- Modal title: `"Edit order size — BUY 47 AAPL (~$9,400.00)"` (SIDE QTY TICKER ~$total)
- Block 2 (context): plain-language framing — "Current: BUY 47 AAPL (~$9,400.00)\nRef price: $200\nAdjust shares below — cap enforced at submit"
- Block 3 (context): "Your change is validated against your strategy's risk caps, not against the agent's original target."

**routes.py (`edit_size_get`):**
- Added `side` and `original_notional` to template context

**routes.py (`edit_size_submit`):**
- Replaced `_drift_check` + drift percentage check with `_check_edit_size_caps`
- Loads strategy from DB; fetches equity from paper broker with 2.5s timeout
- Fail-open on broker/equity errors
- Re-renders modal with plain-language cap error on failure
- Removed stale `drift_pct` from audit event payload (now `{old_qty, new_qty, old_notional, new_notional}`)

**edit_size_modal.html.j2:**
- Headline: `Edit order size — {{ side }} {{ qty }} {{ ticker }} (~${{ original_notional }})`
- Help text: "Ref price: / Current order: / Adjust quantity below. Max order size is enforced at submit."
- Removed "Drift > 2% will be rejected" copy
- `drift_error` div still present (reused for cap error messages)

**03-UI-SPEC.md Surface 1:** Updated modal title, block shapes, cap-exceeded error copy, state transitions to reflect cap-based validation contract. `_drift_check is NOT called for operator edits.` noted explicitly.

**03-CONTEXT.md D-54:** Rewritten to document `_check_edit_size_caps` as the sole operator-edit gate. UAT finding 2026-06-19 noted. Two-layer defense explained: cap check at modal submit + OrderGuard at `execute_proposal` time.

## Deviations from Plan

None — plan executed exactly as written.

The `_edit_size_submit_workflow` background function removed `drift_pct` from the audit event payload since the field is no longer computed. This is an intentional minor deviation aligned with the plan's goal (no drift computation for operator edits). The audit event shape remains valid per D-45.

## Acceptance Criteria Verification

| Check | Result |
|-------|--------|
| `grep -c _check_edit_size_caps src/gekko/approval/slack_handler.py` | 3 (>=1) |
| `grep -c _check_edit_size_caps src/gekko/dashboard/routes.py` | 3 (>=1) |
| `grep -c "That's above your max" 03-UI-SPEC.md` | 3 (>=1) |
| `grep -c _check_edit_size_caps 03-CONTEXT.md` | 3 (>=1) |
| `_drift_check` not in `handle_edit_size_view_submission` body | PASS |
| `_drift_check` not imported/called in `edit_size_submit` | PASS |
| 6 unit tests in test_edit_size_caps.py | 6/6 pass |
| place_order AST gate (test_edit_size_not_direct_broker) | PASS |

## Known Stubs

None — the cap check is fully wired. The fail-open (equity=0) behavior when the broker is unavailable is intentional and documented, not a stub.

## Threat Surface Scan

No new network endpoints introduced. The `broker.get_account()` call is an existing Alpaca API call already used by OrderGuard. The `asyncio.wait_for(..., timeout=2.5)` timeout is a new mitigation for T-03-11-05 (Denial of Service via broker timeout blocking Slack ack).

## Self-Check: PASSED

Files exist:
- `src/gekko/approval/actions.py` — contains `_check_edit_size_caps`
- `tests/unit/test_edit_size_caps.py` — 6 tests
- `src/gekko/approval/slack_handler.py` — uses `_check_edit_size_caps`
- `src/gekko/dashboard/routes.py` — uses `_check_edit_size_caps`
- `src/gekko/dashboard/templates/edit_size_modal.html.j2` — updated
- `03-UI-SPEC.md` — Surface 1 updated
- `03-CONTEXT.md` — D-54 updated

Commits exist:
- `0090c00`: feat(03-11): add _check_edit_size_caps helper to actions.py
- `201d204`: feat(03-11): wire cap check into Slack modal + dashboard; update template + spec docs
