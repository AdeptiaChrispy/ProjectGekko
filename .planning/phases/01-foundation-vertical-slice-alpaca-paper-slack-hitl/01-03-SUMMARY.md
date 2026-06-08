---
phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl
plan: 03
subsystem: database
tags: [sqlcipher, sqlalchemy, alembic, aiosqlite, auth-03, d-14, d-19, d-21]
requires:
  - phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl
    plan: 01
    provides: |
      uv-managed Python 3.12 src-layout, `gekko.*` package namespace, Typer
      CLI, pytest + ruff + mypy toolchain.
  - phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl
    plan: 02
    provides: |
      Settings (db_path_for, db_url_for), structlog credential redaction,
      9 shared conftest fixtures (temp_sqlcipher_db is the load-bearing one
      this plan deepens from a Path stub into a real AsyncEngine fixture).
provides:
  - "gekko.core.errors — GekkoError + WrongPassphraseError + BrokerConfigError + BrokerOrderError + BudgetExceeded + AuditChainBroken hierarchy"
  - "gekko.db.engine.get_async_engine(db_path, passphrase) -> AsyncEngine — SQLCipher-backed async engine with PRAGMA-key connect-event handler (Pitfall 1)"
  - "gekko.db.engine.get_sync_engine(db_path, passphrase) -> Engine — SQLCipher-backed sync engine for APScheduler SQLAlchemyJobStore (Plan 01-09) + Alembic migrations"
  - "gekko.db.engine.verify_passphrase(engine) -> None — Pitfall 2 wrong-passphrase smoke probe; converts OperationalError into WrongPassphraseError"
  - "gekko.db.models — Base + User + Strategy + Guidance + Proposal + Event + BrokerCredential SQLAlchemy 2.x typed-Mapped models (6 P1 tables, every one carrying user_id per D-21)"
  - "gekko.db.session.make_session_factory(engine) -> async_sessionmaker[AsyncSession] + AsyncSessionLocal type alias"
  - "alembic.ini + migrations/env.py + migrations/versions/0001_initial.py — initial migration creating the 6 P1 tables; passphrase NEVER persisted in alembic.ini (T-01-03-04)"
  - "tests/conftest.py — temp_sqlcipher_db upgraded from Path stub to real AsyncEngine fixture; new migrated_sqlcipher_db fixture invokes alembic upgrade head via subprocess"
  - "tests/integration/test_sqlcipher_passphrase.py — 3 integration tests gated by @pytest.mark.integration"
affects:
  - 01-04 (audit log — imports AuditChainBroken, Event, async session factory; uses canonical hash chain over events.payload_json)
  - 01-05 (brokers — imports BrokerConfigError / BrokerOrderError; broker_credentials table is where AlpacaBroker reads paper keys from)
  - 01-06 (schemas — Strategy/TradeProposal Pydantic models serialize into Strategy.payload_json / Proposal.payload_json)
  - 01-07 (agent runtime — imports BudgetExceeded; Proposal Writer inserts into proposals + events tables)
  - 01-08 (slack/approval — proposals state machine PENDING -> APPROVED via Slack action handler)
  - 01-09 (CLI + APScheduler — get_sync_engine is the load-bearing API for SQLAlchemyJobStore; `gekko init` prompts for passphrase + runs alembic upgrade head)
tech-stack:
  added:
    - "aiosqlite>=0.22.1 — async SQLite driver wrapped around our sqlcipher3 connector (Rule 3 auto-fix; was missing from Plan 01-01 dep list)"
  patterns:
    - "Engine factory uses creator=/async_creator_fn= callback that returns a sqlcipher3 Connection — the SQLAlchemy URL is `:memory:` placeholder so the passphrase is NEVER embedded in `str(engine.url)` or `repr(engine)` (T-01-03-05)"
    - "PRAGMA key is the FIRST statement on every new DBAPI connection via @event.listens_for(engine, 'connect') — applies on every pool-miss, not once per engine (Pitfall 1)"
    - "Wrong-passphrase smoke probe (SELECT count(*) FROM sqlite_master) inside the connect handler — converts sqlcipher3.dbapi2.DatabaseError into our typed WrongPassphraseError (Pitfall 2)"
    - "Dialect DBAPI exception classes patched to point at sqlcipher3 (not sqlite3 / aiosqlite) so SQLAlchemy correctly wraps IntegrityError/OperationalError/etc into sqlalchemy.exc.*"
    - "Alembic sqlalchemy.url is LEFT BLANK; env.py reads GEKKO_DB_PASSPHRASE at runtime and constructs the engine via get_async_engine — passphrase NEVER persisted (T-01-03-04)"
    - "CheckConstraint vocabularies declared once at the top of models.py and re-imported by hand into 0001_initial.py — single source of truth for the 3 enums (guidance.scope, proposals.status, events.event_type)"
    - "Defense-in-depth __repr__ excludes payload_json + key_blob + secret_blob from Proposal / Event / BrokerCredential (AUTH-04 belt-and-braces)"
key-files:
  created:
    - src/gekko/core/errors.py
    - src/gekko/db/engine.py
    - src/gekko/db/models.py
    - src/gekko/db/session.py
    - alembic.ini
    - migrations/env.py
    - migrations/script.py.mako
    - migrations/versions/0001_initial.py
    - tests/unit/test_db_engine.py
    - tests/unit/test_db_models.py
    - tests/integration/test_sqlcipher_passphrase.py
  modified:
    - pyproject.toml (added aiosqlite>=0.22.1)
    - tests/conftest.py (temp_sqlcipher_db upgraded; migrated_sqlcipher_db added)
    - .ruff.toml (TRY003 per-file-ignores for migrations/env.py + tests/)
key-decisions:
  - "Chose the `creator=` / `async_creator_fn=` callback pattern over SQLAlchemy's stock `sqlite+pysqlcipher://` URL form. Rationale: the stock dialect reads `url.password` and emits `PRAGMA key=<quoted>`, which means `str(engine.url)` leaks the passphrase. Our callback path returns a freshly opened sqlcipher3.dbapi2 connection (sync) or an aiosqlite.Connection wrapping a sqlcipher3 connector (async); the URL is `:memory:` placeholder so `repr(engine)` / `str(engine.url)` cannot leak the passphrase (T-01-03-05). The PRAGMA key is set entirely by our connect-event handler — full control over Pitfall 1 ordering and Pitfall 2 smoke probe."
  - "Added aiosqlite>=0.22.1 as a direct dependency (Rule 3 — auto-fix blocker). The plan's chosen `sqlite+aiosqlite:///{db_path}` URL form requires it; it was missing from the Plan 01-01 pyproject scaffold. aiosqlite is omnilib's standard async-sqlite library (5+ years old, deeply established, transitively required by FastAPI/SQLAlchemy async stacks); legitimacy is non-controversial."
  - "Patched dialect.loaded_dbapi exception attributes (IntegrityError, OperationalError, DatabaseError, etc.) post-construction to point at sqlcipher3.dbapi2.* — without this, sqlcipher3's exception hierarchy (which does NOT inherit from sqlite3) escapes SQLAlchemy's _handle_dbapi_exception machinery as raw DBAPI errors. The patch keeps the standard sqlalchemy.exc.IntegrityError contract working for downstream code."
  - "Plan 01-09's APScheduler integration is unblocked: get_sync_engine returns a pre-built Engine with the same SQLCipher PRAGMA key handler wired up. `SQLAlchemyJobStore(engine=get_sync_engine(...))` will work without ever passing a URL string with a passphrase. Tests cover passphrase-not-in-repr / passphrase-not-in-url for the sync engine specifically (AUTH-03 cross-engine parity per VALIDATION row 01-09-T2)."
  - "Alembic's `version_locations = %(here)s/migrations/versions` setting in alembic.ini was REMOVED — alembic splits the setting on whitespace, and `%(here)s` expanded to `C:/Users/chris.platika/Desktop/Project Gekko` (containing a space), causing alembic to discover ZERO revisions. The default ({script_location}/versions) handles spaces correctly. Documented inline in alembic.ini."
  - "CheckConstraint vocabularies (guidance.scope, proposals.status, events.event_type) are declared as `tuple[str, ...]` constants at the top of `gekko.db.models` AND copied into `migrations/versions/0001_initial.py`. The duplication is intentional — Alembic migrations are frozen historical artifacts (they MUST work against any future models.py state), so the migration cannot import the live vocabulary. The pattern keeps the duplication local and obvious."
  - "BrokerCredential.paper has server_default='1' (TRUE) to enforce paper-only at the DB layer in addition to the AlpacaBroker constructor guard (Plan 01-05)."
patterns-established:
  - "Pattern: Engine factory + connect-event handler — every encrypted DB connection in Gekko goes through get_async_engine / get_sync_engine. No direct sqlcipher3.dbapi2.connect() calls outside this module."
  - "Pattern: Plan 01-04+ migrations are diffed against Base.metadata; the env.py target_metadata points at it."
  - "Pattern: alembic env.py reads GEKKO_DB_PASSPHRASE from env at runtime, never from alembic.ini. `gekko init` (Plan 01-09) sets the env var on the child subprocess before invoking alembic."
  - "Pattern: Models declare __repr__ that excludes payload_json / *_blob fields — defense-in-depth against accidental log.info(model_instance)."
  - "Pattern: Test fixtures separate fast unit (Base.metadata.create_all) from slow integration (alembic upgrade head subprocess). The integration tests are marked @pytest.mark.integration so the unit feedback loop stays sub-10s."
requirements-completed:
  - AUTH-03
metrics:
  duration_minutes: 60
  completed: "2026-06-08T18:35:00Z"
---

# Phase 01 Plan 03: SQLCipher Engine + 6-Table Data Model + Alembic 0001_initial Summary

**SQLCipher-encrypted SQLite engine with PRAGMA-key connect-event handler (Pitfall 1) and typed `WrongPassphraseError` (Pitfall 2); SQLAlchemy 2.x ORM for the 6 Phase 1 tables (`users`, `strategies`, `guidance`, `proposals`, `events`, `broker_credentials`) with `user_id` everywhere (D-21); Alembic `0001_initial` migration creating all 6 tables, with the passphrase NEVER persisted in any config file (T-01-03-04). Sync engine factory added per the IMPORTANT #4 revision so Plan 01-09's APScheduler integration can pass a pre-built `Engine` rather than a URL string with the passphrase embedded.**

## Performance

- **Duration:** ~60 min (~17:40 → ~18:35 UTC)
- **Tasks:** 3 (Tasks 1 + 2 are `tdd="true"` so each got separate RED + GREEN commits = 5 task commits)
- **Files created:** 11 (4 src/gekko, 3 migrations, 3 tests, 1 alembic.ini)
- **Files modified:** 3 (pyproject.toml, tests/conftest.py, .ruff.toml)

## Accomplishments

- **AUTH-03 satisfied end-to-end.** SQLCipher whole-DB encryption is active on every connection (`PRAGMA key` fires before any other SQL, verified by 12 unit tests). Wrong passphrase raises typed `WrongPassphraseError`, not a confusing `OperationalError` (verified by 2 unit tests + 1 integration test). Passphrase never appears in `repr(engine)`, `str(engine.url)`, alembic.ini, or any log line.
- **D-21 multi-user-ready data model.** Every one of the 6 P1 tables carries a `user_id` column; the events table matches D-14 exactly (`id, ts, user_id, strategy_id, event_type, payload_json, prev_hash, row_hash`).
- **D-05 snapshot-row versioning.** `Strategy` has `UNIQUE(user_id, strategy_name, version)` + `ix_strategy_name_lookup` index for fast latest-version lookups. Test confirms `ORDER BY version DESC` returns rows in [3, 2, 1] order.
- **Sync engine factory wired for APScheduler.** `get_sync_engine` mirrors `get_async_engine` (same PRAGMA handler, same passphrase-in-closure invariant). Plan 01-09 can pass it straight to `SQLAlchemyJobStore(engine=...)` without round-tripping through a URL string.
- **Test infrastructure deepened.** `tests/conftest.py::temp_sqlcipher_db` is no longer a Path stub — it's a real `AsyncEngine` with the 6 P1 tables already created via `Base.metadata.create_all`. New `migrated_sqlcipher_db` fixture invokes `alembic upgrade head` via subprocess for integration tests that need real migration history.
- **All gates green:** 65 unit tests + 3 integration tests = 68 total pass; ruff + mypy --strict clean across 23 source files.

## Task Commits

Tasks 1 and 2 followed strict TDD (RED commit then GREEN commit). Task 3 was a single commit since the integration tests were authored alongside the migration:

1. **Task 1 RED** — failing engine tests + aiosqlite dep — `e586d66` (test)
2. **Task 1 GREEN** — SQLCipher engine factories + errors hierarchy — `03ca810` (feat)
3. **Task 2 RED** — failing model tests (14 of them) — `7252bf9` (test)
4. **Task 2 GREEN** — SQLAlchemy ORM models + session factory + DBAPI exception patching — `7eae12e` (feat)
5. **Task 3** — alembic init + 0001_initial migration + 3 integration tests — `ff91075` (feat)

## Files Created (11)

### Source layer (4)

- `src/gekko/core/errors.py` — GekkoError + WrongPassphraseError + BrokerConfigError + BrokerOrderError + BudgetExceeded + AuditChainBroken
- `src/gekko/db/engine.py` — `get_async_engine`, `get_sync_engine`, `verify_passphrase`; connect-event handler; DBAPI exception class patching
- `src/gekko/db/models.py` — Base + 6 typed-Mapped ORM models with CheckConstraints + indexes + defense-in-depth `__repr__`
- `src/gekko/db/session.py` — `make_session_factory(engine)` + `AsyncSessionLocal` type alias

### Alembic (4)

- `alembic.ini` — script_location=migrations, sqlalchemy.url BLANK, no version_locations override (avoids the space-in-path split bug)
- `migrations/env.py` — async migration entry point reading `GEKKO_DB_PASSPHRASE` from env; engine built via `get_async_engine`
- `migrations/script.py.mako` — modern Python 3.12 template for future revisions
- `migrations/versions/0001_initial.py` — creates the 6 P1 tables with FKs, indexes, and the 3 CheckConstraint vocabularies inline; downgrade in FK-dependency-reversed order

### Tests (3)

- `tests/unit/test_db_engine.py` — 12 tests covering the 6 plan behaviors + sync-engine parity (round-trip, PRAGMAs, wrong-passphrase, repr-leak)
- `tests/unit/test_db_models.py` — 15 tests covering 6-tables-exist, user_id-everywhere, D-14 events shape, CheckConstraints, D-05 versioning, defense-in-depth `__repr__`
- `tests/integration/test_sqlcipher_passphrase.py` — 3 integration tests gated by `@pytest.mark.integration`: fresh-DB upgrade-head, wrong-passphrase-after-migration, idempotent re-run

## Files Modified (3)

- `pyproject.toml` — added `aiosqlite>=0.22.1` (auto-fix Rule 3; see Deviations below)
- `tests/conftest.py` — `temp_sqlcipher_db` upgraded from Path stub to real `AsyncEngine` fixture; new `migrated_sqlcipher_db` fixture
- `.ruff.toml` — added `TRY003` per-file-ignore for `migrations/env.py` and `tests/**/*.py` (auto-fix Rule 1; long boundary error messages are more useful than 3-word exception subclasses)

## Plan `<output>` block answers

The plan asked the executor to record three things in this SUMMARY:

1. **Which `sqlcipher3` binding URL form actually worked?** Neither of the two alternatives in the plan worked verbatim. **Final form: SQLAlchemy URL is a placeholder (`sqlite:///:memory:` sync, `sqlite+aiosqlite:///:memory:` async); the real connection is opened by a `creator=` / `async_creator_fn=` callback that returns `sqlcipher3.dbapi2.connect(<real_path>)` (sync) or `aiosqlite.Connection(<sqlcipher_connector>, ...)` (async).** This avoids the stock `sqlite+pysqlcipher://` dialect's URL-embedded passphrase leak (its `on_connect_url` uses `url.password` and emits `PRAGMA key=<quoted>`). Alternative B from the plan (URL `sqlite+aiosqlite:///{db_path}` with `module=sqlcipher3.dbapi2`) was attempted first but fails immediately because the aiosqlite dialect's adapter (`AsyncAdapt_aiosqlite_dbapi`) checks `aiosqlite.has_stop = hasattr(aiosqlite.Connection, "stop")` and accesses other aiosqlite-specific attributes that `sqlcipher3.dbapi2` does not expose.
2. **Did the executor run the Windows wrong-passphrase manual verification (VALIDATION §Manual-Only row 3)?** This executor ran on Windows 11. `test_wrong_passphrase_after_migration_rejects` PASSED on the executor's Windows machine, which satisfies the Windows leg of the cross-platform parity check. A fresh-Mac re-run of the same scenario remains a phase-gate manual verification per VALIDATION (it cannot be automated here because the executor only has Windows). Deferred to the Plan 01-09 manual verification checkpoint.
3. **Schema diff vs RESEARCH §Architecture Patterns diagram:** No deltas. Every column listed in the RESEARCH diagram is present with the documented type and FK shape. The only additions in this plan are: (a) `Strategy.payload_json` is `String` (not `JSON`) because the SQLite native JSON ops are not used in P1 — text storage with canonical JSON serialization is enough; (b) `Proposal.client_order_id` + `Proposal.broker_order_id` are nullable strings, populated by the Proposal Writer (Plan 01-07) and the Executor (Plan 01-08) respectively; (c) every model declares a defense-in-depth `__repr__` (AUTH-04). The `apscheduler_jobs` table is intentionally NOT in this migration — APScheduler 3.x's `SQLAlchemyJobStore` creates it itself at runtime in Plan 01-09.

## Decisions Made

See frontmatter `key-decisions`. The two most consequential are:

1. **`creator=` callback strategy over `sqlite+pysqlcipher://` URL form** — keeps the passphrase out of `repr(engine)` and `str(engine.url)` entirely (T-01-03-05 mitigation by construction).
2. **DBAPI exception-class patching** — without it, raw `sqlcipher3.dbapi2.IntegrityError` escapes SQLAlchemy's wrapping machinery (because `sqlcipher3` does NOT inherit from `sqlite3`), and tests/code catching `sqlalchemy.exc.IntegrityError` silently fail.

## Deviations from Plan

### Auto-fixed during execution

**1. [Rule 3 — Blocker] Added `aiosqlite>=0.22.1` to `pyproject.toml`.**

- **Found during:** Task 1 GREEN initial run
- **Issue:** The plan's chosen "Alternative B" URL form (`sqlite+aiosqlite:///{db_path}`) requires the `aiosqlite` package, which was missing from the Plan 01-01 dependency list. `import aiosqlite` raised `ModuleNotFoundError`.
- **Fix:** `uv add aiosqlite` (resolved to 0.22.1). aiosqlite is omnilib's standard async-sqlite wrapper (5+ years old, deeply established, transitively required by many FastAPI/SQLAlchemy async stacks); not a slopsquat risk and no human-verify checkpoint required.
- **Files modified:** `pyproject.toml`, `uv.lock`
- **Verification:** `import aiosqlite` succeeds; all 12 engine tests pass
- **Committed in:** `e586d66` (Task 1 RED commit)

**2. [Rule 3 — Blocker] Removed `version_locations = %(here)s/migrations/versions` from `alembic.ini`.**

- **Found during:** Task 3 first alembic-heads invocation (returned 0 revisions)
- **Issue:** Alembic splits the `version_locations` setting on whitespace. `%(here)s` expanded to `C:/Users/chris.platika/Desktop/Project Gekko` (with a space), causing alembic to parse it as two paths (`C:/Users/chris.platika/Desktop/Project` and `Gekko/migrations/versions`) and discover ZERO revisions in either.
- **Fix:** Removed the explicit setting. Alembic defaults to `{script_location}/versions` which handles paths with spaces correctly.
- **Files modified:** `alembic.ini` (added inline comment explaining the gotcha)
- **Verification:** `uv run python -c "from alembic.script import ScriptDirectory; ..."` confirms `0001_initial` revision discovered; `alembic upgrade head` smoke test successfully creates all 6 tables
- **Committed in:** `ff91075` (Task 3 commit)

**3. [Rule 1 — Lint] Added `TRY003` per-file-ignore for `migrations/env.py` and `tests/**/*.py`.**

- **Found during:** Task 3 verification (`ruff check .`)
- **Issue:** `tryceratops` rule TRY003 flagged two long error messages — one in `migrations/env.py::_require_passphrase` explaining which env var is missing and why, one in `tests/conftest.py::migrated_sqlcipher_db` capturing subprocess stdout/stderr.
- **Fix:** Added per-file-ignores for TRY003 in those paths. The boundary error-message context (which env var; what subprocess output) is more useful to the operator than a stack-traced 3-word exception subclass.
- **Files modified:** `.ruff.toml` (added comment explaining the exemption)
- **Verification:** `ruff check .` clean
- **Committed in:** `ff91075` (Task 3 commit)

**4. [Rule 1 — Lint] Auto-fixed UP017 (`datetime.timezone.utc` → `datetime.UTC`) and SIM117 / I001 sorts.**

- **Found during:** Task 1 GREEN and Task 2 GREEN verification (`ruff check --fix`)
- **Issue:** Standard Python 3.12 modernizations (`datetime.UTC` alias; single `with` over nested context managers; import sort).
- **Fix:** `ruff check --fix .` auto-applied; re-ran tests, still 65/65 passing.
- **Committed in:** Same task commits as the GREEN implementations

**5. [Rule 1 — Bug] Added DBAPI exception-class patching in `_patch_dialect_dbapi_exceptions`.**

- **Found during:** Task 2 first GREEN run (`test_insert_strategy_unique_constraint` failed with raw `sqlcipher3.dbapi2.IntegrityError` escaping unwrapped)
- **Issue:** SQLAlchemy's dialect binds its DBAPI exception classes (`self.dbapi.IntegrityError` etc.) to `sqlite3.*` at dialect-init time. The actual connection objects we hand back via `creator=` are sqlcipher3 connections, whose exception hierarchy does NOT inherit from `sqlite3` — so `sqlcipher3.dbapi2.IntegrityError` escapes SQLAlchemy's `_handle_dbapi_exception` machinery as a raw DBAPI error instead of being wrapped in `sqlalchemy.exc.IntegrityError`. Tests catching the standard `sqlalchemy.exc.IntegrityError` contract silently failed.
- **Fix:** Added `_patch_dialect_dbapi_exceptions(engine)` that overwrites each of the 10 standard DBAPI exception attributes on `dialect.loaded_dbapi` to point at `sqlcipher3.dbapi2.*`. Called from both `get_async_engine` and `get_sync_engine` after construction.
- **Files modified:** `src/gekko/db/engine.py`
- **Verification:** All 15 model tests pass; `pytest.raises(IntegrityError)` correctly catches the wrapped exception
- **Committed in:** `7eae12e` (Task 2 GREEN commit)

**6. [Rule 1 — Bug] Broadened the `try/except sqlcipher3.dbapi2.DatabaseError` block to wrap ALL PRAGMA statements (not just the smoke probe).**

- **Found during:** Task 1 first GREEN run (`test_wrong_passphrase_raises_wrongpassphraseerror` async path failed)
- **Issue:** With a wrong passphrase, `PRAGMA cipher_compatibility = 4` succeeds (stateless), but `PRAGMA journal_mode = WAL` requires reading the DB header and so fails with `file is not a database` BEFORE the smoke probe ever runs. My initial `try/except` was scoped only to the probe, so the raw `DatabaseError` escaped.
- **Fix:** Widened the try/except to wrap the entire PRAGMA sequence + the smoke probe. The probe is still defensive — it defends against any future PRAGMA reordering that might let a wrong-passphrase connection slip past the header read.
- **Files modified:** `src/gekko/db/engine.py`
- **Verification:** Both async wrong-passphrase tests pass
- **Committed in:** `03ca810` (Task 1 GREEN commit)

---

**Total deviations:** 6 auto-fixes (1 missing dep, 1 alembic config bug, 2 lint, 2 implementation bugs)
**Impact on plan:** All auto-fixes were essential for correctness. No scope creep — every fix tightened already-planned behavior into a working state. The aiosqlite dependency add is the only material change to the project surface; it was implicit in the plan's chosen URL form anyway.

## Issues Encountered

None outside the auto-fixed deviations above. The plan's documented "Alternative B" was directionally correct but its specific implementation (passing `module=sqlcipher3.dbapi2` to `create_async_engine` with `sqlite+aiosqlite:///` URL) does not work because the aiosqlite dialect's adapter assumes `aiosqlite`-specific attributes on the DBAPI module. The `creator=` callback path was the right escape hatch.

## Known Stubs

None goal-blocking. The following are intentional Wave 0 → Wave 1+ deepening points:

- `BrokerCredential.key_blob` / `secret_blob` are plain `String` (not per-row encrypted) per D-19 — whole-DB SQLCipher encryption is the only at-rest layer in Phase 1; per-row Fernet is explicitly deferred.
- `apscheduler_jobs` table is NOT in `0001_initial.py` — created by APScheduler 3.x's `SQLAlchemyJobStore.start()` in Plan 01-09 (CADENCE-02).

## Self-Check: PASSED

Files verified present:

- `src/gekko/core/errors.py` — FOUND
- `src/gekko/db/engine.py` — FOUND
- `src/gekko/db/models.py` — FOUND
- `src/gekko/db/session.py` — FOUND
- `alembic.ini` — FOUND
- `migrations/env.py` — FOUND
- `migrations/script.py.mako` — FOUND
- `migrations/versions/0001_initial.py` — FOUND
- `tests/unit/test_db_engine.py` — FOUND
- `tests/unit/test_db_models.py` — FOUND
- `tests/integration/test_sqlcipher_passphrase.py` — FOUND

Commits verified in git log:

- `e586d66` — FOUND (Task 1 RED)
- `03ca810` — FOUND (Task 1 GREEN)
- `7252bf9` — FOUND (Task 2 RED)
- `7eae12e` — FOUND (Task 2 GREEN)
- `ff91075` — FOUND (Task 3)

Test gates verified green:

- [x] `uv run pytest tests/unit -q` → 65 passed (38 prior + 12 engine + 15 models)
- [x] `uv run pytest tests/integration -m integration -q` → 3 passed
- [x] `uv run pytest tests/` (full) → 68 passed
- [x] `uv run ruff check .` → All checks passed
- [x] `uv run mypy src` → Success: no issues found in 23 source files
- [x] AUTH-03 closed (whole-DB encryption + passphrase-on-start + wrong-passphrase rejection + no-passphrase-in-repr)

## Next Plan Readiness

Plan 01-04 (audit log) is unblocked. It can:

- `from gekko.db.engine import get_async_engine` and `from gekko.db.session import make_session_factory` to wire a session for the audit writer.
- `from gekko.db.models import Event` and insert rows directly; CheckConstraints enforce the D-14 event_type vocabulary.
- `from gekko.core.errors import AuditChainBroken` for the integrity-failure typed exception.
- Use the `temp_sqlcipher_db` fixture for unit tests of `append_event` / `walk_chain`.

Plans 01-05 (brokers) and 01-07 (agent) can `from gekko.core.errors import BrokerConfigError, BrokerOrderError, BudgetExceeded` without re-declaring.

Plan 01-09's APScheduler integration is unblocked: `get_sync_engine(db_path, passphrase)` returns a sync `Engine` with the same PRAGMA-key handler wired up; `SQLAlchemyJobStore(engine=...)` will work without ever passing a URL string with the passphrase embedded.

---
*Phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl*
*Completed: 2026-06-08*
