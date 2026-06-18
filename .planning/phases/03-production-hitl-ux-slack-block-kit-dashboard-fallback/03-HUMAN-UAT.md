---
status: partial
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
source: [03-VERIFICATION.md]
started: 2026-06-18T14:00:00Z
updated: 2026-06-18T14:00:00Z
---

## Current Test

number: 2
name: Edit-size modal interaction
expected: |
  Modal closes, card updates to APPROVED state, executor fires in background;
  the OrderGuard 2% drift check re-applies on the edited size.
awaiting: user response

## Tests

### 1. Slack Block Kit card rendering and button layout
expected: Proposal card appears with approve / reject / edit-size / escalate-to-dashboard buttons; card is visually distinct for paper vs. live (paper chip vs. live chip).
result: pass

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

- truth: "Operator can run the app against an existing database (migrations apply cleanly to a DB that already holds rows)"
  status: failed
  reason: "User ran `alembic upgrade head` against the live DB (at rev 0001). 0002_orderguard's `batch_alter_table('users')` recreates the table (DROP TABLE users) but FK enforcement is ON and child rows reference users → `FOREIGN KEY constraint failed`. migrations/env.py sets render_as_batch=True but never disables PRAGMA foreign_keys around the migration (and the pragma is a no-op inside a transaction). The Alembic round-trip test only runs on an empty DB, so the FK-with-data path was never exercised. Blocks the app from starting (expiry sweep then fails on missing proposals.account_mode). Prerequisite blocker for UAT Tests 2-5."
  severity: blocker
  test: prerequisite
  artifacts:
    - "migrations/env.py — _do_run_migrations / run_migrations_online: no PRAGMA foreign_keys=OFF around batch table-rebuild"
    - "migrations/versions/0002_orderguard.py — batch_alter_table('users') triggers table recreate + DROP"
    - "tests: alembic round-trip test runs on empty DB; no seeded-data + FK-children migration test"
  missing:
    - "Disable FK enforcement during migrations on the raw connection OUTSIDE the transaction (PRAGMA foreign_keys=OFF before run, ON after), per Alembic SQLite batch guidance"
    - "Add a regression test that seeds users + child rows then runs `alembic upgrade head` end-to-end (would have caught this)"
    - "Document/automate cleanup of a stray _alembic_tmp_* table left by a failed batch, and a DB backup step"
