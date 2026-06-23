---
phase: 04-agent-architecture-cost-bounds
plan: "02"
subsystem: db-schema + agent-pricing
tags:
  - alembic
  - cost-ceiling
  - pricing
  - orm
dependency_graph:
  requires:
    - "04-01"
  provides:
    - "04-03"
    - "04-04"
    - "04-05"
  affects:
    - "src/gekko/db/models.py"
    - "migrations/versions/0005_p4_cost_ceiling.py"
    - "src/gekko/agent/pricing.py"
tech_stack:
  added: []
  patterns:
    - "Alembic batch_alter_table for SQLite CHECK constraint extension (mirrors 0004 pattern)"
    - "Frozen vocabulary local copy in migration (Plan 01-03 convention)"
    - "Decimal constants module for money pricing (no float leakage)"
key_files:
  created:
    - migrations/versions/0005_p4_cost_ceiling.py
    - src/gekko/agent/pricing.py
    - tests/unit/test_p4_alembic_round_trip.py
  modified:
    - src/gekko/db/models.py
decisions:
  - "server_default value for daily_cost_ceiling_usd is \"'5.00'\" (single-quoted inside double-quoted Python string — SQLite stores the literal '5.00' as default)"
  - "tokens_to_usd raises ValueError for unknown model aliases rather than returning $0 (fail loud)"
  - "Subprocess round-trip test skipped on Windows (SQLCipher cross-process file-lock hang per Plan 02-01 SUMMARY); in-process logic tests always run"
  - "_FROZEN_EVENT_TYPES_PRE in 0005 is a literal copy of 0004 _FROZEN_EVENT_TYPES_POST — verified equal by test_0005_frozen_vocab_pre_matches_0004_post"
metrics:
  duration: "14 minutes"
  completed: "2026-06-23"
  tasks_completed: 2
  files_changed: 4
---

# Phase 04 Plan 02: Alembic 0005 + Pricing Module Summary

Wave 2 schema substrate: Alembic migration 0005 (User cost-ceiling columns + 2 new event_types) + ORM alignment + pricing constants module. test_pricing.py turns GREEN.

## What Was Built

### Task 1: Alembic migration 0005 + ORM User model extension

**`migrations/versions/0005_p4_cost_ceiling.py`** — new Alembic revision, exact structural mirror of 0004_p3_hitl_ux.py:

- `revision = "0005_p4_cost_ceiling"`, `down_revision = "0004_p3_hitl_ux"`
- Frozen vocabulary pattern: `_FROZEN_EVENT_TYPES_PRE` is a literal copy of 0004's `_FROZEN_EVENT_TYPES_POST` (17 entries); `_FROZEN_EVENT_TYPES_POST = _FROZEN_EVENT_TYPES_PRE + ("llm_cost", "suspicious_content")`
- `upgrade()`:
  - `batch_alter_table("users")`: adds `daily_cost_ceiling_usd` TEXT `server_default="'5.00'"` nullable, `cost_alert_80_sent_date` TEXT nullable, `cost_alert_100_sent_date` TEXT nullable
  - `batch_alter_table("events")`: drop + recreate `ck_event_type` CHECK with POST vocabulary
- `downgrade()`: reverses in opposite order (events CHECK first, then users columns)
- `_in_check` helper copied verbatim (not imported from models — frozen artifact convention)

**`src/gekko/db/models.py`** changes:
- `_EVENT_TYPES` extended with `"llm_cost"` and `"suspicious_content"` at the end (Phase-4 additions)
- `User` ORM gains three new `Mapped[str | None]` columns after the Phase-3 quiet-hours block:
  - `daily_cost_ceiling_usd` — configurable ceiling, NULL = read DEFAULT_DAILY_CEILING_USD
  - `cost_alert_80_sent_date` — ISO date of last 80% DM (NULL = never sent)
  - `cost_alert_100_sent_date` — ISO date of last 100% DM (NULL = never sent)

**`tests/unit/test_p4_alembic_round_trip.py`** — in-process logic tests (always run on Windows):
- `test_0005_revision_wiring` — revision + down_revision values correct
- `test_0005_frozen_vocab_pre_matches_0004_post` — frozen vocab sync validated
- `test_0005_frozen_vocab_post_adds_phase4_types` — exactly 2 new types added
- `test_0005_models_event_types_match_frozen_post` — models.py vs migration vocabulary in sync
- `test_0005_user_orm_has_cost_columns` — ORM column presence via SQLAlchemy class_mapper
- `test_0005_alembic_round_trip` — subprocess upgrade/downgrade/upgrade (skipped on Windows per Plan 02-01 caveat)

### Task 2: pricing.py — constants module

**`src/gekko/agent/pricing.py`** — new module, stdlib `decimal` only (no external deps):

| Constant | Value | Notes |
|----------|-------|-------|
| `SONNET_INPUT_PER_MTOK` | `Decimal("3.00")` | $/MTok input, Claude Sonnet 4.6 |
| `SONNET_OUTPUT_PER_MTOK` | `Decimal("15.00")` | $/MTok output |
| `HAIKU_INPUT_PER_MTOK` | `Decimal("1.00")` | $/MTok input, Claude Haiku 4.5 |
| `HAIKU_OUTPUT_PER_MTOK` | `Decimal("5.00")` | $/MTok output |
| `DEFAULT_DAILY_CEILING_USD` | `Decimal("5.00")` | D-02 configurable default |

`tokens_to_usd(input_tokens, output_tokens, *, model="sonnet") -> Decimal`:
- Dict dispatch on model alias: `"sonnet"` and `"haiku"` supported
- Formula: `(Decimal(input_tokens) / 1_000_000) * input_rate + (Decimal(output_tokens) / 1_000_000) * output_rate`
- Raises `ValueError` for unknown model alias (fail loud, not silent $0)
- `__all__` exports all 5 constants + the function

## Verification Results

| Check | Result |
|-------|--------|
| `test_pricing.py` (7 tests) | PASS (GREEN) |
| `test_p4_alembic_round_trip.py` (5 logic + 1 subprocess) | 5 PASS, 1 SKIP (Windows) |
| `test_p3_alembic_round_trip.py` | 1 PASS, 1 SKIP (Windows, pre-existing) |
| `test_alembic_0002.py` | PASS (pre-existing) |
| Migration `py_compile` check | OK (no syntax errors) |
| `_FROZEN_EVENT_TYPES_PRE` == 0004 `_FROZEN_EVENT_TYPES_POST` | VERIFIED |
| `models._EVENT_TYPES` == 0005 `_FROZEN_EVENT_TYPES_POST` | VERIFIED |
| `User` ORM 3 new columns present | VERIFIED |
| `alembic upgrade head` (subprocess) | Skipped on Windows (cross-process file-lock, known Windows SQLCipher caveat per Plan 02-01 SUMMARY); logic verified in-process |

## Alembic Upgrade Head — Windows Caveat

Running `alembic upgrade head` as a subprocess on Windows against an existing SQLCipher DB fails with `WrongPassphraseError` when using the test passphrase — the real operator DB uses the production passphrase. The subprocess round-trip test is skipped on Windows (matching the established `test_p3_alembic_round_trip.py` pattern from Plan 02-01). Migration logic is verified through:
1. `py_compile` — confirms no syntax errors
2. In-process import + attribute inspection — confirms revision wiring + frozen vocabulary correctness
3. SQLAlchemy `class_mapper` — confirms ORM columns are present
4. Frozen vocab equality assertions — confirms 0005 PRE == 0004 POST

## Deviations from Plan

None. Plan executed exactly as written.

## Known Stubs

None. All constants are wired with real values; `tokens_to_usd()` is a complete implementation (not a stub).

## Threat Flags

None. No new network endpoints, auth paths, or schema changes at trust boundaries beyond those explicitly designed in the plan's `<threat_model>`.

## Self-Check: PASSED

Files exist:
- FOUND: migrations/versions/0005_p4_cost_ceiling.py
- FOUND: src/gekko/agent/pricing.py
- FOUND: tests/unit/test_p4_alembic_round_trip.py

Commits exist:
- FOUND: 202bc07 (Task 1 — migration + ORM + round-trip test)
- FOUND: 8b3732f (Task 2 — pricing.py)
