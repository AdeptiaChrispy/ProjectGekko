---
status: partial
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
source: [03-VERIFICATION.md]
started: 2026-06-18T14:00:00Z
updated: 2026-06-23T13:30:00Z
---

## Current Test

number: 2
name: Edit-size slider — live browser test (D-62 / Plan 03-14)
expected: |
  On /approvals, click "Edit size" on a pending proposal. A modal opens with a draggable
  SLIDER. Slack "Edit size" deep-links to the same dashboard page.
result: resolved_in_code   # live 2026-06-22 — real bug found+fixed after clean restart; awaiting browser retest
reported: "Dashboard Edit Size does nothing; Slack Edit Size → Internal Server Error."
severity: blocker
diagnosis_2026_06_22: |
  CORRECTION: my first-pass "stale server" hypothesis was WRONG. The clean restart loaded 03-14
  and produced the real traceback:
    routes.py:531 edit_size_get → AttributeError: 'Proposal' object has no attribute 'ticker'
  Root cause: `ticker = payload.get("ticker", row.ticker or "")`. The Proposal ORM model has NO
  ticker/side/qty columns (they live in payload_json). Python evaluates the default `row.ticker or ""`
  BEFORE .get() runs, so every GET /approvals/{id}/edit-size raised → 500. That 500 is exactly what
  the Slack URL button surfaced ("Internal Server Error") and what made the dashboard hx-get silently
  no-op. Both symptoms = one bug.
  Why tests missed it: the GET-route tests mocked the proposal row with a plain MagicMock, which
  auto-creates `.ticker` and masked the AttributeError.
  FIX (commit 09d8f48): edit_size_get now reads `payload.get("ticker", "")` (mirrors edit_size_submit).
  Added regression test test_edit_size_get_uses_payload_not_orm_ticker using MagicMock(spec=Proposal)
  so a non-column access raises like production. Full edit-size suite green (6 passed).
  RETEST: Python isn't hot-reloaded — RESTART `uv run gekko serve` once more to load 09d8f48, then
  re-open Edit Size on a fresh proposal.
second_bug_2026_06_22: |
  After the 09d8f48 fix the slider rendered and submitted, but resizing NVDA 2→5 shares was then
  [REJECTED BY ORDERGUARD] qty_price_drift: "qty × ref_price (5 × 210.05 = 1050.25) drifts 150% from
  target_notional_usd 420.1; max allowed 2%". Root cause: edit_size_submit updated qty but NOT
  target_notional_usd, so OrderGuard's D-27 check_qty_price_sanity (qty×price vs declared notional)
  rejected every deliberate resize. The edit-size hard-cap check passed; this is a SECOND, deeper layer.
  FIX (commit ad57988): edit_size_submit now rewrites target_notional_usd = new_qty × ref_price
  alongside qty. Safety unchanged — the absolute bound stays the hard cap (max_position_pct×equity via
  the slider max + _check_edit_size_caps + OrderGuard check_hard_caps); only the qty↔declared-notional
  consistency guard is kept in sync so the operator's deliberate size isn't treated as an agent typo.
  Regression test added (test_edit_updates_target_notional_to_match_new_qty). Edit-size + OrderGuard
  suites green (46 passed).
  RETEST: RESTART `uv run gekko serve` again to load ad57988, then resize + Approve at this size →
  order should pass OrderGuard and fill (paper), proposal → FILLED.
third_round_2026_06_22: |
  After restart the slider RENDERS and a resize passed OrderGuard (target_notional fix works) — a
  proposal reached FILLED. But three more real bugs surfaced (cluster in the dashboard HITL action
  flow; all confirmed live, all diagnosed to specific lines):

  BUG B (headline crash) — edit_size_submit re-reads on a rolled-back session.
    claim_action() calls session.rollback() on the DUPLICATE path (repeat Edit-Size clicks on the same
    proposal across restarts). approve/reject endpoints handle this by re-reading with a FRESH session
    OUTSIDE the begin() block (their comment: "session was rolled back by claim_action"). edit_size_submit's
    duplicate branch instead re-reads on the same rolled-back session2 INSIDE `async with session2.begin()`
    → sqlalchemy InvalidRequestError "Can't operate on closed transaction" → 500. routes.py edit_size_submit.

  BUG A — Slack "Edit size" deep-link page is unstyled.
    The URL button opens /approvals/{id}/edit-size which returns the bare edit_size_modal.html.j2 FRAGMENT
    (no base.html.j2, no CSS/HTMX). Only styled when HTMX-swapped into /approvals. Direct browser nav
    (from Slack) gets the naked partial. Fix: when the request is NOT an HX request (no HX-Request header),
    render the modal wrapped in the base layout (or redirect to /approvals with an auto-open hint).

  BUG C — terminal-state proposals 500 on action.
    Approving/editing an already-FILLED (or EXPIRED/REJECTED) proposal raises ValueError "Invalid proposal
    status transition: 'FILLED' -> 'APPROVED'" → 500, instead of cleanly re-rendering the current card as
    "already handled". Affects approve_proposal_endpoint AND edit_size_submit. The card likely also still
    shows action buttons on terminal proposals, inviting the click.

  ROOT THEME: these dashboard action endpoints have NO real-session integration test — unit tests mock
  claim_action/append_event/transition_status, masking exactly these seams. Fix set MUST include a real
  (non-mocked, real SQLite) integration test covering: first_write happy path, duplicate re-click, and
  terminal-state action — for both approve and edit. Recommend a tested gap-closure plan (03-15) rather
  than another inline hand-patch.
resolved_2026_06_23: |
  All three bugs CLOSED by tested gap-closure Plan 03-15 (the decision taken: tested plan, not
  inline patch). Verified in source by gsd-verifier (03-VERIFICATION.md, 6/6 must-haves) and by
  the orchestrator with real test runs:
    BUG B — edit_size_submit duplicate branch now re-reads on a FRESH _get_session_factory session
            outside the rolled-back session2.begin() block (routes.py ~1034-1055). fix 6986663.
    BUG C — _TERMINAL_STATUSES guard precedes the PENDING→APPROVED transition in approve/reject/
            edit-submit; terminal cards render read-only status chips, no action buttons
            (routes.py + _proposal_card.html.j2). fix 6986663 + 54c6176.
    BUG A — edit_size_get branches on HX-Request; non-HX Slack deep-link → 302 redirect to
            /approvals?open_edit={id} (styled page); HTMX swap keeps the bare fragment. fix 54c6176.
  Root-cause test gap closed: tests/integration/test_dashboard_hitl_actions.py — 8 real-SQLite
  tests (approve+edit × first-write/duplicate/terminal + Bug A fragment-vs-page), zero mocks on
  claim_action/transition_status/append_event. 8/8 pass (commit 64ee8f7). No regressions:
  the kill-modal SessionMiddleware failure + walking-skeleton cross-test pollution were confirmed
  IDENTICAL at the pre-03-15 baseline (41f9821).
awaiting: browser retest — RESTART `uv run gekko serve` (Python not hot-reloaded), then on /approvals:
  (1) click Edit Size twice on the same proposal → second click returns the card at 200, no 500;
  (2) open the Slack "Edit size" deep-link in a browser → lands on the styled /approvals page;
  (3) act on an already-FILLED/EXPIRED proposal → card re-renders "already handled", no 500.
  Then run `/gsd-verify-work 3` to record results and close Phase 3.

## Tests

### 1. Slack Block Kit card rendering and button layout
expected: Proposal card appears with approve / reject / edit-size / escalate-to-dashboard buttons; card is visually distinct for paper vs. live (paper chip vs. live chip).
result: pass

### 2. Edit-size modal interaction (cap-based redesign, Plan 03-11)
expected: |
  Plain-language modal headline ("Edit order size — BUY 47 AAPL (~$9,400.00)"). A meaningful
  resize within the strategy's hard caps (e.g. 47 → 50) is ACCEPTED — modal closes, card → APPROVED,
  executor fires. A size above the hard cap shows a plain-language bound ("That's above your max of
  $Z (~W shares) — pick a smaller number"), NOT the old 2%-drift "outside the range" error.
result: issue   # re-verify after 03-11 — cap math correct but still not legible
reported: "still not very clear to the end user, but maybe that means the safety net is too low"
severity: major
finding_2026_06_22: |
  Re-test after the 03-11 cap redesign. The cap math is correct, but the modal is still not
  digestible for a non-technical operator. Operator selected all three: (1) wording/layout still
  confusing, (2) cap rejects too easily, (3) allowed range not shown up front. Core insight:

  UNIT-MODEL MISMATCH. When a user sees a proposed trade they think of it as "quantity 1" and
  naturally want to increment by whole shares (1 → 2). But because max_position_pct is low (the
  ai-infra-bull test strategy), the allowable band is tiny — e.g. 1 → ~1.02 shares — which no
  normal user would reason about. The modal forces the operator to infer a fractional ceiling
  they can't see. Result: a "valid" resize feels impossible and the rejection feels arbitrary.

  Implications for the fix (design decision needed before planning):
  - Show the allowed bound UP FRONT (max shares ~ $max) before the user types, not only on reject.
  - Reconcile whole-share intuition with a small position cap — e.g. surface the cap as a share
    count + dollar ceiling, offer quick-pick sizes (Max / half), and/or clamp+explain rather than
    reject. Consider whether the test strategy's max_position_pct is unrealistically low for a
    meaningful demo (calibration), separate from the UI.
  - Live "New: N shares (~$total)" readout as the count changes.
  This is a UI-contract + possibly cap-calibration change → route to gap-closure planning.
  Touches BOTH the Slack modal and the D-55 dashboard edit-size mirror (keep parity).
prior_result: issue   # 2026-06-19 (old 2%-drift contract — now replaced)
prior_finding: |
  Design contradiction in D-54: the modal's stated use case is "I want 50 shares not 47"
  (~6% change) but D-54 step 2 validates qty x ref_price within 2% of the ORIGINAL
  target_notional_usd, rejecting any change >2%. Implementation (handle_edit_size_view_submission
  -> _drift_check vs target_notional) faithfully follows the rule, but the rule makes edit-size
  unusable for resizing. Modal also never shows the allowed qty range; "New quantity"/"Edit size"
  implies more freedom than 2% allows.

  OPERATOR DECISION (2026-06-19): the core problem is UI legibility for a non-technical /
  non-finance user, not the threshold math. Required redesign of edit-size (Slack modal + the
  D-55 dashboard mirror):
  - State the action in plain language: "Buy N shares of TICKER (~$total)" — shares + dollar total.
  - Let the user adjust the share count easily and intuitively, with LIVE "New: N shares ~ $total" feedback.
  - Allow real resizing (current +/-2% vs agent notional blocks even 2->3). Validate the edited size
    against the strategy's OrderGuard HARD CAPS (max position / order size) instead of the 2% target-
    notional consistency check (that check is for the agent's output, not the operator's deliberate edit).
    This preserves the true Knight-Capital defense (absolute risk bounds) while enabling legible resizing.
  - Replace "outside the range" with plain-language bounds: e.g. "That's above your max of $Z (~W shares) —
    pick a smaller number."
  This is a UI-contract change (update 03-UI-SPEC.md + D-54) → handle via gap-closure planning.

### 3. Quiet-hours queuing behavior over time
expected: No Slack DM arrives during the quiet window; DM arrives when the window opens. Safety-critical categories (kill, executor errors, first-live fills, proposal expiry) still fire during quiet hours.
result: skipped
reason: "Time-gated — requires a real multi-hour quiet window. Deferred; verify naturally during overnight running. Unit/integration coverage exists (test_quiet_hours_*, test_scheduler_quiet_hours)."

### 4. Dashboard fallback end-to-end (Slack unavailable) — after 03-12 + 03-13
expected: |
  Operator logs in (passphrase) and uses /approvals. (a) Cards are compact: SIDE QTY TICKER +
  $cost + 1-line summary, with rationale/evidence collapsed under a details toggle. (b) New
  proposals appear WITHOUT a manual reload (page polls every 30s). (c) Approving a paper proposal
  places the order and records a fill — proposal reaches FILLED (no "broker not configured"),
  identical to the Slack path. (d) Unauthenticated access to /live-confirm, /kill, /unkill,
  /promote-to-live, /trigger still redirects to /login.
result: pass   # 2026-06-22 — all four criteria (a–d) confirmed live
passed_note: |
  Operator confirmed: compact card looks better; live refresh, paper approve→fill, and the
  auth gate all work. Two ENHANCEMENTS raised (logged separately as new-scope items, not failures
  of this test):
    E1: segment proposals by state — expired trades in their own section, separate from pending
        and complete; possibly break up by tabs (Pending / Completed / Expired).
    E2: enhance the site-wide toolbar/nav to make moving between dashboard pages easier.
prior_result: issue   # 2026-06-19
prior_finding: |
  Three sub-findings under DASH-04 dashboard fallback:
  (a) FIXED this session: /approvals rendered blank cards — the index didn't unpack the
      proposal dict into the partial's flat vars. Cards now render + are actionable.
  (b) OPEN: paper order PLACEMENT fails on approve — executor logs
      'BrokerOrderError: broker not configured; falling back to yahooquery' and the proposal
      goes FAILED (14ef...). Trading STREAM connects (paper) but the order-placement client
      appears unconfigured. Needs triage: config vs code wiring gap. Blocks the 'executes
      identically' half of SC-5.
  (c) UX gap: /approvals is not live — new proposals don't appear until manual reload (no
      polling/SSE). Compounded by the 30-min expiry, the operator can miss/lose proposals.
  Plus compact-card redesign (logged separately) — too text-heavy; reworked to trade+cost+summary.

### 5. Daily P&L digest at 16:30 ET on a NYSE trading day
expected: Block Kit digest with correct gross P&L (BUYs subtract, SELLs add), per-strategy breakdown by strategy name — no `_unknown_` buckets, no sign-flipped SELLs.
result: skipped
reason: "Time-gated — fires at 16:30 ET on a trading day. Deferred; verify at a real market close. CR-02 fix (strategy_name+side in fill payload) is unit-tested (test_daily_pnl_aggregation, test_fill_payload_fields)."

## Summary

total: 5
passed: 2
issues: 0
pending: 0
skipped: 2
blocked: 1   # Test 2 edit-size slider — stale server; retest after clean restart (not a code gap)
enhancements: 2   # dashboard state-tabs + site nav toolbar (new scope, minor → Phase 6)

## Gaps

# NOTE: Bugs marked [RESOLVED-IN-SESSION] were fixed live during this UAT (committed).
# The 4 OPEN gaps were closed by gap-closure plans 03-11/03-12/03-13 (verified static,
#   2026-06-19) and are now status: resolved. Live/time-gated behaviors (edit-size in a
#   real Slack modal, dashboard approve→fill during market hours, quiet-hours timing,
#   daily P&L digest) remain human-verify items — confirm via /gsd-verify-work 3.

- truth: "Operator can run the app against an existing database (migrations apply cleanly to a DB that already holds rows)"
  status: resolved   # [RESOLVED-IN-SESSION] via /gsd-debug → migrations/env.py FK-toggle + regression test
  reason: "0002/0004 batch_alter_table recreated FK-referenced parents with FK enforcement ON → DROP TABLE refused. Fixed: env.py disables PRAGMA foreign_keys on the raw connection outside the transaction. Live DB migrated to 0004 successfully."
  severity: blocker
  test: prerequisite

- truth: "HITL proposal card is delivered to the operator's Slack DM"
  status: resolved   # [RESOLVED-IN-SESSION]
  reason: "post_run_result posted channel=gekko_user_id ('chris') → channel_not_found. Fixed to settings.slack_user_id (+ regression test). Card now delivers."
  severity: blocker
  test: 1

- truth: "Operator can edit the order size from an understandable UI and approve the resized order"
  status: resolved_in_code   # closed by Plan 03-14 (D-62 slider); live drag = human-verify item
  severity: major
  test: 2
  reason_resolved: "Plan 03-14 (D-62): dashboard edit-size is now a native range SLIDER (1 → cap-derived max shares, handle at proposed qty) with a live readout 'N shares ≈ $X — Y% of your $Z equity' and at-cap/equity-fail variants. The bound is shown UP FRONT (slider max + readout). Slack 'Edit size' deep-links to the dashboard slider; the in-Slack edit modal is retired. Server gate _check_edit_size_caps unchanged (slider max is display-only). Code-review CSP fix applied: readout bound via delegated listener + htmx:afterSettle in edit-size-slider.js (inline oninput would be blocked by script-src 'self'). Cap calibration intentionally NOT changed (slider provides the visual legibility; user-editable cap deferred to Phase 6). Verified 7/7 static (03-VERIFICATION.md). NOTE: live browser drag→readout and approve→fill remain human-verify items."
  reason: "User reported: 'still not very clear to the end user, but maybe that means the safety net is too low.' Cap-based validation (03-11) works, but the modal is not legible for a non-technical operator. Root: UNIT-MODEL MISMATCH — users think in whole shares (1→2) but a low max_position_pct only permits a tiny fractional band (e.g. 1→~1.02 on the ai-infra-bull test strategy) that is never shown up front, so valid resizing feels impossible and rejections feel arbitrary."
  history: "03-11 closed the threshold-math gap (drift → hard caps); 03-14 (D-62 slider) closed the legibility layer on top of that."
  artifacts:
    - "src/gekko/approval/slack_handler.py — handle_edit_size (modal blocks) + handle_edit_size_view_submission"
    - "src/gekko/dashboard/routes.py + edit_size_modal.html.j2 — D-55 dashboard mirror (keep parity)"
    - "src/gekko/approval/actions.py — _check_edit_size_caps (cap source for the displayed bound)"
    - "03-UI-SPEC.md Surface 1 + D-54 — UI contract"
    - "strategy max_position_pct default / ai-infra-bull demo strategy — possible calibration"
  missing:
    - "Show the allowed bound UP FRONT before the operator types: 'max ~W shares (~$Z)' on both Slack modal and dashboard"
    - "Reconcile whole-share intuition with a small position cap — quick-pick sizes (e.g. Max / half), or clamp-and-explain instead of reject"
    - "Live 'New: N shares (~$total)' readout as the share count changes"
    - "Decide whether the test strategy's max_position_pct is unrealistically low for a meaningful demo (calibration, separate from UI)"
    - "Re-run live UAT Test 2 after the redesign"
  reason: "Edit-size modal rejects any real resize (2% drift vs agent notional blocks even 2->3) and shows a cryptic 'outside the range'. See Test 2 finding for the full operator-approved redesign (plain-language shares+$total, easy increment + live feedback, validate against OrderGuard hard caps not 2% notional, plain-language bounds). UI-contract change: update 03-UI-SPEC.md + D-54."
  severity: major
  test: 2
  artifacts:
    - "src/gekko/approval/slack_handler.py — handle_edit_size (modal) + handle_edit_size_view_submission (_drift_check vs target_notional)"
    - "src/gekko/approval/actions.py — _drift_check"
    - "src/gekko/dashboard/ — D-55 dashboard edit-size mirror must match"
  missing:
    - "Redesign edit-size modal for legibility (Slack + dashboard): plain-language framing, share stepper w/ live $total"
    - "Validate edited qty against strategy OrderGuard hard caps (max position/order size), not 2% target-notional drift"
    - "Plain-language rejection messages with the actual allowed bound"
    - "Update 03-UI-SPEC.md + D-54 to reflect the new contract"

- truth: "Dashboard /approvals card is scannable (trade + cost + short summary), not a wall of text"
  status: resolved   # closed by Plan 03-13 Task 2
  reason_resolved: "Plan 03-13: _proposal_card.html.j2 finalized to SIDE QTY TICKER + $cost chip + 1-line summary with rationale/evidence in collapsed <details>; cost formatted $X,XXX.XX via _build_proposal_ctx; 03-UI-SPEC.md Surface 2 updated with Compact Card Contract. (Slack-card parity left as a deferred note.)"
  reason: "Card was too text-heavy (full rationale + evidence). Reworked live to SIDE QTY TICKER + $cost + 1-line summary, with full rationale/evidence collapsed. Needs serve restart to surface cost/summary (Python not hot-reloaded), and 03-UI-SPEC.md Surface 2 must be updated to match."
  severity: minor
  test: 4
  missing:
    - "Finalize compact-card design + dollar formatting; update 03-UI-SPEC.md Surface 2"
    - "Apply the same compact treatment to the Slack Block Kit card if desired (parity)"

- truth: "Approving a paper proposal executes the order and records a fill (executes identically to Slack path)"
  status: resolved   # closed by Plan 03-12 (triage: no code bug; live paper fill still a human-verify item)
  reason_resolved: "Plan 03-12 triage: 'broker not configured' string lives ONLY in alpaca_data.py (Researcher get_quote fallback), NEVER on the executor path (grep-confirmed absent from executor.py + routes.py). The observed FAILED proposal was the market-closed guard firing during off-hours testing (Scenario A) — correct behavior. Tests added: paper approve path reaches EXECUTING with a configured broker; architectural grep gate keeps the string off the executor path. NOTE: actual paper place-then-fill during market hours remains a human-verify item."
  reason: "On approve, executor logs 'BrokerOrderError: broker not configured; falling back to yahooquery' and the proposal goes FAILED — the order never places/fills. Trading STREAM connects (paper) but the order-placement broker client appears unconfigured. Blocks the execution half of DASH-04 / SC-5."
  severity: major
  test: 4
  artifacts:
    - "src/gekko/execution/executor.py — broker resolution / place_order path"
    - "src/gekko/agent/tools/alpaca_data.py — 'broker not configured' fallback origin"
  missing:
    - "Triage whether paper order placement needs broker_credentials config or a code wiring gap; fix so approve → place → fill works on paper"

- truth: "Dashboard /approvals separates expired trades from pending/complete (state segmentation / tabs)"
  status: enhancement   # NEW SCOPE from live UAT 2026-06-22 (Test 4) — not a Phase-3 acceptance criterion
  routed_to: Phase 6 (Web Dashboard & Multi-User Auth) — per operator 2026-06-22; recorded in ROADMAP.md
  severity: minor
  test: 4
  reason: "Operator: expired trades should live in their own section vs pending/complete — maybe tabs (Pending / Completed / Expired). Core Test-4 criteria all passed; this is additive ergonomics."
  missing:
    - "Group /approvals proposals by state; surface expired separately from pending/complete (tabs or sections)"

- truth: "Dashboard has an easy site-wide toolbar/nav for moving between pages"
  status: enhancement   # NEW SCOPE from live UAT 2026-06-22 — cross-cutting dashboard nav, not a Phase-3 criterion
  routed_to: Phase 6 (Web Dashboard & Multi-User Auth) — per operator 2026-06-22; recorded in ROADMAP.md
  severity: minor
  test: 4
  reason: "Operator: enhance the toolbar on the website to make it easier to navigate between pages."
  missing:
    - "Add/improve a persistent dashboard nav toolbar across pages (approvals, strategies, kill-switch, etc.)"

- truth: "Dashboard /approvals reflects new proposals without a manual reload"
  status: resolved   # closed by Plan 03-13 Task 1
  reason_resolved: "Plan 03-13: GET /approvals/poll registered on the authenticated router (require_session); approvals_index polls via hx-get=/approvals/poll hx-trigger='every 30s'; _proposals_list.html.j2 fragment added; modal-mount placed outside the polling container so edit-size modal survives refreshes."
  reason: "/approvals is static — new proposals don't appear until the operator reloads (no polling/SSE). Combined with the 30-min expiry, proposals can be missed/lost. Operator expected the dashboard to surface new trades live."
  severity: minor
  test: 4
  missing:
    - "Add lightweight live refresh to /approvals (HTMX polling or SSE) so new/expired proposals update without manual reload"
