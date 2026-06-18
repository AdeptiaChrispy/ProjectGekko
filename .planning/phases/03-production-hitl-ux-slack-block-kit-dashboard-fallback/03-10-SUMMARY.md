---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
plan: "10"
subsystem: slack-dedup-cleanup
tags: [hitl, idempotency, slack-bolt, socket-mode, dead-code-removal]
dependency_graph:
  requires:
    - claim_action-dedup-helper
    - approve-reject-dedup-wiring
  provides:
    - socket-mode-dedup-contract
    - retry-gate-removal
  affects:
    - src/gekko/approval/slack_handler.py
    - tests/unit/test_slack_retry_gate.py
    - tests/unit/test_slack_retry_header.py
tech_stack:
  added: []
  patterns:
    - claim_action UNIQUE-INSERT as sole exactly-once dedup primitive (Socket Mode)
    - dead-code removal (retry gate that read absent HTTP headers in WebSocket transport)
key_files:
  created:
    - tests/unit/test_slack_retry_gate.py
  modified:
    - src/gekko/approval/slack_handler.py
    - tests/unit/test_slack_retry_header.py
decisions:
  - "claim_action UNIQUE INSERT is the sole and sufficient exactly-once dedup primitive — HTTP-header retry gates are dead code in Socket Mode (WebSocket transport has no HTTP headers)"
  - "_extract_retry_num deleted entirely (not tombstoned) — no unexpected call sites found outside the two gate blocks that were also removed"
  - "test_slack_retry_header.py updated to remove the 2 tests asserting old gate short-circuit behavior; 1 backward-compat test retained confirming bodies with 'headers' key still work"
metrics:
  duration_minutes: 20
  completed: "2026-06-18"
  tasks_completed: 2
  files_modified: 3
  files_created: 1
---

# Phase 3 Plan 10: Retry Gate Removal (WR-08 Gap Closure) Summary

**One-liner:** Removed dead X-Slack-Retry-Num retry gate from handle_approve/handle_reject and deleted _extract_retry_num; claim_action UNIQUE INSERT is now documented as the sole and sufficient dedup primitive.

## Tasks Completed

| Task | Commit | Description |
|------|--------|-------------|
| 1 — Remove retry gate + delete _extract_retry_num | 56d81c1 | Delete _extract_retry_num function and the retry gate blocks from both handle_approve and handle_reject |
| 2 — Socket Mode dedup contract tests | e842952 | New test_slack_retry_gate.py (3 tests); updated test_slack_retry_header.py (remove 2 obsolete gate tests) |

## What Was Built

### `src/gekko/approval/slack_handler.py` (MODIFIED)

Three changes:

1. **Deleted `_extract_retry_num`**: The helper function that read `body["headers"]["x-slack-retry-num"]` was deleted. The "headers" key is absent in Socket Mode (WebSocket delivery), so the function always returned 0 in production. No unexpected call sites were found outside the two gate blocks.

2. **Removed retry gate block from `handle_approve`**: The 30-line block starting with `retry_num = _extract_retry_num(body)` and ending with the `finally: if _engine is not None: await _engine.dispose()` was deleted. `handle_approve` now goes directly from `await ack()` + extracting `decision_id`/`slack_user_id` to `asyncio.create_task(_approve_workflow(...))`.

3. **Removed retry gate block from `handle_reject`**: Same shape as `handle_approve`. Deleted the identical block. `handle_reject` now dispatches `_reject_workflow` immediately after `ack()`.

The `claim_action` UNIQUE INSERT in `_approve_workflow`/`_reject_workflow` is untouched and remains the sole dedup primitive. Module docstring and handler docstrings updated to document the Socket Mode dedup contract.

### `tests/unit/test_slack_retry_gate.py` (CREATED)

Three tests covering the Socket Mode dedup contract:

- `test_approve_double_click_dedup_via_claim_action`: Double-click approve with Socket Mode body (no "headers" key) — first call: claim_action returns "first_write"; second call: claim_action returns "duplicate". Exactly one first_write confirmed.
- `test_socket_mode_body_no_headers_key_does_not_raise`: handle_approve with body missing "headers" key does not raise AttributeError or KeyError.
- `test_reject_double_click_dedup_via_claim_action`: Double-click reject with Socket Mode body — same dedup behavior via claim_action.

### `tests/unit/test_slack_retry_header.py` (UPDATED)

Removed:
- `test_retry_header_suppresses_duplicate_claim` — asserted old gate short-circuit (gate deleted)
- `test_retry_header_no_prior_row_passes_through` — asserted old gate fall-through (gate deleted)

Retained/replaced:
- `test_body_with_headers_key_still_works` — backward compat: bodies with "headers" key still work; claim_action is still called

## Deviations from Plan

None — plan executed exactly as written.

## Threat Surface Scan

No new network endpoints, auth paths, or schema changes. This plan removes code and tests. The dedup contract (claim_action UNIQUE INSERT) is unchanged. No new threat flags.

## Self-Check

Files created/modified verified:
- `src/gekko/approval/slack_handler.py` — modified, _extract_retry_num deleted, retry gate blocks removed
- `tests/unit/test_slack_retry_gate.py` — created, 3 tests, all pass
- `tests/unit/test_slack_retry_header.py` — updated, 2 obsolete tests removed, 1 retained

Commits verified:
- 56d81c1 — Task 1 (retry gate removal)
- e842952 — Task 2 (test_slack_retry_gate.py + test_slack_retry_header.py update)

Acceptance criteria verified:
- `_extract_retry_num` not defined in slack_handler.py (AST walk confirmed)
- `retry_num` not present in handle_approve or handle_reject bodies (confirmed)
- `claim_action` still present 9 times in slack_handler.py (unchanged)
- All 4 tests in test_slack_retry_gate.py + test_slack_retry_header.py pass
- All 10 dedup-related tests pass (test_slack_action_dedup.py + both files above)

## Self-Check: PASSED
