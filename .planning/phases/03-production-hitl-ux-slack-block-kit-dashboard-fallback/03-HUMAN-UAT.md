---
status: partial
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
source: [03-VERIFICATION.md]
started: 2026-06-18T14:00:00Z
updated: 2026-06-22T12:00:00Z
---

## Current Test

[testing complete — 2026-06-22 re-verification: Test 1 pass, Test 4 pass; Test 2 reopened (edit-size legibility); Tests 3 & 5 time-gated/skipped; 2 new dashboard-nav enhancements logged]

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
issues: 1
pending: 0
skipped: 2
blocked: 0
enhancements: 2   # dashboard state-tabs + site nav toolbar (new scope, minor)

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
  status: failed   # REOPENED by live UAT 2026-06-22 — cap math correct (03-11) but UI still not digestible
  severity: major
  test: 2
  reason: "User reported: 'still not very clear to the end user, but maybe that means the safety net is too low.' Cap-based validation (03-11) works, but the modal is not legible for a non-technical operator. Root: UNIT-MODEL MISMATCH — users think in whole shares (1→2) but a low max_position_pct only permits a tiny fractional band (e.g. 1→~1.02 on the ai-infra-bull test strategy) that is never shown up front, so valid resizing feels impossible and rejections feel arbitrary."
  history: "03-11 closed the threshold-math gap (drift → hard caps); this is the remaining legibility/calibration layer on top of that."
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
