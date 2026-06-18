---
status: resolved
trigger: "alembic upgrade head fails on an existing SQLCipher DB with data — FOREIGN KEY constraint failed on DROP TABLE users during 0002 batch_alter_table"
created: 2026-06-18
updated: 2026-06-18
source: 03-HUMAN-UAT.md (Phase 3 UAT blocker gap)
---

## Symptoms

- expected: |
    `alembic upgrade head` applies cleanly to an existing SQLCipher database that already holds
    rows, advancing it from rev 0001 through 0002 → 0003 → 0004, so `gekko serve` / `gekko run`
    can start. Migrations must be safe to run repeatedly on a populated real-money ledger.
- actual: |
    Upgrade fails partway through 0002_orderguard with
    `sqlcipher3.dbapi2.IntegrityError: FOREIGN KEY constraint failed [SQL: DROP TABLE users]`.
    DB remains at rev 0001 (migration not committed). Until fixed, the app crashes — e.g. the
    expiry sweep fails on missing `proposals.account_mode`.
- error: "sqlcipher3.dbapi2.IntegrityError: FOREIGN KEY constraint failed  [SQL: \nDROP TABLE users]"
- timeline: |
    DB was created at Phase 1 (rev 0001). Phase 2 (0002) and Phase 3 (0004) migrations were never
    applied to this DB. First surfaced 2026-06-18 during Phase 3 UAT when the operator started the app.
- reproduction: |
    Against a SQLCipher DB at rev 0001 that has at least one `users` row plus child rows
    (strategies/proposals/events referencing it):
    `$env:GEKKO_DB_PASSPHRASE='...'; uv run alembic upgrade head`
    Reproduce on a THROWAWAY seeded DB — never the live ledger.

## Leading Hypothesis (operator-supplied, to verify)

`0002_orderguard.upgrade()` uses `op.batch_alter_table("users")` (to add kill_active columns), which
on SQLite recreates the table: create temp → copy → `DROP TABLE users` → rename. The engine
(`src/gekko/db/engine.py`) enforces `PRAGMA foreign_keys=ON`, and child tables reference `users`
with real rows, so the DROP is refused. `migrations/env.py` sets `render_as_batch=True` but never
disables FK enforcement around the migration — and `PRAGMA foreign_keys` is a no-op inside a
transaction, so it must be toggled on the raw connection BEFORE Alembic opens its transaction.

## Current Focus

hypothesis: CONFIRMED — see Resolution below.
test: Regression test written at tests/integration/test_alembic_fk_seeded_upgrade.py
next_action: RESOLVED

## Investigation Constraints

- The live DB is the user's real-money ledger, currently intact at rev 0001. Do NOT run migrations
  against it during investigation. Reproduce only on throwaway seeded DBs.
- The failed batch likely left a stray `_alembic_tmp_users` table; the fix/runbook must handle
  dropping it (and a DB backup step) before the operator re-runs.
- Sweep ALL migrations (0002, 0003, 0004) for the same batch-recreate-on-FK-referenced-table issue;
  don't fix only 0002.
- The existing Alembic round-trip test runs on an EMPTY DB so it never caught this. A proper fix
  includes a regression test that seeds users + child rows, then runs `upgrade head` end-to-end.

## Evidence

- timestamp: 2026-06-18T00:00:00Z
  finding: |
    engine.py connect-event handler (line 207) issues PRAGMA foreign_keys = ON on every new
    connection. This applies to the connection Alembic uses for migrations.
  file: src/gekko/db/engine.py
  lines: 207

- timestamp: 2026-06-18T00:01:00Z
  finding: |
    SQLite's PRAGMA foreign_keys is session-scoped and is a no-op inside a transaction.
    Alembic opens its transaction via `with context.begin_transaction()` in _do_run_migrations.
    To disable FK enforcement, we must issue the PRAGMA on the raw DBAPI connection BEFORE
    the transaction is opened.
  source: SQLite docs + Alembic batch docs

- timestamp: 2026-06-18T00:02:00Z
  finding: |
    Migrations sweep — tables affected by batch_alter_table and their FK exposure:
    0002: batch_alter_table("users") — FK-REFERENCED PARENT by strategies/proposals/events/broker_credentials/guidance. VULNERABLE.
    0002: batch_alter_table("broker_credentials") — not FK-referenced by any child table. Safe.
    0002: batch_alter_table("proposals") x2 — at time of 0002 run, slack_action_dedup doesn't exist yet. Safe for 0002.
    0003: batch_alter_table("events") — not FK-referenced. Safe.
    0004: batch_alter_table("users") — same FK exposure as 0002. VULNERABLE.
    0004: batch_alter_table("proposals") — slack_action_dedup is created in step 1 of same migration, then proposals is batch-altered in step 3. slack_action_dedup.proposal_id FKs to proposals. VULNERABLE.
    0004: batch_alter_table("events") — not FK-referenced. Safe.

- timestamp: 2026-06-18T00:03:00Z
  finding: |
    Verified via probe script: `connection.connection.dbapi_connection` returns
    `AsyncAdapt_aiosqlite_connection` which supports synchronous `cursor().execute(PRAGMA ...)`.
    Toggle cycle confirmed: PRAGMA foreign_keys = OFF sets value to 0; = ON restores to 1.
    This works in the `run_sync` callback context that `_do_run_migrations` runs in.

- timestamp: 2026-06-18T00:04:00Z
  finding: |
    Existing tests in test_alembic_0002.py and test_p3_alembic_round_trip.py all pass with
    the env.py fix in place (7 passed in test_alembic_0002.py). Round-trip test skips on
    Windows (known cross-process SQLCipher file-lock behavior — pre-existing skip).

## Eliminated

- "The migration itself is wrong" — No. The batch_alter_table DDL is correct; the issue is
  exclusively that FK enforcement is ON during the DROP TABLE phase of batch recreation.
- "render_as_batch=True missing" — No. env.py already sets render_as_batch=True. The bug is
  FK enforcement around the batch operation, not the batch mode itself.
- "Problem only in 0002" — No. The sweep confirmed 0004 has two additional vulnerable
  batch operations (users + proposals).

## Resolution

root_cause: |
  Alembic's batch_alter_table recreates tables via CREATE-tmp → COPY → DROP → RENAME. The
  engine's connect-event handler (engine.py line 207) issues PRAGMA foreign_keys = ON on every
  connection, including the Alembic migration connection. SQLite refuses DROP TABLE on an
  FK-referenced parent (users, proposals) when child rows exist. PRAGMA foreign_keys cannot be
  toggled inside an active transaction (it's a no-op) — it must be issued on the raw DBAPI
  connection before Alembic opens its transaction. The previous env.py never disabled FK
  enforcement, so any populated DB would fail on 0002 and 0004.

fix: |
  migrations/env.py: Added _get_raw_dbapi_connection() and _set_foreign_keys() helpers.
  _do_run_migrations() now disables FK enforcement on the raw DBAPI connection BEFORE calling
  context.begin_transaction(), and re-enables it in a finally block after the transaction
  commits. FK enforcement is preserved at runtime — the connect-event handler in engine.py
  re-applies PRAGMA foreign_keys = ON on every new connection opened by the application
  (migrations run in a separate process/connection lifecycle).

files_changed:
  - migrations/env.py — FK toggle around migration transaction (the fix)
  - tests/integration/test_alembic_fk_seeded_upgrade.py — regression test (new)

operator_runbook: |
  See "## Operator Runbook" section below.

## Operator Runbook — Applying the Fix to the Live DB

**Before you start:** Back up your live DB first. Your DB is at rev 0001 and was untouched
by the failed migration attempt.

### Step 1: Back up the live DB

  cp %USERPROFILE%\AppData\Local\Gekko\{your-user-id}.db \
     %USERPROFILE%\AppData\Local\Gekko\{your-user-id}.db.backup-$(date +%Y%m%d)

On Windows PowerShell:
  $db = "$env:LOCALAPPDATA\Gekko\{your-user-id}.db"
  Copy-Item $db "$db.backup-$(Get-Date -Format yyyyMMdd)"

### Step 2: Check for stray _alembic_tmp_* tables (from the failed migration)

The failed 0002 migration may have left `_alembic_tmp_users` in the DB. Check and drop it:

  # Open a sqlcipher3 shell or use the probe script:
  python -c "
  import sqlcipher3.dbapi2 as sc
  conn = sc.connect(r'C:\path\to\{your-user-id}.db')
  conn.execute(\"PRAGMA key='YOUR_PASSPHRASE'\")
  conn.execute('PRAGMA cipher_compatibility=4')
  tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")]
  print('Tables:', tables)
  stray = [t for t in tables if t.startswith('_alembic_tmp')]
  print('Stray Alembic tables:', stray)
  conn.close()
  "

  If stray tables are found, drop them:
  python -c "
  import sqlcipher3.dbapi2 as sc
  conn = sc.connect(r'C:\path\to\{your-user-id}.db')
  conn.execute(\"PRAGMA key='YOUR_PASSPHRASE'\")
  conn.execute('PRAGMA cipher_compatibility=4')
  conn.execute('PRAGMA foreign_keys=OFF')
  conn.execute('DROP TABLE IF EXISTS _alembic_tmp_users')
  conn.commit()
  conn.close()
  print('Cleaned.')
  "

### Step 3: Confirm DB is at rev 0001

  $env:GEKKO_DB_PASSPHRASE='YOUR_PASSPHRASE'; uv run alembic current

  Output should say: 0001_initial (head) if migration failed cleanly, or
  show a partial/blank state if it was interrupted mid-transaction.

  If it shows no revision at all (migration was interrupted before the revision
  was stamped), stamp it manually:
  $env:GEKKO_DB_PASSPHRASE='YOUR_PASSPHRASE'; uv run alembic stamp 0001_initial

### Step 4: Run the migration with the fixed env.py

  $env:GEKKO_DB_PASSPHRASE='YOUR_PASSPHRASE'; uv run alembic upgrade head

  Expected output: Running upgrade 0001_initial -> 0002_orderguard
                   Running upgrade 0002_orderguard -> 0003_event_types_phase2
                   Running upgrade 0003_event_types_phase2 -> 0004_p3_hitl_ux

### Step 5: Verify

  $env:GEKKO_DB_PASSPHRASE='YOUR_PASSPHRASE'; uv run alembic current

  Output should say: 0004_p3_hitl_ux (head)

  Then start the app normally:
  $env:GEKKO_DB_PASSPHRASE='YOUR_PASSPHRASE'; uv run gekko serve
