---
status: partial
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
source: [03-VERIFICATION.md]
started: 2026-06-18T14:00:00Z
updated: 2026-06-18T14:00:00Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Slack Block Kit card rendering and button layout
expected: Proposal card appears with approve / reject / edit-size / escalate-to-dashboard buttons; card is visually distinct for paper vs. live (paper chip vs. live chip).
result: [pending]

### 2. Edit-size modal interaction
expected: Modal closes, card updates to APPROVED state, executor fires in background; the OrderGuard 2% drift check re-applies on the edited size.
result: [pending]

### 3. Quiet-hours queuing behavior over time
expected: No Slack DM arrives during the quiet window; DM arrives when the window opens. Safety-critical categories (kill, executor errors, first-live fills, proposal expiry) still fire during quiet hours.
result: [pending]

### 4. Dashboard fallback end-to-end (Slack unavailable)
expected: Operator logs in (passphrase), approves/rejects/edits via /approvals; proposal transitions to APPROVED, executor fires, fill recorded in audit log — identical to the Slack path. Unauthenticated access to /live-confirm, /kill, /unkill, /promote-to-live, /trigger redirects to /login.
result: [pending]

### 5. Daily P&L digest at 16:30 ET on a NYSE trading day
expected: Block Kit digest with correct gross P&L (BUYs subtract, SELLs add), per-strategy breakdown by strategy name — no `_unknown_` buckets, no sign-flipped SELLs.
result: [pending]

## Summary

total: 5
passed: 0
issues: 0
pending: 5
skipped: 0
blocked: 0

## Gaps
