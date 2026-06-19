---
status: partial
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
source: [03-VERIFICATION.md]
started: 2026-06-18T14:00:00Z
updated: 2026-06-19T18:00:00Z
---

## Current Test

[testing complete — 2026-06-19; findings routed to gap-closure]

## Tests

### 1. Slack Block Kit card rendering and button layout
expected: Proposal card appears with approve / reject / edit-size / escalate-to-dashboard buttons; card is visually distinct for paper vs. live (paper chip vs. live chip).
result: pass

### 2. Edit-size modal interaction
expected: Modal closes, card updates to APPROVED state, executor fires in background; the OrderGuard 2% drift check re-applies on the edited size.
result: issue
reported: "Modal opens fine, but any meaningful size change ('+1', a decimal) is rejected as 'outside the range'. The 2% drift check is anchored to the agent's original target_notional, so only ~+/-2% of the original qty is accepted — you can't actually resize."
severity: major
finding: |
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

### 4. Dashboard fallback end-to-end (Slack unavailable)
expected: Operator logs in (passphrase), approves/rejects/edits via /approvals; proposal transitions to APPROVED, executor fires, fill recorded in audit log — identical to the Slack path. Unauthenticated access to /live-confirm, /kill, /unkill, /promote-to-live, /trigger redirects to /login.
result: issue
reported: "Auth gate works (unauth → /login). But /approvals showed NOTHING despite PENDING proposals (DASH-04 blank-card bug — fixed this session). Also: approve → executor fails with 'broker not configured' so the order never fills; and the page is not live (new proposals don't appear without a manual reload)."
severity: major
finding: |
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
passed: 1
issues: 2
pending: 0
skipped: 2
blocked: 0

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
  status: resolved   # closed by Plan 03-11 (verified static; live Slack modal behavior still a human-verify item)
  reason_resolved: "Plan 03-11: _check_edit_size_caps validates operator edits against OrderGuard hard caps (max_position_pct * equity), not 2% drift; wired on both Slack + dashboard paths; plain-language framing + bounds; 03-UI-SPEC.md Surface 1 + D-54 updated. Code-review CR-01 (fail-open-on-strategy-load-failure) fixed to mode-aware fail-closed (LIVE blocks, PAPER lenient). 6 unit tests in test_edit_size_caps.py + dashboard cap-rejection + LIVE-fail-closed tests green."
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

- truth: "Dashboard /approvals reflects new proposals without a manual reload"
  status: resolved   # closed by Plan 03-13 Task 1
  reason_resolved: "Plan 03-13: GET /approvals/poll registered on the authenticated router (require_session); approvals_index polls via hx-get=/approvals/poll hx-trigger='every 30s'; _proposals_list.html.j2 fragment added; modal-mount placed outside the polling container so edit-size modal survives refreshes."
  reason: "/approvals is static — new proposals don't appear until the operator reloads (no polling/SSE). Combined with the 30-min expiry, proposals can be missed/lost. Operator expected the dashboard to surface new trades live."
  severity: minor
  test: 4
  missing:
    - "Add lightweight live refresh to /approvals (HTMX polling or SSE) so new/expired proposals update without manual reload"
