---
phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl
plan: 04
subsystem: audit
tags: [sha256, hash-chain, canonical-json, asyncio-lock, audt-01, audt-02, d-14, d-15, d-16, a11]
requires:
  - phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl
    plan: 03
    provides: |
      gekko.db.models.Event (D-14 columns + CheckConstraint vocabulary),
      gekko.db.engine.get_async_engine + make_session_factory,
      gekko.core.errors.AuditChainBroken (imported for typed-exception
      contract, though Plan 01-04's append_event never raises it — verify is
      walk_chain's job).
provides:
  - "gekko.audit.canonical.canonical_json(payload) — RFC-8785-ish: sort_keys=True, separators=(',',':'), ensure_ascii=True, default=str — locale-stable byte sequence for the hash chain"
  - "gekko.audit.canonical.normalize_decimals(payload) — recursive walker that calls .normalize() on every Decimal (mandatory pre-step for any payload containing money values; RESEARCH §Pitfall 6)"
  - "gekko.audit.canonical.GENESIS_PREV_HASH = '0' * 64 — locked per CONTEXT.md Claude's Discretion A11"
  - "gekko.audit.log.append_event(session, *, user_id, strategy_id, event_type, payload, ts=None) -> Event — single-source-of-truth audit writer, per-user asyncio.Lock serialization, returns the inserted Event row"
  - "gekko.audit.verify.walk_chain(session, user_id) -> list[int] — read-only integrity verifier; returns ids of broken rows, [] if intact; user-scoped"
  - "gekko.audit re-exports the full surface (GENESIS_PREV_HASH, canonical_json, normalize_decimals, append_event, walk_chain) for caller convenience"
affects:
  - 01-07 (agent runtime — Proposal Writer calls append_event(event_type='proposal') with the full structured rationale per D-15; MUST call normalize_decimals on the payload first because TradeProposal.qty is Decimal per D-20)
  - 01-08 (Slack/approval — append_event(event_type='approval'|'rejection') from the action handler; payload includes Slack message id + approver user_id)
  - 01-09 (CLI + APScheduler — `gekko audit verify` CLI command wraps walk_chain; periodic background verifier job uses the same; APScheduler kill_switch handler calls append_event(event_type='kill_switch'))
tech-stack:
  added: []
  patterns:
    - "Per-user asyncio.Lock registry: dict[user_id, asyncio.Lock] guarded by a _registry_lock for lazy creation. Lets different users append in parallel while same-user appends serialize (no prev_hash race)."
    - "Canonical-subset lock-in (RESEARCH §Pattern 3): payload_json column stores the FULL canonical-subset JSON string ({event_type, payload, ts, user_id}) — NOT just the inner payload dict. Re-hashing in walk_chain is then sha256(prev_hash + row.payload_json), no JSON re-parse."
    - "strategy_id is a separate Event column and is INTENTIONALLY excluded from the canonical-subset hash. Future strategy_id backfills / renames cannot retroactively invalidate the chain."
    - "Decimal normalization is the CALLER's job, not canonical_json's. The canonical serializer preserves trailing-zero distinctions ('1.230' != '1.23') so it never silently mutates caller-visible payload shape. normalize_decimals is the explicit pre-step."
    - "ensure_ascii=True on canonical_json: non-ASCII characters escape to \\uXXXX so two writers in different locales produce byte-identical hash inputs."
key-files:
  created:
    - src/gekko/audit/canonical.py
    - src/gekko/audit/log.py
    - src/gekko/audit/verify.py
    - tests/unit/test_audit_canonical.py
    - tests/unit/test_audit_chain.py
    - tests/unit/test_audit_verify.py
  modified:
    - src/gekko/audit/__init__.py (re-export the full public surface)
key-decisions:
  - "payload_json stores the full canonical-subset string ({event_type, payload, ts, user_id}), not just the inner payload dict. RESEARCH §Pattern 3 left this as a planner decision; the lock-in here makes verification a one-liner — sha256(prev_hash + row.payload_json) — instead of requiring walk_chain to rebuild the canonical subset from columns. The cost is ~30-50 bytes of duplicate data per row (event_type, ts, user_id appear in both columns and payload_json); the benefit is verification cannot drift out of sync with the writer's canonical-subset schema if it ever changes."
  - "Per-user asyncio.Lock dict rather than a single module-level Lock. The plan's <implementation> block showed a single Lock but the critical_implementation_notes called for per-user locks; we chose per-user so independent users (alice in Slack handler, bob in APScheduler job — both inside the same event loop in Plan 01-09) don't block each other's audit writes. The registry-creation step is itself guarded by a _registry_lock to prevent a setdefault race on first use."
  - "Decimal normalization is the CALLER's responsibility, not canonical_json's. Per RESEARCH §Pitfall 6, normalize_decimals lives as a separate helper; canonical_json itself does NOT call normalize() on Decimals because that would silently mutate caller-visible payload shape (e.g., a $1.230 quote would become $1.23 in the audit log even if the caller wanted the trailing-zero precision preserved for downstream reasoning). normalize_decimals returns a fresh structure (no input mutation) for the same reason — keeps the original payload intact for Slack/dashboard rendering."
  - "normalize_decimals uses `+payload.normalize()` (unary plus on the normalized value) rather than `payload.normalize()` directly. Python's docs note that Decimal('0').normalize() yields Decimal('0E+0'), which would canonicalize as '0E+0' under str()  — visually surprising. The unary plus re-applies the current decimal context, collapsing 0E+0 back to plain '0' so the canonical JSON form is human-readable."
  - "walk_chain advances expected_prev = row.row_hash even when a break is detected, so the caller gets the COMPLETE list of broken rows for forensic analysis. The alternative (stop on first break) would mask tampered tails after a deliberate forge-then-reseal attack on a middle row. Per-row indexing is naturally preserved because we never skip rows."
  - "GENESIS_PREV_HASH locked at '0' * 64 (lowercase). Per CONTEXT.md Claude's Discretion A11, this was the planner's decision. We picked lowercase to match Python's `hashlib.sha256(...).hexdigest()` output (which is always lowercase) so equality comparisons against future row_hashes don't need a .lower() coercion."
  - "Test fixture seeds Users + Strategy in dependency order with an explicit s.flush() between add_all(users) and s.add(strategy). SQLAlchemy's unit-of-work flush is breadth-first, not dependency-ordered, so adding a Strategy with a FK to User in the same flush as the User itself can fail the FK constraint depending on insert order. The explicit flush is the cheap correct fix."
patterns-established:
  - "Pattern: canonical-subset lock-in — payload_json IS the canonical JSON string. Future audit modules (proposals.payload_json in Plan 01-06, strategies.payload_json in Plan 01-06) can reuse canonical_json directly without inventing a different serialization."
  - "Pattern: caller-side Decimal normalization — every plan that writes audit events with money values MUST call normalize_decimals(payload) BEFORE append_event. Plans 01-07 and 01-08 must surface this in their own docs."
  - "Pattern: write/verify separation — append_event NEVER raises AuditChainBroken. AuditChainBroken (from gekko.core.errors) is for walk_chain callers and the CLI to raise; the write path is intentionally tolerant so a single corrupted row doesn't block all future audit writes."
  - "Pattern: per-user lock registry — same shape can be reused for other per-user single-writer invariants (broker idempotency mutex in Plan 01-08, APScheduler trigger debounce in Plan 01-09)."
requirements-completed:
  - AUDT-01
  - AUDT-02
metrics:
  duration_minutes: 45
  completed: "2026-06-08T19:30:00Z"
---

# Phase 01 Plan 04: Audit Chain (canonical_json + append_event + walk_chain) Summary

**Append-only SHA-256 hash chain for the events table, locked-in canonical-subset shape `{event_type, payload, ts, user_id}` stored as the literal payload_json column so verification is a one-liner re-hash; per-user asyncio.Lock serializes concurrent appends; walk_chain detects payload tampering, prev_hash forging, deleted rows, and cross-user contamination — closing AUDT-01 and AUDT-02 with 36 unit tests.**

## Performance

- **Duration:** ~45 min (~18:50 → ~19:30 UTC)
- **Tasks:** 3 TDD pairs = 6 task commits (test → feat for each module)
- **Files created:** 6 (3 src/gekko/audit, 3 tests/unit)
- **Files modified:** 1 (src/gekko/audit/__init__.py)
- **Tests added:** 36 (19 canonical + 10 chain + 7 verify); test suite now 101 unit + 3 integration

## Accomplishments

- **AUDT-01 closed.** Every D-14 event_type (`decision`, `proposal`, `approval`, `rejection`, `order_submitted`, `fill`, `kill_switch`, `cap_rejection`, `error`) can now be written via the single `append_event` chokepoint. Plans 01-07 / 01-08 / 01-09 wire the concrete `event_type` writes; the chain integrity is solved here.
- **AUDT-02 closed.** Every row carries actor (`user_id`), action (`event_type`), inputs+outputs+rationale (the full canonical-subset string in `payload_json`), and a `row_hash` chained to the previous row's `row_hash` via the `prev_hash` column. The chain holds across:
  - Tampered `payload_json` on any middle row (walk_chain detects)
  - Forged `prev_hash` to break a row's link (walk_chain detects)
  - Deleted middle row (walk_chain detects via the next-row prev_hash mismatch)
  - Cross-user contamination (the chain is `WHERE user_id = :uid` scoped, so alice's tampered events are invisible to bob's verify call and vice versa)
- **Concurrent appends serialize per-user.** A 50-task `asyncio.gather` test confirms the per-user `asyncio.Lock` serializes correctly: each of 50 concurrent appends for the same user_id reads the prev_hash, computes the row_hash, and inserts atomically — the resulting chain is intact end-to-end.
- **Decimal normalization documented + helper exposed.** `normalize_decimals` is the caller-side pre-step every money-handling plan must use. Plans 01-07 and 01-08 must surface this in their own SUMMARYs.
- **All gates green:** 101 unit tests + 3 integration tests = 104 total pass; ruff + mypy --strict clean across 26 source files (4 in `gekko.audit`).

## Task Commits

Three TDD pairs (RED commit then GREEN commit per the plan's `tdd="true"` invariant):

1. **Task 1 RED** — failing canonical tests (19) — `e524689` (test)
2. **Task 1 GREEN** — `canonical.py` + `normalize_decimals` + GENESIS_PREV_HASH — `a890c71` (feat)
3. **Task 2 RED** — failing append_event tests (10) — `b85668d` (test)
4. **Task 2 GREEN** — `log.py` + per-user `asyncio.Lock` registry — `df3f24c` (feat)
5. **Task 3 RED** — failing walk_chain tests (7) — `525b845` (test)
6. **Task 3 GREEN** — `verify.py` integrity verifier — `b441c8d` (feat)

## Files Created (6)

### Source layer (3)

- `src/gekko/audit/canonical.py` — `canonical_json` (sort_keys, no whitespace, ensure_ascii, default=str), `normalize_decimals` (recursive Decimal walker), `GENESIS_PREV_HASH = "0" * 64`
- `src/gekko/audit/log.py` — `append_event` with per-user `asyncio.Lock` registry, canonical-subset `{event_type, payload, ts, user_id}` hashed via `sha256(prev_hash.encode("ascii") + canonical.encode("utf-8"))`
- `src/gekko/audit/verify.py` — `walk_chain` read-only integrity verifier returning `list[int]` of broken row ids (`[]` = intact)

### Tests (3)

- `tests/unit/test_audit_canonical.py` — 19 tests covering behaviors 1-8 plus determinism / non-mutation / parametrized normalization cases
- `tests/unit/test_audit_chain.py` — 10 tests covering behaviors 9-17 including 50-task concurrent-append chain integrity
- `tests/unit/test_audit_verify.py` — 7 tests covering behaviors 18-24 including payload tamper / prev_hash forge / deleted-row / read-only / user-scoped detection

## Files Modified (1)

- `src/gekko/audit/__init__.py` — re-exports `GENESIS_PREV_HASH`, `canonical_json`, `normalize_decimals`, `append_event`, `walk_chain` so callers can write `from gekko.audit import append_event` directly.

## Plan `<output>` block answers

The plan asked the executor to record three things in this SUMMARY:

1. **Confirmed canonical-subset shape.** `{event_type, payload, ts, user_id}` is what's locked. This is the EXACT key-set that ends up inside `payload_json` — sorted by `canonical_json` at every dict level. Future plans can ADD keys to the inner `payload` dict without breaking the chain (because `canonical_json` sort_keys handles new keys), but they CANNOT add/remove keys at the canonical-subset level (event_type / payload / ts / user_id) without coordinated migration.

2. **`payload_json` stores the FULL canonical-subset string, NOT the inner payload dict alone.** Per RESEARCH §Pattern 3 the choice was a planner decision; we chose the full string. Tradeoff: ~30-50 bytes per row of duplicate data (event_type, ts, user_id are also in their own columns) in exchange for verify being a one-liner `sha256(prev_hash + row.payload_json)`. Verification cannot silently drift out of sync with the writer's canonical-subset schema.

3. **Decimal-normalization policy.** Callers of `append_event` MUST call `normalize_decimals(payload)` first if the payload contains `Decimal` values. The canonical JSON serializer deliberately does NOT normalize (so it never mutates caller-visible payload shape — the canonical_json function is a one-shot pure serializer, never a mutator). Plans 01-07 (TradeProposal qty is Decimal per D-20) and 01-08 (fill price is Decimal) MUST surface this in their own SUMMARYs.

## Decisions Made

See frontmatter `key-decisions`. The three most consequential:

1. **payload_json IS the canonical-subset string** — Pattern 3 lock-in. Verifies are one-liners; the cost is small and the safety invariant is large.
2. **Per-user `asyncio.Lock` dict instead of a single module-level Lock** — Independent users append in parallel in Plan 01-09's APScheduler + Slack handler shared event loop. Registry-creation race protected by a separate `_registry_lock`.
3. **Decimal normalization is the caller's job, not `canonical_json`'s** — Canonical serializer is a pure function; normalization is an explicit pre-step. Returns a fresh structure so callers retain the original (un-normalized) payload for Slack/dashboard rendering.

## Deviations from Plan

### Auto-fixed during execution

**1. [Rule 3 — Blocker] Seeded a Strategy row in the test session fixture in dependency order.**

- **Found during:** Task 2 GREEN initial run (test_payload_json_is_canonical_subset_string_not_inner_payload)
- **Issue:** The audit-chain test fixture pre-seeded `User(alice)` and `User(bob)` but the behavior-13/14 tests pass `strategy_id="strat-abc"` to `append_event`, which has an FK to `strategies.strategy_id`. Without seeding the Strategy row, the Event insert raised `IntegrityError: FOREIGN KEY constraint failed`. SQLAlchemy's unit-of-work flush is breadth-first not dependency-ordered, so even adding both `User` + `Strategy` in the same `add_all` failed because the Strategy was flushed before its parent User.
- **Fix:** Used `s.add_all([User(...), User(...)])` followed by an explicit `await s.flush()` before `s.add(Strategy(...))`. The explicit flush guarantees the FK target exists when the child Strategy row is checked.
- **Files modified:** `tests/unit/test_audit_chain.py`
- **Verification:** All 10 chain tests pass; FK constraints fire correctly for genuine FK violations elsewhere.
- **Committed in:** `df3f24c` (Task 2 GREEN commit)

**2. [Rule 1 — Lint] Auto-fixed I001 (import sort) on test_audit_canonical.py.**

- **Found during:** Task 1 GREEN verification (`ruff check`)
- **Issue:** Import block ordering was off (ruff's isort rules).
- **Fix:** `ruff check --fix` auto-applied; re-ran tests, still 19/19 passing.
- **Files modified:** `tests/unit/test_audit_canonical.py` (import block reflowed)
- **Verification:** `ruff check src/gekko/audit/ tests/unit/test_audit_canonical.py` clean.
- **Committed in:** `a890c71` (Task 1 GREEN commit)

---

**Total deviations:** 2 auto-fixed (1 test-fixture FK ordering, 1 lint)
**Impact on plan:** No scope creep. The FK fix is a test-only adjustment to satisfy the existing `Event.strategy_id` FK; the lint auto-fix is a no-op for behavior.

## Issues Encountered

None outside the auto-fixed deviations above. The plan body's `<implementation>` block showed a single module-level `asyncio.Lock()` while the executor prompt's `critical_implementation_notes` section called for per-user locks via `dict[user_id, asyncio.Lock]`. We followed the executor-prompt guidance because (a) it's more specific to the production scenario (Plan 01-09 will run APScheduler + Slack + dashboard in the same event loop), and (b) the single-Lock approach is a strict subset of the per-user approach (both pass the same concurrent-append test).

The plan and the critical_implementation_notes also differed slightly on `walk_chain`'s return shape — the plan body specified `list[int]` of broken row ids while the prompt notes mentioned a `WalkResult(ok, broken_at, reason)` dataclass. We picked `list[int]` because (a) every test behavior (18-24) explicitly asserts list-of-ids semantics ("walk_chain returns `[3]`"), and (b) `list[int]` is the simpler API for the CLI exit-code mapping in Plan 01-09 (`exit 1 if walk_chain(...)`).

## Known Stubs

None goal-blocking. Three intentional Wave 1 → Wave 2+ deepening points:

- **`walk_chain` returns all broken rows but does not distinguish broken-payload vs broken-prev_hash vs deleted-predecessor.** Forensic disambiguation is a `gekko audit verify --detailed` Plan 01-09 polish item — the breaks list is enough for the AUDT-01/02 acceptance gate.
- **`append_event` does not enforce caller-side Decimal normalization** — it documents the requirement in the docstring (and SUMMARY) and trusts the caller. If a future plan ships a money-handling caller that forgets `normalize_decimals`, the chain is still byte-stable but two numerically-equal Decimals could hash differently. A future Plan 01-07 / 01-08 review or lint rule can catch this; we did not invest in a runtime check because (a) the caller-side fix is one line and (b) the runtime overhead of a recursive Decimal scan on every append is not free.
- **Cross-process concurrency** is NOT a P1 concern (D-18 single-process modular monolith). If Phase 7's supervisor design later requires multi-process audit writes, the per-user `asyncio.Lock` becomes insufficient — an `fcntl.flock(db_path)` or SQLite advisory lock would replace it. Documented in the module docstring.

## Self-Check: PASSED

Files verified present:

- `src/gekko/audit/canonical.py` — FOUND
- `src/gekko/audit/log.py` — FOUND
- `src/gekko/audit/verify.py` — FOUND
- `src/gekko/audit/__init__.py` — FOUND (modified)
- `tests/unit/test_audit_canonical.py` — FOUND
- `tests/unit/test_audit_chain.py` — FOUND
- `tests/unit/test_audit_verify.py` — FOUND

Commits verified in git log:

- `e524689` — FOUND (Task 1 RED)
- `a890c71` — FOUND (Task 1 GREEN)
- `b85668d` — FOUND (Task 2 RED)
- `df3f24c` — FOUND (Task 2 GREEN)
- `525b845` — FOUND (Task 3 RED)
- `b441c8d` — FOUND (Task 3 GREEN)

Test gates verified green:

- [x] `uv run pytest tests/unit -q` → 101 passed (65 prior + 36 new audit)
- [x] `uv run pytest tests/integration -m integration -q` → 3 passed (no regression)
- [x] `uv run pytest tests/unit/test_audit_canonical.py tests/unit/test_audit_chain.py tests/unit/test_audit_verify.py --no-header` → 36 passed in 5.10s
- [x] `uv run ruff check src/gekko/audit/ tests/unit/test_audit_*.py` → All checks passed
- [x] `uv run mypy src/gekko/audit/` → Success: no issues found in 4 source files
- [x] AUDT-01 closed (append-only audit writer for all D-14 event_types)
- [x] AUDT-02 closed (actor + action + inputs/outputs/rationale + row_hash chained to prev_hash)

## TDD Gate Compliance

All three task pairs followed the strict RED → GREEN sequence:

| Task | RED commit | RED state | GREEN commit | GREEN state |
| ---- | ---------- | --------- | ------------ | ----------- |
| 1 (canonical) | `e524689` test | ModuleNotFoundError on import | `a890c71` feat | 19/19 pass |
| 2 (chain) | `b85668d` test | ModuleNotFoundError on import | `df3f24c` feat | 10/10 pass |
| 3 (verify) | `525b845` test | ModuleNotFoundError on import | `b441c8d` feat | 7/7 pass |

No GREEN commit landed before its RED counterpart.

## Next Plan Readiness

Plan 01-05 (AlpacaBroker) and Plan 01-06 (schemas) are unblocked by Plan 01-04 indirectly — they don't import from `gekko.audit` themselves, but Plan 01-07 (agent runtime) and Plan 01-08 (Slack/approval) both do, and those plans are now load-bearing-clear:

- `from gekko.audit import append_event, normalize_decimals` is the canonical caller pattern.
- Plan 01-07's Proposal Writer should call `normalize_decimals(proposal_payload)` BEFORE passing to `append_event` (proposal qty + price are Decimals per D-20).
- Plan 01-08's Slack action handler can call `append_event(event_type="approval", payload={"slack_message_ts": ..., "approver_user_id": ...})` from inside the action callback.
- Plan 01-09's `gekko audit verify` CLI command can wrap `walk_chain(session, user_id=settings.user_id)` and exit non-zero on any breaks. A periodic APScheduler job can do the same on a daily cadence.

The canonical-subset shape `{event_type, payload, ts, user_id}` is LOCKED. Any future plan that wants to change it requires (a) a coordinated migration and (b) a documented invalidation of every existing event row's hash — i.e., it is an irreversible architectural change. Treat it as a one-shot decision the way RESEARCH §Pattern 3 said.

---
*Phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl*
*Completed: 2026-06-08*
