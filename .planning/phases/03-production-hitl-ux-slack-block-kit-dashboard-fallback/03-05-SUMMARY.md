---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
plan: "05"
subsystem: dashboard-auth-hitl
tags:
  - hitl
  - dashboard
  - auth
  - slack-modal
  - htmx
  - session
  - drift-check
dependency_graph:
  requires:
    - 03-01  # DB models with quiet_hours/timezone columns
    - 03-02  # claim_action dedup + Slack approve/reject handlers
  provides:
    - dashboard-login  # /login GET + POST with passphrase cookie auth
    - dashboard-approvals  # GET /approvals + POST approve/reject with HTMX
    - dashboard-edit-size  # GET/POST /approvals/{id}/edit-size modal
    - dashboard-settings  # GET/POST /settings quiet hours form
    - slack-edit-size-modal  # handle_edit_size opens Block Kit modal
    - d60-url-button  # Slack escalate replaced by URL button
  affects:
    - approval-flow
    - slack-interactivity
    - dashboard-routes
tech_stack:
  added:
    - itsdangerous>=2.1 (Starlette SessionMiddleware dependency)
  patterns:
    - SessionMiddleware with ephemeral per-restart secret (D-57/D-58)
    - require_session FastAPI dependency (302 redirect on unauthenticated)
    - HTMX hx-post + hx-target="closest article" + hx-swap="outerHTML" for card self-replacement
    - Slack views.open modal with private_metadata round-trip
    - response_action='errors' for Slack view_submission drift validation
    - _drift_check shared helper (both Slack and dashboard call same function)
    - D-56 cross-surface dedup: source='dashboard' for all dashboard actions
key_files:
  created:
    - src/gekko/approval/actions.py  # _drift_check shared helper
    - src/gekko/dashboard/templates/login.html.j2
    - src/gekko/dashboard/templates/approvals_index.html.j2
    - src/gekko/dashboard/templates/_proposal_card.html.j2
    - src/gekko/dashboard/templates/edit_size_modal.html.j2
    - src/gekko/dashboard/templates/settings.html.j2
    - tests/unit/test_dashboard_login.py
    - tests/unit/test_dashboard_middleware_order.py
    - tests/unit/test_dashboard_approvals.py
    - tests/unit/test_proposal_card_shared_partial.py
    - tests/unit/test_dashboard_edit_size.py
    - tests/unit/test_edit_size_not_direct_broker.py
    - tests/integration/test_dashboard_approve_flow.py
    - tests/integration/test_dashboard_edit_size_happy.py
  modified:
    - src/gekko/dashboard/app.py  # SessionMiddleware registration
    - src/gekko/dashboard/routes.py  # 9 new routes + require_session
    - src/gekko/dashboard/static/tailwind.css  # Phase 3 CSS additions
    - src/gekko/approval/slack_handler.py  # handle_edit_size + workflow + escalate no-op
    - src/gekko/slack/interactivity.py  # view_submission listener
    - src/gekko/reporter/slack.py  # D-60 URL button
    - src/gekko/logging_config.py  # _REDACT_KEYS session + gekko_session
    - src/gekko/vault/passphrase.py  # verify_passphrase
    - pyproject.toml  # itsdangerous>=2.1
    - tests/unit/test_approval_proposals.py  # updated edit_size_stub test
decisions:
  - "D-57/D-58: ephemeral per-restart session secret via os.urandom(32).hex(); HttpOnly + SameSite=Strict + max_age=8h"
  - "D-56: dashboard actions INSERT dedup row with source='dashboard' before state-machine transition"
  - "D-60: Slack escalate button converted to URL button pointing to /approvals/{proposal_id}; no action handler"
  - "D-54: handle_edit_size opens Block Kit modal; drift >2% returns response_action='errors'; pass spawns workflow task"
  - "_drift_check shared helper in actions.py: both Slack and dashboard paths call same function (D-27 Knight Capital defense)"
  - "Scoping decision: _approve_logic refactor deferred; handlers inline the post-dedup logic with comment annotations"
  - "test_approval_proposals.py: updated edit_size_stub test to verify modal open instead of deferred DM"
metrics:
  duration: "~5 hours (across 2 sessions)"
  completed_date: "2026-06-18"
  tasks_completed: 3
  tasks_total: 3
  files_created: 14
  files_modified: 10
---

# Phase 03 Plan 05: Dashboard Auth + HITL Approvals + Edit-Size Modal Summary

**One-liner:** Passphrase-cookie dashboard auth + HTMX approve/reject/edit-size approvals surface + Slack Block Kit edit-size modal with shared 2% drift guard, completing the D-54/D-55/D-56/D-57/D-58/D-60 requirements as a full vertical slice.

## What Was Built

### Task 1: SessionMiddleware + /login + require_session + AST gate

- `src/gekko/dashboard/app.py`: `add_middleware(SessionMiddleware)` with `os.urandom(32).hex()` ephemeral secret per D-57/D-58. Registered BEFORE the `@app.middleware("http")` block per Starlette reverse-order execution.
- `src/gekko/dashboard/routes.py`: `require_session` FastAPI dependency (raises 302 to `/login?next=...` on unauthenticated); `GET /login` renders `login.html.j2`; `POST /login` validates passphrase via `verify_passphrase()`, mints session cookie, 303-redirects to sanitized `next` (open-redirect defense: must start with `/`, no `://`).
- `src/gekko/dashboard/templates/login.html.j2`: per UI-SPEC §Surface 3 verbatim — hero + subtitle + conditional error block (`role="alert" aria-live="assertive"`) + form with `autocomplete="off"` + hidden `next` input.
- `src/gekko/logging_config.py`: extended `_REDACT_KEYS` with `"session"` and `"gekko_session"` per T-03-05-04/05.
- `src/gekko/vault/passphrase.py`: added `verify_passphrase()` function.
- `tests/unit/test_dashboard_middleware_order.py`: AST gate — asserts `add_middleware(SessionMiddleware)` appears before `@app.middleware("http")` in source order.
- `itsdangerous>=2.1` added to `pyproject.toml` (required by `starlette.middleware.sessions.SessionMiddleware`).

### Task 2: GET /approvals + shared _proposal_card partial + approve/reject + D-60

- `src/gekko/dashboard/routes.py`: `GET /approvals` (requires session, queries PENDING/AWAITING_2ND_CHANNEL/EXPIRED proposals); `POST /approvals/{id}/approve` (claim_action source="dashboard" + transition + execute_proposal dispatch); `POST /approvals/{id}/reject` (claim_action source="dashboard" + reject_proposal).
- `src/gekko/dashboard/templates/approvals_index.html.j2`: iterates proposals using `_proposal_card.html.j2` partial; empty state with `role="status"`.
- `src/gekko/dashboard/templates/_proposal_card.html.j2`: shared partial with HTMX `hx-post`/`hx-target="closest article"`/`hx-swap="outerHTML"`/`hx-disable-elt="this"` on Approve/Reject/Edit-size buttons; status chips (LIVE, EXPIRED, AWAITING 2ND CHANNEL); evidence `<details>` block.
- `src/gekko/reporter/slack.py`: replaced action escalate button with D-60 URL button pointing to `{dashboard_url}/approvals/{proposal_id}`.
- `src/gekko/approval/slack_handler.py`: `handle_escalate_stub` converted to no-op deprecation warning (URL buttons don't trigger action handlers).

### Task 3: handle_edit_size modal + view_submission + dashboard /edit-size + settings + drift check

- `src/gekko/approval/actions.py` (NEW): `_drift_check(qty, ref_price, target_notional_usd) -> Decimal` — single source of truth for 2% drift threshold. Both Slack and dashboard paths call this (D-27 Knight Capital defense via T-03-05-07).
- `src/gekko/approval/slack_handler.py`: replaced `handle_edit_size_stub` with full `handle_edit_size` (opens Block Kit modal via `views.open`); added `handle_edit_size_view_submission` (drift check + `response_action='errors'` or `ack()` + task); added `_edit_size_submit_workflow` (dedup source='slack' + edit_size audit event + qty update + APPROVED transition + executor dispatch).
- `src/gekko/slack/interactivity.py`: added `@slack_app.view("edit_size_modal")` listener; updated `_edit_size` action wrapper to call `handle_edit_size`.
- `src/gekko/dashboard/routes.py`: `GET /approvals/{id}/edit-size` (renders modal partial with server-derived ref_price); `POST /approvals/{id}/edit-submit` (drift check + dedup source='dashboard' + edit_size event + qty update + APPROVED + executor); `GET /settings` + `POST /settings` (quiet hours config per UI-SPEC §Surface 5, timezone validation via `zoneinfo.available_timezones()`).
- `src/gekko/dashboard/templates/edit_size_modal.html.j2`: HTMX modal partial with drift error block; form posts to `/approvals/{id}/edit-submit`.
- `src/gekko/dashboard/templates/settings.html.j2`: quiet hours form per UI-SPEC §Surface 5 verbatim.
- `tests/unit/test_approval_proposals.py`: updated `test_handle_edit_size_stub_acks_and_opens_modal` to verify `views_open` is called (not Phase 3 DM); updated `test_handle_escalate_stub_acks_and_is_noop` to verify no DM.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `ModuleNotFoundError: itsdangerous` in venv**
- **Found during:** Task 1
- **Issue:** `starlette.middleware.sessions.SessionMiddleware` requires `itsdangerous` which was installed in system Python but not in the venv.
- **Fix:** `uv add "itsdangerous>=2.1"` and added to `pyproject.toml`.
- **Files modified:** `pyproject.toml`
- **Commit:** 6a4a0d6

**2. [Rule 1 - Bug] `TradeProposal` validation — uppercase enum values in test**
- **Found during:** Task 2 (test_proposal_card_shared_partial_schema)
- **Issue:** Test used `side="BUY"`, `order_type="MARKET"` but `OrderSide`/`OrderType` enums require lowercase values.
- **Fix:** Changed to `side="buy"`, `order_type="market"` in `_make_test_proposal()`.
- **Files modified:** `tests/unit/test_proposal_card_shared_partial.py`
- **Commit:** be8ee5e

**3. [Rule 1 - Bug] `append_event` mock patch target wrong in integration tests**
- **Found during:** Task 2 (test_dashboard_approve_flow)
- **Issue:** Test patched `gekko.audit.log.append_event` but `proposals.py` imports it via `from gekko.audit.log import append_event` so the reference is `gekko.approval.proposals.append_event`. The mock didn't intercept the call, causing `TypeError: object supporting the buffer API required` in `hashlib.sha256()`.
- **Fix:** Changed patch target to `gekko.approval.proposals.append_event` in approve test; `gekko.audit.log.append_event` in edit-size tests (routes.py imports locally as `_ae`).
- **Files modified:** `tests/integration/test_dashboard_approve_flow.py`, `tests/unit/test_dashboard_edit_size.py`, `tests/integration/test_dashboard_edit_size_happy.py`

**4. [Rule 1 - Bug] `session.refresh()` not AsyncMock in integration test**
- **Found during:** Task 2 (test_dashboard_approve_flow)
- **Issue:** `mock_session.refresh` was a plain `MagicMock` — can't be awaited in the approve endpoint.
- **Fix:** Added `mock_session.refresh = AsyncMock()` to the mock setup.
- **Files modified:** `tests/integration/test_dashboard_approve_flow.py`

**5. [Rule 1 - Bug] `test_handle_edit_size_stub_acks_and_dms_deferred_message` stale after Plan 03-05**
- **Found during:** Task 3 broader test run
- **Issue:** Existing test in `test_approval_proposals.py` expected `handle_edit_size_stub` to send a "Phase 3 DM". After Plan 03-05 replaced the stub with the full modal handler (`handle_edit_size`), the test got a `KeyError` (missing `trigger_id` in body) and incorrect expectations.
- **Fix:** Rewrote test as `test_handle_edit_size_stub_acks_and_opens_modal` — verifies `ack()` first + `views_open` called with `callback_id="edit_size_modal"`. Updated escalate stub test similarly.
- **Files modified:** `tests/unit/test_approval_proposals.py`

### Scoping Decision (Plan-documented)

Per plan: `_approve_logic` refactor deferred. Dashboard handlers inline the post-dedup logic with comments citing shared semantics. The `_edit_size_logic` refactor scope was captured as a shared `_drift_check` helper in `actions.py` instead.

## Known Stubs

None — all routes are fully implemented with real DB logic (mocked in tests via session factory seam). The `/settings` form updates `User.quiet_hours_*` and `User.timezone` ORM fields.

## Threat Flags

All threats from the plan's STRIDE register were addressed:

| Threat | Status |
|--------|--------|
| T-03-05-01 Forged session cookie | Mitigated: itsdangerous-signed via SessionMiddleware |
| T-03-05-02 Open-redirect via `next` param | Mitigated: `next` must start with `/`, no `://` |
| T-03-05-03 Repudiation — dashboard approve | Mitigated: `actor_gekko_user_id` from session in dedup row + audit event |
| T-03-05-04/05 Passphrase/session in logs | Mitigated: `_REDACT_KEYS` extended with `passphrase`, `session`, `gekko_session` |
| T-03-05-07 edit-size bypasses OrderGuard | Mitigated: `_drift_check` + AST gate + executor dispatch path |
| T-03-05-08 private_metadata tampering | Mitigated: server re-fetches ref_price from payload_json at submit time |
| T-03-05-09 CSRF | Mitigated: SameSite=Strict + HTMX same-origin POST |

## Deferred Items

- `test_trigger_strategy_run_no_action_path` in `test_agent_runtime.py` — pre-existing failure (Plan 03-03 passphrase not set in that test's setup). Out of scope for Plan 03-05.
- `TemplateResponse(name, {"request": request})` deprecation warnings — routes.py has some older `TemplateResponse` call signatures that Starlette warns about. Functional, not critical. Can be addressed in a cleanup pass.
- CSRF tokens deferred to P6 per T-03-05-09 (single-operator deployment).

## Self-Check: PASSED

Files created:
- src/gekko/approval/actions.py: FOUND
- src/gekko/dashboard/templates/login.html.j2: FOUND
- src/gekko/dashboard/templates/approvals_index.html.j2: FOUND
- src/gekko/dashboard/templates/_proposal_card.html.j2: FOUND
- src/gekko/dashboard/templates/edit_size_modal.html.j2: FOUND
- src/gekko/dashboard/templates/settings.html.j2: FOUND

Commits:
- 6a4a0d6: feat(03-05): Task 1 — SessionMiddleware + /login + require_session + AST gate
- be8ee5e: feat(03-05): GET /approvals index + _proposal_card partial + approve/reject + D-60 URL button
- 51b921e: feat(03-05): handle_edit_size modal + view_submission + dashboard /edit-size + /settings + drift check

Test results: 16 plan-specific tests + 31 broader unit/integration tests — all PASS.
