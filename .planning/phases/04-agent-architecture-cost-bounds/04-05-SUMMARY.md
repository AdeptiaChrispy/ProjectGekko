---
phase: 04-agent-architecture-cost-bounds
plan: "05"
subsystem: dashboard
tags:
  - cost-visibility
  - spend-dashboard
  - settings
  - COST-02
  - COST-03
dependency_graph:
  requires:
    - "04-04"  # llm_cost ledger populated by runtime
    - "04-03"  # check_cost_ceiling + User.daily_cost_ceiling_usd column exist
    - "04-02"  # pricing.py DEFAULT_DAILY_CEILING_USD; migration 0005 User columns
    - "04-01"  # test stubs Wave 0
  provides:
    - GET /spend route on auth-gated router
    - spend.html.j2 template (today vs ceiling, per-strategy, 7-day history)
    - Settings form: daily_cost_ceiling_usd field (save + default)
  affects:
    - src/gekko/dashboard/routes.py
    - src/gekko/dashboard/templates/spend.html.j2
    - src/gekko/dashboard/templates/settings.html.j2
    - src/gekko/dashboard/templates/base.html.j2
tech_stack:
  added: []
  patterns:
    - "spend_get() follows approvals_poll() + settings_get() DB load pattern"
    - "Decimal sum over llm_cost event rows (Python-side, no SQL SUM on JSON text)"
    - "ZoneInfo today_start_utc_str for tz-aware daily boundary (mirrors quiet_hours.py)"
    - "Jinja2 float filter for Decimal display in templates"
    - "One-POST design: ceiling fieldset inside existing /settings form"
key_files:
  created:
    - src/gekko/dashboard/templates/spend.html.j2
  modified:
    - src/gekko/dashboard/routes.py
    - src/gekko/dashboard/templates/settings.html.j2
    - src/gekko/dashboard/templates/base.html.j2
    - tests/unit/test_spend_route.py
    - tests/unit/test_settings_route.py
decisions:
  - "Jinja2 `float` filter used on Decimal context values for display (Decimal is not JSON-serializable by Jinja2 natively; float conversion acceptable for display-only formatting)"
  - "test_spend_get_shows_today_total uses AsyncMock side_effect call_count dispatch to mock three execute() calls (user row, today rows, 7-day rows) in a single session context"
  - "test_ceiling_saved validates user.daily_cost_ceiling_usd assignment via MagicMock attribute set, confirming normalization (str(Decimal) result)"
  - "Pre-existing test_handle_edit_size_stub_acks_and_opens_modal failure (test_approval_proposals.py) confirmed pre-existing via git stash; deselected from suite run"
metrics:
  duration: "17 minutes"
  completed: "2026-06-23"
  tasks: 2
  files: 6
---

# Phase 04 Plan 05: Spend Dashboard + Settings Ceiling Field Summary

**One-liner:** GET /spend route (today-vs-ceiling progress bar + per-strategy table + 7-day history) and configurable daily ceiling fieldset in the Settings form, closing COST-02 and COST-03.

## What Was Built

### Task 1: GET /spend route + spend.html.j2 (COST-02)

**Route (`spend_get` in routes.py):**
- Added on the auth-gated `router` (`Depends(require_session)`) — unauthenticated → 302 to `/login`
- Loads `User` row for ceiling and timezone; falls back to `DEFAULT_DAILY_CEILING_USD = Decimal("5.00")` and `"America/New_York"` when NULL
- Computes `today_start_utc_str` via `ZoneInfo` (mirrors `quiet_hours.py` pattern — D-03 correct tz-midnight boundary)
- Fetches `llm_cost` events for today; sums `payload["cost_usd"]` as `Decimal` in Python (avoids SQLite JSON text SUM pitfall per RESEARCH §Pitfall 7)
- Groups by `strategy_name` from payload (D-11 per-strategy breakdown)
- Builds 7-day history by fetching events for `today_start - 7d` window, bucketing by local date via `datetime.fromisoformat(ts).astimezone(tz).date().isoformat()`
- Fills all 7 days (including zero-spend days) to guarantee 7 entries
- Computes `pct = (today_total / ceiling * 100).quantize(...)` as Decimal

**Template (`spend.html.j2`):**
- Extends `base.html.j2` (no external scripts, no inline handlers — CSP/SRI safe)
- Today's spend section: `$X.XX of $X.XX ceiling (Y.Z%)` + HTML progress bar color-coded (green / amber / red at 80%/100%)
- Per-strategy table: Strategy | Spend Today columns; shows "No LLM costs logged today." when empty
- 7-day history table: Date | Spend columns
- Uses `"%.2f"|format(value|float)` Jinja2 pattern for Decimal display

**Navigation (`base.html.j2`):**
- Added `Spend` and `Settings` nav links to header navigation bar

**Tests (`test_spend_route.py`):** 6 tests (previously NotImplementedError stubs), all GREEN:
- `test_spend_get_returns_200` — 200 with authenticated session
- `test_spend_get_shows_today_total` — two rows summed correctly shown in response
- `test_spend_get_shows_ceiling` — user ceiling value (10.00) shown in response
- `test_spend_get_per_strategy_breakdown` — strategy names appear in response
- `test_spend_get_7day_history` — 7-day history section header present
- `test_spend_get_requires_auth` — 302 to /login for unauthenticated request

### Task 2: Settings ceiling field (COST-03)

**`settings_get` extension:**
- Passes `ceiling_value = user.daily_cost_ceiling_usd or "5.00"` to template context

**`settings_post` extension:**
- Added `daily_cost_ceiling_usd: str = Form("5.00")` parameter
- Validates as `Decimal`: catches `InvalidOperation` → error; catches `<= 0` → error (T-04-15)
- Stores as `str(Decimal(value))` — normalized form prevents "05.00" / "1e2" style storage
- Passes `ceiling_value` on both success and error re-renders

**`settings.html.j2` extension:**
- Added `<fieldset>` for "Daily LLM Cost Ceiling" INSIDE the existing `<form method="POST" action="/settings">`
- `<input type="number" step=0.01 min=0.50>` with help text (D-02 default $5.00/day, 80% degraded, 100% halt)
- One-POST design: ceiling saves alongside timezone/quiet-hours in the same form submit

**Tests (`test_settings_route.py`):** 2 tests (previously NotImplementedError stubs), all GREEN:
- `test_ceiling_saved` — POST saves ceiling, user row updated, response shows success
- `test_ceiling_defaults_to_5` — NULL user row → GET shows "5.00" + "Daily LLM Cost Ceiling"

## Deviations from Plan

### Auto-fixed Issues

None — plan executed as written.

### Implementation Notes

**Jinja2 Decimal display:** The Jinja2 template engine cannot format `decimal.Decimal` with `"%.2f"|format()` directly because `format()` requires a float. Used `value|float` coercion inside the template filter. This is display-only; all route logic uses `Decimal` throughout. (D-10 money math in Decimal is honored in route code; float is only used at the Jinja2 render boundary.)

**Test mock dispatch for 3 execute() calls:** `spend_get` calls `session.execute()` three times (user row, today's rows, 7-day rows) within a single `async with sf() as session` context. The test uses a `call_count` counter in a closure as the `side_effect` to dispatch different mock results per call.

**Pre-existing test failure:** `test_handle_edit_size_stub_acks_and_opens_modal` (test_approval_proposals.py) was failing before this plan — confirmed via `git stash` → test still fails → confirms pre-existing. Not caused by or related to Plan 04-05 changes. Deselected from the full suite run.

## Known Stubs

None — the two stub test files (test_spend_route.py and test_settings_route.py) have been fully implemented. No placeholder data flows to UI rendering; the spend page reads real llm_cost events from the DB.

## Threat Flags

None — no new network endpoints, auth paths, or schema changes beyond what was already covered by the plan's threat model. T-04-14 (user_id scoping) and T-04-15 (Decimal validation) are implemented.

## Self-Check

Checking created files exist:

- `src/gekko/dashboard/templates/spend.html.j2` — created ✓
- `src/gekko/dashboard/routes.py` — modified (spend_get + settings extensions) ✓
- `src/gekko/dashboard/templates/settings.html.j2` — modified (ceiling fieldset) ✓
- `src/gekko/dashboard/templates/base.html.j2` — modified (Spend nav link) ✓

Commits verified:
- `d221731` — feat(04-05): GET /spend route + spend.html.j2 template (COST-02)
- `6cd28e0` — feat(04-05): settings ceiling field + validation (COST-03)
