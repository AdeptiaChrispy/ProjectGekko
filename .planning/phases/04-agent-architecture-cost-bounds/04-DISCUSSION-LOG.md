# Phase 4: Agent Architecture & Cost Bounds - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-23
**Phase:** 04-agent-architecture-cost-bounds
**Areas discussed:** Daily ceiling (default + scope), 80% degradation tactics, 100% hard-halt + reset, Cost visibility (dashboard + alerts)

---

## Daily ceiling — default & scope (COST-01)

First pass — user chose "Other" with note **"let it be configurable"** (declined a fixed hardcoded value). Follow-up resolved scope + starting default:

| Option | Description | Selected |
|--------|-------------|----------|
| Per-user pooled, default $5/day, editable | One configurable ceiling per user across all strategies; ships at $5/day; matches single-tenant per-user deployment (D-18) | ✓ |
| Per-user pooled, default $10/day, editable | Same model, roomier $10/day starting default | |
| Per-strategy, default $2/strategy/day, editable | Each strategy its own budget; total = sum; no global pool | |

**User's choice:** Per-user pooled, default $5/day, editable.
**Notes:** User explicitly wants the ceiling to be **configurable** (a Settings value, not a constant). $5/day is the shipped starting default, editable anytime.

---

## 80% — graceful degradation tactics (COST-04)

| Option | Description | Selected |
|--------|-------------|----------|
| Slow cadence + Haiku triage; Decision stays full model | ×2 cadence + Haiku "is this cycle worth it" pre-gate + trimmed context; trade Decision never downgraded | ✓ |
| Slow cadence only — no model changes | Just space runs out; never touch models/context | |
| Full degradation incl. Haiku Decision | Max savings; Decision agent itself drops to Haiku (quality risk) | |

**User's choice:** Slow cadence + Haiku triage; Decision stays full model.
**Notes:** Hard line — the real-money trade Decision must never run on a cheaper model. Haiku is triage-only.

---

## 100% — hard halt & reset (COST-04)

| Option | Description | Selected |
|--------|-------------|----------|
| Skip runs, DM once, reset at tz-midnight; raise ceiling in Settings to resume early | Absolute halt; scheduled cycles skipped (not queued); only early resume is editing the Settings ceiling | ✓ |
| Skip + queue next cycle to fire at reset | Same halt but next due cycle queued for post-reset | |
| Explicit one-day 'top-up' button on dashboard | Deliberate mid-day extend-ceiling action | |

**User's choice:** Skip runs, DM once, reset at tz-midnight; raise ceiling in Settings to resume early.
**Notes:** Halt is honest/absolute; no hidden override or top-up. Reuses the Phase-3 timezone setting for the midnight reset.

---

## Cost visibility — dashboard & alerts (COST-02/03/05)

| Option | Description | Selected |
|--------|-------------|----------|
| Daily total vs ceiling + per-strategy breakdown + 7-day history | Satisfies SC-5 + light trend; alerts 80%/100% | ✓ |
| Daily total vs ceiling only (minimal) | Smallest "ceiling visible" surface | |
| Add Researcher-vs-Decision split per cycle | Recommended view + per-subagent cost attribution | |

**User's choice:** Daily total vs ceiling + per-strategy breakdown + 7-day history.
**Notes:** Slack alerts at 80%/100% only (per SC-4) — not more granular.

---

## Claude's Discretion

- Cost-ledger storage shape (events event_type vs dedicated table).
- "Cadence ×2" mechanism + exact placement of the degradation/halt guard in the run pipeline.
- USD pricing constants/source for the cost calc (per-model $/Mtok).
- Haiku pre-triage prompt + "thin cycle" definition.
- Whether SC-2 suspicious-content needs new detection or existing D-40 + OrderGuard universe rejection suffices (verify first — Plan 02-04 deferred a regex to "P4 scope").

## Deferred Ideas

- Per-strategy sub-caps on top of the per-user pool (declined for v2.0).
- Mid-day top-up/override button (declined — raise Settings ceiling instead).
- Researcher-vs-Decision per-cycle cost split on dashboard (declined for now; easy additive later).
- More granular cost alerts beyond 80%/100% (declined).
