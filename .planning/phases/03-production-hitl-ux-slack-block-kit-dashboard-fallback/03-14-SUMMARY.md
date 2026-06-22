---
phase: "03"
plan: "14"
subsystem: "dashboard, slack, approval"
tags: ["edit-size", "slider", "d62", "url-button", "hitl"]
dependency_graph:
  requires: ["03-13"]
  provides: ["edit-size-slider-ui", "slack-url-deep-link", "retired-edit-size-modal"]
  affects: ["src/gekko/dashboard/routes.py", "src/gekko/dashboard/templates/edit_size_modal.html.j2", "src/gekko/reporter/slack.py", "src/gekko/slack/interactivity.py", "src/gekko/approval/slack_handler.py"]
tech_stack:
  added: []
  patterns:
    - "Native HTML range slider with data-attribute driven JS live readout (CSP-safe external JS)"
    - "URL button replacing Bolt action button on Slack card (no callback)"
    - "TDD red-green cycle for new GET context keys and equity-failure variant"
key_files:
  created:
    - "src/gekko/dashboard/static/edit-size-slider.js"
  modified:
    - "src/gekko/dashboard/routes.py"
    - "src/gekko/dashboard/templates/edit_size_modal.html.j2"
    - "src/gekko/reporter/slack.py"
    - "src/gekko/slack/interactivity.py"
    - "src/gekko/approval/slack_handler.py"
    - ".planning/phases/03-production-hitl-ux-slack-block-kit-dashboard-fallback/03-UI-SPEC.md"
    - "tests/unit/test_dashboard_edit_size.py"
    - "tests/unit/test_slack_block_kit.py"
    - "tests/integration/test_p3_walking_skeleton.py"
decisions:
  - "D-62 dashboard slider replaces Slack number-input modal as edit-size surface"
  - "edit_size_get fetches equity (2.5s timeout) and max_position_pct to compute max_shares for slider"
  - "edit-size-slider.js is an external static file for CSP script-src self compliance"
  - "At-cap variant: max_shares == proposed_qty renders a note; equity-fail variant renders caution note"
  - "handle_edit_size and handle_edit_size_view_submission are no-op ack stubs (retained for backward compat)"
  - "Slack Edit size URL button points at /approvals/{id}/edit-size; no action_id"
metrics:
  duration: "35min"
  completed: "2026-06-22"
  tasks_completed: 3
  files_changed: 9
---

# Phase 03 Plan 14: Edit-Size Slider Redesign (D-62) Summary

**One-liner:** D-62 slider UX for edit-size — native range input with live dollar/% readout replacing Slack number-input modal; Slack Edit size becomes URL deep-link to dashboard.

## What Was Built

### Task 1: Dashboard slider — update edit_size_get route + replace number-input template

- **`edit_size_get` (routes.py):** New logic to fetch strategy `max_position_pct` and account equity (2.5s timeout, fail-open to 0). Computes `max_shares = floor(max_position_pct * equity / ref_price)`, clamped to `>= proposed_qty`. Passes four new context keys: `max_shares`, `account_equity_display`, `equity_fetch_failed`, `max_position_pct`.

- **`edit_size_modal.html.j2`:** Replaced `<input type="number">` with `<input type="range" name="qty" min="1" step="1" max="{{ max_shares }}" ...>`. Added `#size-readout` (aria-live="polite"). At-cap and equity-fetch-failure variants render inline notes. Submit path (`hx-post`) unchanged.

- **`edit-size-slider.js`:** New static JS file (`updateSizeReadout(el)`) — computes `qty * ref_price` notional and `%` of equity from data-attributes. Initialises readout on `DOMContentLoaded`. CSP-safe `script-src 'self'` via `<script src="/static/edit-size-slider.js">`.

- **All submit-path re-renders** updated to pass new context keys (for the cap-error modal re-render and LIVE-strategy-fail re-render).

- **`edit_size_submit` is UNCHANGED** — `_check_edit_size_caps` remains the sole server-side gate.

### Task 2: Slack URL deep-link + retire handle_edit_size modal handlers

- **`reporter/slack.py`:** `build_proposal_card` Edit Size element changed from `{action_id: "edit_size", value: ...}` to `{url: f"{dashboard}/approvals/{id}/edit-size"}`. No `action_id` (URL buttons don't round-trip to Bolt).

- **`slack/interactivity.py`:** `@slack_app.action("edit_size")` is now a no-op ack stub; `@slack_app.view("edit_size_modal")` is a no-op ack stub with deprecation warning. Both imports of `handle_edit_size` and `handle_edit_size_view_submission` removed.

- **`approval/slack_handler.py`:** `handle_edit_size` body replaced with ack + warning log (no `views_open`). `handle_edit_size_view_submission` body replaced with ack + warning log. Both functions retained in `__all__` for backward compat. `_edit_size_submit_workflow` private helper retained.

### Task 3: Update 03-UI-SPEC.md Surface 1 + full regression

- **`03-UI-SPEC.md` Surface 1** rewritten to describe the slider contract: range input, `max_shares` formula, live readout, at-cap and equity-failure variants, submit gate, ARIA semantics, and Slack URL deep-link. All `views_open`, `number_input`, and `callback_id` references removed from Surface 1.

- **`test_p3_walking_skeleton.py::test_p3_happy_path_with_edit_size`** updated: now asserts that `handle_edit_size` does NOT call `views_open` (retired stub) and `handle_edit_size_view_submission` is a no-op (retired stub). Proposal stays PENDING — no state mutation from retired stubs.

## Commits

| Hash | Message |
|------|---------|
| 13d6d53 | test(03-14): add failing tests for edit-size GET slider context keys |
| 760edd2 | feat(03-14): dashboard slider — range input, max_shares context, CSP-safe JS |
| f5c7ead | test(03-14): update edit_size block kit test for D-62 URL button assertion |
| 76b39ce | feat(03-14): Slack URL deep-link + retire handle_edit_size modal handlers |
| 0064840 | feat(03-14): update UI-SPEC Surface 1 to slider contract + update walking skeleton test |

## Test Results

- `tests/unit/test_dashboard_edit_size.py` — 5 passed (includes both regression guards and 2 new tests)
- `tests/unit/test_slack_block_kit.py` — tests pass
- `tests/unit/test_edit_size_caps.py` — tests pass
- `tests/unit/test_edit_size_not_direct_broker.py` — tests pass
- `tests/integration/test_dashboard_edit_size_happy.py` — passes
- `tests/integration/test_p3_walking_skeleton.py` — passes
- **Total: 42 tests pass**

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test_p3_happy_path_with_edit_size walking skeleton test**
- **Found during:** Task 3 verification run
- **Issue:** `test_p3_happy_path_with_edit_size` was asserting that `handle_edit_size` calls `views_open` (the old Slack modal behavior). After retiring the handler, the test failed.
- **Fix:** Updated the test to assert the new D-62 retired-stub behavior: no `views_open` call, no state mutation, proposal stays `PENDING`.
- **Files modified:** `tests/integration/test_p3_walking_skeleton.py`
- **Commit:** 0064840

**2. [Rule 1 - Bug] Added new slider context keys to all submit-path re-render calls**
- **Found during:** Task 1 implementation
- **Issue:** The `edit_size_submit` route has three error re-render paths (invalid qty, LIVE strategy load failure, cap exceeded) that all pass context to `edit_size_modal.html.j2`. The new template requires `max_shares`, `account_equity_display`, `equity_fetch_failed`, `max_position_pct`.
- **Fix:** Updated all three re-render context dicts with appropriate fallback values.
- **Files modified:** `src/gekko/dashboard/routes.py`
- **Commit:** 760edd2

## Verification

All plan acceptance criteria verified:

1. `src/gekko/dashboard/templates/edit_size_modal.html.j2` contains `type="range"` with `min="1"`, `step="1"`, `max="{{ max_shares }}"` — PASS
2. `edit_size_get` passes `max_shares`, `account_equity_display`, `equity_fetch_failed`, `max_position_pct` — PASS
3. `edit_size_submit` UNCHANGED — `_check_edit_size_caps` still the sole gate — PASS
4. `reporter/slack.py` has URL button with `url=".../edit-size"`, no `action_id="edit_size"` — PASS
5. `interactivity.py` both handlers are no-op ack stubs — PASS
6. `slack_handler.py` both functions are no-op ack stubs, no `views_open` — PASS
7. `03-UI-SPEC.md` Surface 1 describes slider contract, no `views_open`/`number_input` in Surface 1 — PASS
8. Static JS file exists at `src/gekko/dashboard/static/edit-size-slider.js` — PASS
9. Regression guards pass: `test_edit_above_hard_cap_rejected`, `test_happy_path_closes_modal`, `test_live_proposal_strategy_load_failure_rejected` — PASS
10. New tests pass: `test_edit_size_get_context_keys`, `test_edit_size_get_equity_fail_open` — PASS

## Known Stubs

None — all context keys are wired to real data sources (DB strategy row, broker equity fetch).

## Self-Check: PASSED

- `src/gekko/dashboard/static/edit-size-slider.js` exists
- `src/gekko/dashboard/templates/edit_size_modal.html.j2` contains `type="range"`
- All 5 task commits found in git log
- 42 tests pass in full regression
