---
phase: "03"
plan: "13"
subsystem: dashboard
tags: [htmx, polling, approvals, compact-card, ui-spec]
dependency_graph:
  requires:
    - 03-05-SUMMARY.md  # compact card CSS classes (.proposal-card-cost, .proposal-card-summary, .proposal-card-ticker)
    - 03-08-SUMMARY.md  # two-router auth pattern (router vs public_router)
  provides:
    - GET /approvals/poll  # HTMX polling partial for proposal list
    - _proposals_list.html.j2  # reusable proposals fragment
  affects:
    - src/gekko/dashboard/templates/approvals_index.html.j2
    - src/gekko/dashboard/routes.py
tech_stack:
  added: []
  patterns:
    - HTMX every-Ns polling with hx-trigger="every 30s" on a container div
    - Fragment partial template (_proposals_list.html.j2) extracted from full-page template for poll reuse
key_files:
  created:
    - src/gekko/dashboard/templates/_proposals_list.html.j2
  modified:
    - src/gekko/dashboard/routes.py
    - src/gekko/dashboard/templates/approvals_index.html.j2
    - .planning/phases/03-production-hitl-ux-slack-block-kit-dashboard-fallback/03-UI-SPEC.md
decisions:
  - "HTMX polling container wraps only the proposal list — modal-mount div is outside so edit-size modal is unaffected by 30s poll refreshes."
  - "_proposals_list.html.j2 is a fragment partial (does not extend base.html.j2) so it can be returned directly from /approvals/poll without a full page wrapper."
  - "Slack compact-card parity deferred — Slack Block Kit card redesign is lower priority; dashboard compact card is correct; deferred is documented in 03-UI-SPEC.md Surface 2."
metrics:
  duration: "6 minutes"
  completed: "2026-06-19"
  tasks_completed: 2
  files_changed: 4
---

# Phase 03 Plan 13: HTMX Polling + Compact Card Formalization Summary

**One-liner:** HTMX 30s polling on /approvals proposal list via new GET /approvals/poll fragment route; compact card (SIDE/QTY/TICKER/$cost/1-line summary + collapsed details) verified correct and formalized in 03-UI-SPEC.md.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add GET /approvals/poll route + HTMX polling on index | c630246 | routes.py, approvals_index.html.j2, _proposals_list.html.j2 (new) |
| 2 | Verify compact card + update 03-UI-SPEC.md Surface 2 | a110c49 | 03-UI-SPEC.md |

## What Was Built

### Task 1: HTMX Polling Infrastructure

**New partial template** `_proposals_list.html.j2`: Extracted the proposal loop + empty state from `approvals_index.html.j2` into a standalone fragment. Does not extend `base.html.j2` — pure HTML fragment for HTMX swap target.

**New route** `GET /approvals/poll`: Added to the authenticated `router` (inherits `require_session` automatically via router-level dependency). Duplicates the query logic from `approvals_index` (select PENDING/AWAITING_2ND_CHANNEL/EXPIRED proposals for user_id) and returns `_proposals_list.html.j2` as the fragment response.

**Updated `approvals_index.html.j2`**: Wrapped the proposal list in a container div with HTMX polling attributes:
```html
<div id="proposals-list-container"
     hx-get="/approvals/poll"
     hx-trigger="every 30s"
     hx-target="#proposals-list-container"
     hx-swap="innerHTML">
  {% include "_proposals_list.html.j2" %}
</div>
```
The `modal-mount` div remains OUTSIDE the polling container — edit-size modal is not cleared by poll refreshes.

### Task 2: Compact Card Verification + Spec Update

The compact card code was already correct from Plan 03-05 (live UAT rework):
- `_proposal_card.html.j2`: header shows `SIDE QTY TICKER` (via `proposal-card-ticker`) + `$cost` chip, `proposal-card-summary` div visible by default, full rationale/evidence inside `<details>` (collapsed).
- `_build_proposal_ctx` in `routes.py`: cost formatted as `f"${Decimal(str(cost_raw)):,.2f}"`.

Updated `03-UI-SPEC.md` Surface 2 to replace the old text-heavy card HTML example with the compact-card layout, including:
- Compact Card Contract prose note
- Updated HTML structure showing SIDE/QTY/TICKER + $cost + summary + collapsed details
- Context builder table documenting field sources and format
- Slack compact-card parity deferred note

## Deviations from Plan

None — plan executed exactly as written. Compact card was already correct in code; Task 2 was verification + spec update as planned.

## Security Posture

T-03-13-01 (Spoofing — unauthenticated /approvals/poll access): mitigated. Poll route is on `router = APIRouter(dependencies=[Depends(require_session)])`. Unauthenticated request returns 302 → /login. HTMX receives the redirect; the polling container shows the redirect response (harmless — the full-page auth redirect fires on next user interaction). Single-operator localhost deployment.

T-03-13-02 (Information Disclosure): accepted per plan. Poll response is session-scoped to `settings.gekko_user_id`.

T-03-13-03 (DoS — poll interval): accepted. 30s interval on single-user SQLite WAL; negligible load.

## Known Stubs

None.

## Threat Flags

None. No new network endpoints, auth paths, or schema changes beyond what was planned. The poll route reuses the existing approvals query logic with no new trust boundary crossings.

## Self-Check: PASSED

- FOUND: src/gekko/dashboard/templates/_proposals_list.html.j2
- FOUND: src/gekko/dashboard/templates/approvals_index.html.j2
- FOUND: src/gekko/dashboard/routes.py
- FOUND: .planning/phases/03-production-hitl-ux-slack-block-kit-dashboard-fallback/03-UI-SPEC.md
- FOUND: .planning/phases/03-production-hitl-ux-slack-block-kit-dashboard-fallback/03-13-SUMMARY.md
- Commit c630246 verified in git log
- Commit a110c49 verified in git log
