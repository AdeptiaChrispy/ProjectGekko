# Phase 4: Agent Architecture & Cost Bounds - Context

**Gathered:** 2026-06-23
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 4 delivers two things:

1. **Formalize / complete the agent architecture** — the Researcher (read-only tools, zero order/credential access) ↔ Decision (structured-brief-only, tool-use schema enforcement) separation, prompt-injection defense (source allowlist + `<untrusted_content>` delimiters + suspicious-content logging), and bounded research turns that prevent autoregressive drift into a trade. **Most of this already exists** from Phase 1 (the two-`query()` split, `RESEARCHER_TOOLS`/`DECISION_TOOLS`, `BudgetTracker`, `no_action` as a first-class output) and Phase 2 Plan 02-04 (`WEB_ALLOWLIST`, `<untrusted_content>` wrapping, D-40 warnings, directory-wide AST isolation gate). Phase 4 verifies these satisfy SC-1/SC-2/SC-3 and closes any gaps (e.g., the suspicious-content **event** in SC-2, the SDK `ResultMessage.usage` token refinement noted in `budget.py`).

2. **Add the two-tier daily LLM-cost ceiling (the net-new work, COST-01…05)** — a per-user pooled daily dollar ceiling the agent cannot talk past: 80% → graceful degradation, 100% → hard halt, reset at user-timezone midnight, with a per-LLM-call cost ledger and a dashboard spend surface.

**In scope:** research/decision separation verification + gap-closure; prompt-injection neutralization + suspicious-content event; bounded research turns / no_action-on-thin-evidence; per-user daily cost ceiling (config + ledger + 80%/100% tiers + reset); dashboard spend view + Slack alerts.

**Out of scope (other phases):** Trust Ladder / auto-execution promotion (Phase 5); per-strategy promotion gates; portfolio-level caps; capital-scaling rungs; additional brokers; web-dashboard multi-user auth (Phase 6).

</domain>

<decisions>
## Implementation Decisions

### Daily cost ceiling — scope & default (COST-01)
- **D-01:** The daily LLM-cost ceiling is **per-user, single pooled** — one ceiling covers ALL of that user's strategies combined (not per-strategy). Matches the single-tenant per-user deployment model (Phase-1 D-18). One number, one ledger to reason about.
- **D-02:** The ceiling is **user-configurable in Settings** (any value) and **ships defaulting to $5.00/day**. The default is a starting point, not a hardcoded constant — it must be editable at runtime via the dashboard Settings surface (alongside the existing quiet-hours settings from Phase 3). Tier thresholds derive from it: 80% = $4.00, 100% = $5.00 at the default.
- **D-03:** Reset is at **the user's configured timezone midnight** (reuse the same `timezone` user setting Phase 3 introduced for quiet hours — do NOT introduce a second timezone field).

### 80% — graceful degradation tactics (COST-04)
- **D-04:** At 80% of the ceiling the agent enters degradation mode with three escalating tactics: **(1) slow cadence (~2× longer between scheduled runs), (2) a cheap Haiku pre-triage gate** ("is this cycle even worth a full research run?" — skip thin cycles), and **(3) trimmed research context** (shorter context window into the Researcher).
- **D-05:** **The Decision (trade) agent NEVER drops to a cheaper model.** Real-money trade decisions must not ride on Haiku. Haiku is used ONLY for the disposable pre-triage gate, never for the actual `propose_trade`/`propose_no_action` Decision call. This is a hard safety boundary, not a tunable.
- **D-06:** Entering 80% degradation sends **one Slack DM** to the operator (per SC-4).

### 100% — hard halt & reset (COST-04)
- **D-07:** At 100% the agent **hard-halts all further LLM calls**. Scheduled cycles are **skipped, NOT queued** — a missed cycle simply does not run (swing-horizon strategies tolerate a missed day; this is the deliberate safe default).
- **D-08:** **One Slack DM** at 100% (per SC-4). No repeat spam for subsequent skipped cycles in the same window.
- **D-09:** Halt is **absolute until the timezone-midnight reset.** The ONLY early-resume path is **raising the ceiling in Settings** (editing the config value, which re-opens headroom) — there is no separate "override"/"top-up" button and no mid-day bypass action. Keeps the halt honest and the surface minimal.

### Cost visibility — dashboard & alerts (COST-02/03/05)
- **D-10:** Every LLM call is logged to a **cost ledger**: input tokens, output tokens, USD. This is the per-call source of truth for spend (per SC-5). Token figures should move from the current flat per-tool estimates toward the SDK's actual `ResultMessage.usage` (see `budget.py` note / `docs/sdk-shape.md` delta #6) — researcher to confirm feasibility.
- **D-11:** The dashboard **Spend** surface shows: **today's running total vs the ceiling** (with the ceiling visible), a **per-strategy breakdown** of the day's spend, and a **7-day history** (small trend so the operator sees if they're routinely near the cap). Satisfies SC-5 (per-strategy + per-user spend, ceiling visible) plus a light trend.
- **D-12:** Slack cost alerts fire at **80% and 100% only** (per SC-4) — not more granular.

### Claude's Discretion
- Cost-ledger storage shape (new `events` event_type vs a dedicated table) — planner/researcher decide, consistent with the Phase-1 append-only audit model and Decimal money math.
- Exact "cadence ×2" mechanism (APScheduler reschedule vs skip-N) and where the degradation/halt check is enforced in the run pipeline (likely a guard in `trigger_strategy_run` before the first `query()`).
- USD pricing source/constants for the cost calc (per-model input/output $/Mtok) — researcher to source current Sonnet/Haiku pricing; must be a maintainable constant, not scattered.
- How the Haiku pre-triage gate is prompted and what makes a cycle "thin" enough to skip.
- Whether SC-2's suspicious-content detection needs a new regex/heuristic or the existing D-40 prompt-boundary defense + OrderGuard universe rejection already satisfies it (verify first — Plan 02-04 explicitly deferred a suspicious-content regex + `injected_content_flags` to "P4 scope").

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 4 scope & success criteria
- `.planning/ROADMAP.md` §"Phase 4: Agent Architecture & Cost Bounds" — Goal + 5 success criteria (COST-01…05). The authoritative requirement source (there is no REQUIREMENTS.md in this repo).
- `.planning/PROJECT.md` §Requirements (Active v2.0) — "Agent architecture" + "Two-tier cost ceiling" bullets; §Constraints "Cost" bullet (configurable daily ceiling, graceful degradation).

### Existing agent architecture (already built — verify & extend, don't rebuild)
- `src/gekko/agent/runtime.py` — `trigger_strategy_run` orchestrates the two-`query()` Researcher→Decision flow; constructs `BudgetTracker`; emits `agent.run.complete` with `budget.to_dict()`. The cost-ceiling guard belongs in this pipeline.
- `src/gekko/agent/researcher.py` — `RESEARCHER_TOOLS` (read-only tool allowlist), Researcher system prompt.
- `src/gekko/agent/decision.py` — `DECISION_TOOLS`, Decision system prompt incl. the `<untrusted_content>` / D-40 trust-boundary block (SC-1/SC-2 evidence).
- `src/gekko/agent/budget.py` — `BudgetTracker` (per-cycle 12 calls / 8K tokens / 60s soft + 2× hard halt; flat token estimates). Explicitly flags Phase-4 refinement to `ResultMessage.usage`. This is the per-cycle budget; the daily ceiling is the NEW orthogonal layer.
- `src/gekko/agent/tools/` — `web_fetch.py` (`WEB_ALLOWLIST` + `<untrusted_content>` wrap), `finnhub_news.py` (news wrap), `alpaca_data.py`, `edgar.py`, `propose_trade.py`, `propose_no_action.py`.

### Locked decisions to honor (prior phases)
- `.planning/phases/02-orderguard-real-money-alpaca-live/02-CONTEXT.md` — D-39 (three trust tiers / allowlist) and D-40 (verbatim Decision-prompt warning); Plan 02-04 SUMMARY explicitly deferred suspicious-content regex + `injected_content_flags` + per-user allowlist override to **P4 scope**.
- `.planning/phases/01-foundation-vertical-slice-alpaca-paper-slack-hitl/01-CONTEXT.md` — D-13 (per-cycle research budget soft+2× grace), D-18 (single-tenant per-user runtime — basis for per-user pooled ceiling), Researcher/Decision split decisions.
- `docs/sdk-shape.md` — delta #6 (real `ResultMessage.usage` token accounting), the authoritative claude-agent-sdk 0.2.93 reference.

### Settings surface to extend (Phase 3)
- `src/gekko/dashboard/routes.py` §`settings_get`/`settings_post` + `settings.html.j2` — existing quiet-hours + timezone settings form; the configurable daily ceiling field and the timezone reuse (D-03) land here.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `BudgetTracker` (`agent/budget.py`): the per-cycle guard pattern (soft warn + hard raise) is the model for the daily ceiling's tier checks, but the daily ceiling is a separate, persistent, cross-cycle accumulator — not an extension of this per-cycle object.
- `agent.run.complete` structlog event already emits `budget.to_dict()` per cycle — a natural hook point to also write the cost-ledger entry.
- Phase-3 Settings form (timezone + quiet-hours) — the ceiling config field and the existing `timezone` for the midnight reset plug in here (no new timezone field per D-03).
- Append-only `events` audit table + `normalize_decimals` Decimal pipeline — the cost ledger should follow the same money-math discipline (USD as Decimal).
- Slack DM path (`_send_slack_dm` / quiet-hours-aware sender from Phase 3) — reuse for the 80%/100% cost alerts; note cost-halt alerts are operator-safety-adjacent (consider whether they bypass quiet hours like kill/cap_rejection do).

### Established Patterns
- Researcher/Decision isolation is enforced structurally (distinct `allowed_tools`, separate `query()` calls, no shared raw transcript) AND by an AST-walk test gate (`test_decision_prompt_isolation.py`) — any Phase-4 change near the Decision boundary must keep that gate green.
- `place_order` / live-construction AST grep gates — the project's idiom for locking a safety invariant in a test. A "Decision agent never runs on Haiku" invariant (D-05) is a strong candidate for the same AST-gate treatment.
- Deterministic guards run BEFORE the LLM (OrderGuard, market-hours) — the cost-ceiling check should likewise be a deterministic pre-`query()` gate the LLM cannot reach or reason past.

### Integration Points
- `trigger_strategy_run` (pre-first-`query()`): daily-ceiling check → allow / degrade / halt.
- Scheduler (`scheduler/jobs.py`): cadence ×2 in degradation mode; skip (not queue) when halted.
- Dashboard: new Spend route + template; Settings: ceiling field.
- Cost ledger write: at/after each `query()` (or at `agent.run.complete`).

</code_context>

<specifics>
## Specific Ideas

- Hard safety line the user cares about: **the trade Decision never degrades to a cheaper model** (D-05) — Haiku is triage-only. Treat as an invariant worth an AST/test gate, not a config knob.
- Halt must be **honest**: skipped-not-queued, one DM, and the only early resume is raising the ceiling in Settings (D-07/D-08/D-09). No hidden override.
- Ceiling is a **setting with a $5/day starting default** (D-02) — the value is configurable; the planner should not bake $5 in as a constant anywhere except the default.

</specifics>

<deferred>
## Deferred Ideas

- Per-strategy sub-caps on top of the per-user pool — considered and declined for v2.0 (D-01 chose per-user pooled). Revisit if a single user runs many strategies and one starves the others.
- Mid-day "top-up"/override button — declined (D-09); raising the Settings ceiling is the resume path. Could revisit if the absolute halt proves too blunt in practice.
- Researcher-vs-Decision per-cycle cost split on the dashboard — considered for the Spend view; declined for now in favor of per-strategy + 7-day history (D-11). Easy additive enhancement later.
- More granular cost alerts (e.g., 50%/90%) — declined; 80%/100% only (D-12).

</deferred>

---

*Phase: 04-agent-architecture-cost-bounds*
*Context gathered: 2026-06-23*
