---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
plan: 15
subsystem: dashboard-hitl
tags: [bug-fix, integration-test, hitl, dashboard, dedup, session-management]
depends_on: ["03-14"]
dependency_graph:
  requires: ["03-14"]
  provides: ["bug-free HITL action endpoints", "real-SQLite integration coverage"]
  affects: ["src/gekko/dashboard/routes.py", "templates", "tests/integration"]
tech_stack:
  added: []
  patterns:
    - "terminal-state guard before transition_status: check row.status in _TERMINAL_STATUSES"
    - "fresh _get_session_factory outside begin() block for duplicate re-read (Bug B pattern)"
    - "HX-Request header check for HTMX vs direct-nav branching (Bug A pattern)"
key_files:
  created:
    - tests/integration/test_dashboard_hitl_actions.py
  modified:
    - src/gekko/dashboard/routes.py
    - src/gekko/dashboard/templates/_proposal_card.html.j2
    - tests/integration/test_dashboard_edit_size_happy.py
decisions:
  - "Bug A: redirect (302 /approvals) chosen over inline full-page wrap — simpler, avoids new template, and the user lands on the styled dashboard where HTMX loads the modal via the normal path"
  - "_TERMINAL_STATUSES frozenset defined at module level in routes.py as the single source of truth for all three action endpoint guards"
  - "Test assertion uses 'Internal Server Error' not '500' substring to avoid false positives from '$500.00' dollar amounts in card content"
  - "Pre-existing cross-test pollution: test_p3_walking_skeleton::test_p3_happy_path_approve fails when run after test_dashboard_approve_flow etc. — confirmed pre-existing, not caused by this plan; each suite passes in isolation"
metrics:
  duration: "28min"
  completed: "2026-06-23T13:01:53Z"
  tasks: 3
  files: 4
---

# Phase 03 Plan 15: Dashboard HITL 3-Bug Cluster Fix + Real-SQLite Integration Tests

**One-liner:** Fixed Bug B (SQLAlchemy InvalidRequestError on duplicate edit-submit via rolled-back session), Bug C (ValueError 500 on terminal-state approve/reject/edit-submit), and Bug A (unstyled deeplink for non-HX edit-size GET), backed by 8 real-SQLite integration tests that run claim_action / transition_status / append_event without mocks.

## What Was Built

### Task 1 — Bug B + Bug C in routes.py (commit `6986663`)

**Bug C fix:** Added `_TERMINAL_STATUSES = frozenset({...})` near the top of `routes.py` (after imports). Applied status guard in all three action endpoints before calling `approve_proposal`, `reject_proposal`, or `transition_status`:
- `approve_proposal_endpoint`: check `row.status in _TERMINAL_STATUSES` before `approve_proposal`; if terminal, set `already_terminal = True`; after the begin block, re-read via fresh `sf2/engine2` and render current card
- `reject_proposal_endpoint`: same pattern for `reject_proposal`
- Both include a defensive `except ValueError` fallback that rolls back session and sets `already_terminal = True`

**Bug B fix:** In `edit_size_submit`, the `else:` (duplicate) branch inside `async with sf3() as session2, session2.begin()` previously executed `updated_row = (await session2.execute(...)).scalar_one_or_none()` on a session that `claim_action` had just rolled back, causing `SQLAlchemy InvalidRequestError`. Fix:
- Removed the re-read from inside the `else:` block (now just `pass`)
- After the `engine3.dispose()` finally block, added a fresh `sf4, engine4 = _get_session_factory(user_id)` re-read for both `outcome == "duplicate"` and `already_terminal` paths

### Task 2 — Bug A + terminal-card chips (commit `54c6176`)

**Bug A fix:** In `edit_size_get`, added `is_htmx = request.headers.get("HX-Request") == "true"` check before the final return. Non-HX direct-nav (e.g. Slack URL button deeplink) returns `RedirectResponse(url=f"/approvals?open_edit={proposal_id}", status_code=302)` so the user lands on the styled dashboard. HTMX swap path returns bare fragment unchanged. CSP safe — no new external script src added.

**`_proposal_card.html.j2`:** Action buttons were already inside `{% if status == "PENDING" %}` (pre-existing, correct). Added explicit read-only status chips for the remaining terminal statuses (APPROVED/APPROVED_LIVE/EXECUTING, FILLED, REJECTED, FAILED) so terminal cards render a status line rather than an empty area.

**Deviation:** Updated `test_dashboard_edit_size_happy.py` to send `HX-Request: true` header on the GET `/edit-size` call (the test was testing the HTMX fragment path but didn't include the header that the new Bug A guard now requires). This is a Rule 1 auto-fix — the test was incomplete for the new behavior.

### Task 3 — Real-SQLite integration suite (commit `64ee8f7`)

`tests/integration/test_dashboard_hitl_actions.py` — 8 `@pytest.mark.asyncio` tests:

| Test | What it covers | Result |
|------|----------------|--------|
| test_approve_first_write_happy | approve PENDING → 200, APPROVED in DB, dedup row | PASS |
| test_approve_duplicate_returns_200 | two approve calls → both 200, no error | PASS |
| test_approve_terminal_proposal_returns_200 | approve FILLED → 200, Bug C gate | PASS |
| test_edit_submit_first_write_happy | edit-submit PENDING → 200, APPROVED, dedup | PASS |
| test_edit_submit_duplicate_returns_200 | two edit-submit calls → both 200, Bug B gate | PASS |
| test_edit_submit_terminal_proposal_returns_200 | edit-submit FILLED → 200, Bug C gate | PASS |
| test_reject_first_write_happy | reject PENDING → 200, REJECTED in DB | PASS |
| test_edit_size_get_hx_vs_direct_nav | HX-Request:true → fragment; no header → 302, Bug A gate | PASS |

No mocks of `claim_action`, `transition_status`, or `append_event`. All three run against the real SQLCipher engine seeded via `temp_sqlcipher_db` fixture.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test_dashboard_edit_size_happy to send HX-Request header**
- **Found during:** Task 2 verification run
- **Issue:** The existing `test_dashboard_edit_size_happy` test GETs `/edit-size` without `HX-Request: true` header. Before Plan 03-15, the route returned 200 for all requests. After Bug A fix, non-HX requests get a 302 redirect, so the test got 302 instead of 200.
- **Fix:** Added `headers={"HX-Request": "true"}` to the GET call in the test. The test was always testing the HTMX fragment path; it just hadn't needed the header before.
- **Files modified:** `tests/integration/test_dashboard_edit_size_happy.py`
- **Commit:** `54c6176`

**2. [Rule 1 - Bug] Assertion used "Internal Server Error" not "500" substring**
- **Found during:** Task 3 first run
- **Issue:** Test assertions like `assert "500" not in resp.text` were false-positives because `$500.00` cost amounts appear in the card HTML.
- **Fix:** Changed to `assert "Internal Server Error" not in resp.text` which correctly identifies HTTP 500 error pages without false-positives.
- **Files modified:** `tests/integration/test_dashboard_hitl_actions.py`
- **Commit:** `64ee8f7`

**3. [Rule 1 - Bug] Strategy Pydantic model requires all fields for JSON**
- **Found during:** Task 3 first run (seed helper)
- **Issue:** The seed helper tried to use the `Strategy` Pydantic class constructor, but it requires `strategy_id`, `user_id`, `version`, `created_at` in addition to `name`, `thesis`, `watchlist`, `hard_caps`.
- **Fix:** Switched to building the strategy `payload_json` as a raw JSON dict with all required fields.
- **Files modified:** `tests/integration/test_dashboard_hitl_actions.py`
- **Commit:** `64ee8f7`

## Pre-Existing Issues (Deferred)

`test_p3_walking_skeleton::test_p3_happy_path_approve` fails when run after `test_dashboard_approve_flow + test_dashboard_edit_size_happy + test_dedup_race` in a single pytest process due to cross-test `get_settings()` cache contamination / vault state pollution. Confirmed pre-existing (present before any Plan 03-15 changes). Each suite passes in isolation. Logged to deferred items.

## Known Stubs

None. All three bugs have been fixed with real implementations.

## Threat Flags

None. The changes close existing STRIDE threats T-03-15-01 through T-03-15-04:
- T-03-15-01: terminal-state guard implemented (prevents invalid transitions → no state mutation)
- T-03-15-02: fresh-session re-read in duplicate path (no accidental write on rolled-back session)
- T-03-15-03: non-HX path redirects to authenticated dashboard (no new attack surface)
- T-03-15-04: 500 paths eliminated by Bug B + C fixes

## Self-Check

### Commits
- `6986663`: Bug B + Bug C in routes.py
- `54c6176`: Bug A deeplink + terminal-card chips + HX-Request header fix in test
- `64ee8f7`: Real-SQLite integration suite (8 tests)

### Key Files
- `/src/gekko/dashboard/routes.py` — _TERMINAL_STATUSES + Bug A/B/C fixes
- `/src/gekko/dashboard/templates/_proposal_card.html.j2` — terminal status chips
- `/tests/integration/test_dashboard_hitl_actions.py` — new 8-test suite
- `/tests/integration/test_dashboard_edit_size_happy.py` — HX-Request header fix
