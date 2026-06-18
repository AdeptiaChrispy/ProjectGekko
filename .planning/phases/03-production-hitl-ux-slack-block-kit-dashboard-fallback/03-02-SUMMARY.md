---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
plan: 02
subsystem: dedup-gate
tags: [hitl, idempotency, slack-bolt, sqlcipher, audit-chain]
dependency_graph:
  requires:
    - alembic-0004-migration
    - slack_action_dedup-orm
  provides:
    - claim_action-dedup-helper
    - approve-reject-dedup-wiring
    - x-slack-retry-num-gate
    - d43-ephemeral-response
  affects:
    - src/gekko/approval/dedup.py
    - src/gekko/approval/slack_handler.py
    - tests/unit/test_slack_action_dedup.py
    - tests/unit/test_slack_retry_header.py
    - tests/integration/test_dedup_race.py
tech_stack:
  added:
    - httpx (already in tree — used for _post_ephemeral response_url POST)
  patterns:
    - UNIQUE-INSERT idempotency with IntegrityError catch + rollback (PATTERNS §2b)
    - fresh-session audit event on duplicate path (PATTERNS §2d)
    - _get_session_factory module-local test seam (PATTERNS §2d)
    - X-Slack-Retry-Num gate at handle_approve/handle_reject entry
    - identity-split-safe ephemeral (Slack user id only in message text)
key_files:
  created:
    - src/gekko/approval/dedup.py
    - tests/unit/test_slack_action_dedup.py
    - tests/unit/test_slack_retry_header.py
    - tests/integration/test_dedup_race.py
  modified:
    - src/gekko/approval/slack_handler.py
decisions:
  - "claim_action uses try/except IntegrityError + await session.rollback() (MANDATORY — IntegrityError aborts the SQLAlchemy transaction context)"
  - "dedup_click audit event written in a FRESH session after rollback so the event is not lost (D-45 contract)"
  - "duplicate branch in _approve_workflow/_reject_workflow opens a new read session to query original dedup row for ephemeral copy (session was rolled back by claim_action)"
  - "X-Slack-Retry-Num gate fires BEFORE claim_action at handle_approve/handle_reject entry — avoids spurious dedup rows on retry storms"
  - "Retry gate falls through (treats as first delivery) when no prior dedup row exists — defensive correctness over short-circuit"
  - "cross-actor test (D-42) uses different action_ids (approve vs reject) to avoid uq_dedup_dashboard UNIQUE conflict — in single-operator model two different Slack users with same action on same proposal WOULD conflict on dashboard UNIQUE"
  - "race test asserts invariants (one terminal status, two dedup rows, <=1 place_order, intact chain) not the specific winner — winner is nondeterministic in asyncio.gather"
metrics:
  duration_minutes: 75
  completed: "2026-06-18"
  tasks_completed: 3
  files_modified: 5
  files_created: 4
---

# Phase 3 Plan 2: Dedup Gate Summary

**One-liner:** claim_action() UNIQUE-INSERT dedup helper wired into approve/reject workflows with D-43 ephemeral response, X-Slack-Retry-Num gating, and race test proving exactly-once execution.

## Tasks Completed

| Task | Commit | Description |
|------|--------|-------------|
| 1 — claim_action helper + unit tests | a7e4cdd | dedup.py with 6 unit test cases (first_write, duplicate, cross-actor D-42, cross-surface D-56, trigger_id, audit event) |
| 2 — Wire into approve/reject + retry gate | e219523 | slack_handler.py extended; 3 retry-header unit tests |
| 3 — Integration race cassette | bb9ef6d | approve+reject race producing exactly one state transition |

## What Was Built

### `src/gekko/approval/dedup.py` (NEW)

`claim_action(session, *, proposal_id, action_id, actor_slack_user_id, actor_gekko_user_id, source, slack_trigger_id=None) -> Literal["first_write", "duplicate"]`

The load-bearing UNIQUE-INSERT idempotency gate:

- Inserts a `SlackActionDedup` row with `result="first_write"` + `await session.flush()`
- On `IntegrityError`: `await session.rollback()` (MANDATORY — IntegrityError aborts the transaction context); opens a fresh session via `_get_session_factory`; writes a `dedup_click` audit event with `normalize_decimals()` (Pitfall 6 invariant); returns `"duplicate"`
- On success: returns `"first_write"` — caller proceeds with state-machine transition
- Module-local `_get_session_factory` test seam (PATTERNS §2d)
- No `claude_agent_sdk` or `anthropic` imports (deterministic Python firewall)

### `src/gekko/approval/slack_handler.py` (EXTENDED)

Two-layer dedup defense added to `handle_approve` + `handle_reject`:

**Layer 1 — X-Slack-Retry-Num gate** (at `handle_approve`/`handle_reject` entry, BEFORE `asyncio.create_task`):
- Extracts `x-slack-retry-num` from `body["headers"]`
- When `retry_num > 0` AND a `SlackActionDedup` row already exists for `(proposal_id, action_id, actor_slack_user_id)`: short-circuit (ack only, no workflow dispatch)
- When `retry_num > 0` but NO prior row: falls through to normal workflow (defensive — treats as first delivery)

**Layer 2 — claim_action gate** (at `_approve_workflow`/`_reject_workflow` entry, inside `session.begin()` AFTER cross-user check):
- Calls `claim_action(session, action_id="approve_proposal"|"reject_proposal", ...)` immediately
- On `"duplicate"`: opens a fresh read session to get original dedup row (original actor + inserted_at); POSTs D-43 ephemeral via `_post_ephemeral(response_url, text)`; returns WITHOUT touching state
- On `"first_write"`: falls through to existing AWAITING_2ND_CHANNEL divert / standard approve/reject flow (VERBATIM PRESERVED)

New helpers added:
- `_post_ephemeral(response_url, text)` — httpx.AsyncClient POST + structlog warning on >=400 (non-critical)
- `_format_hhmm(iso_ts)` — extracts HH:MM from ISO timestamp for ephemeral copy
- `_extract_retry_num(body)` — reads x-slack-retry-num header safely

Identity-split invariant: ephemeral text uses `<@{orig_slack_user_id}>` pulled from the original dedup row's `actor_slack_user_id` — never `gekko_user_id`.

### Tests

**`tests/unit/test_slack_action_dedup.py`** (6 cases):
- `test_first_click_first_write` — first INSERT returns "first_write", row visible
- `test_second_click_duplicate` — second identical INSERT returns "duplicate" without raising
- `test_dedup_click_event_appended` — exactly one `dedup_click` event in audit log after dup
- `test_cross_actor_both_first_write` — D-42: different action_ids for different Slack actors
- `test_cross_surface_both_first_write` — D-56: slack vs dashboard source both get first_write
- `test_trigger_id_persisted_and_masked` — trigger_id persisted, excluded from __repr__

**`tests/unit/test_slack_retry_header.py`** (3 cases):
- retry_num=0 → claim_action called
- retry_num=1 + dedup row exists → claim_action NOT called (short-circuit)
- retry_num=1 + no prior row → claim_action called (defensive first delivery)

**`tests/integration/test_dedup_race.py`** (1 case with flaky(reruns=2)):
- Concurrent `asyncio.gather(handle_approve, handle_reject)` on the same proposal
- Asserts: exactly one terminal state, two dedup rows, ≤1 place_order call, walk_chain=[], ≤1 ephemeral

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Post-rollback session state in duplicate branch**
- **Found during:** Task 2 implementation
- **Issue:** The plan's behavior spec said to query `orig_row` and `proposal_row` in the SAME session after `claim_action` returns `"duplicate"`. But `claim_action` calls `await session.rollback()` on IntegrityError — the session is in a rolled-back/aborted state. Subsequent queries on the same session would fail.
- **Fix:** The duplicate branch in `_approve_workflow`/`_reject_workflow` opens a FRESH `async with sf() as read_session:` context to query the original dedup row and proposal status, instead of reusing the rolled-back session.
- **Files modified:** `src/gekko/approval/slack_handler.py`
- **Commit:** e219523

**2. [Rule 1 - Bug] test_cross_actor_both_first_write needed different action_ids**
- **Found during:** Task 1 test run (test failed in RED as expected, then implementation revealed why)
- **Issue:** The test used the SAME `action_id="approve_proposal"` for two different Slack actors. The `uq_dedup_dashboard` UNIQUE constraint on `(proposal_id, action_id, actor_gekko_user_id, source)` fires when both actors share the same `actor_gekko_user_id` and `source="slack"` — as they do in the single-operator model.
- **Fix:** Changed test (d) to use `action_id="approve_proposal"` for actor 1 and `action_id="reject_proposal"` for actor 2, matching the actual D-42 scenario ("User A approves; a different Slack user fires Reject").
- **Files modified:** `tests/unit/test_slack_action_dedup.py`
- **Commit:** a7e4cdd (fixed before GREEN commit)

## Threat Surface Scan

No new network endpoints or auth paths introduced. `_post_ephemeral` makes an outbound POST to Slack's `response_url` (a Slack-provided URL, TTL ~30min, non-sensitive). The dedup table grows linearly with legitimate actions — T-03-02-05 (DoS via retry storm) remains `accept` per the threat model (signing secret + cross-user check + UNIQUE constraint gates already in place). No new threat flags.

## Self-Check

Files created/modified verified:
- `src/gekko/approval/dedup.py` — exists, contains `async def claim_action`
- `src/gekko/approval/slack_handler.py` — exists, contains `claim_action`, `_post_ephemeral`, `x-slack-retry-num`
- `tests/unit/test_slack_action_dedup.py` — 6 tests, all pass
- `tests/unit/test_slack_retry_header.py` — 3 tests, all pass
- `tests/integration/test_dedup_race.py` — 1 test, passes

Commits verified:
- a7e4cdd — Task 1
- e219523 — Task 2
- bb9ef6d — Task 3

Acceptance criteria verified:
- `grep -n "async def claim_action" src/gekko/approval/dedup.py` → line 76 (one match)
- No `claude_agent_sdk`/`anthropic` imports in dedup.py (only in docstring comments)
- `grep -n 'event_type="dedup_click"'` → line 174 (one match)
- `grep -n "claim_action" src/gekko/approval/slack_handler.py` → 4 matches (import + 2 call sites + comments)
- `grep -nE 'action_id="(approve|reject)_proposal"'` → 2 matches
- `grep -n "response_url" src/gekko/approval/slack_handler.py` → 6 matches
- `grep -nE "(x-slack-retry-num|X-Slack-Retry-Num)"` → 2 matches
- AWAITING_2ND_CHANNEL preserved: 1 match (unchanged from pre-task)
- Phase-1 walking-skeleton test: PASSES

## Self-Check: PASSED
