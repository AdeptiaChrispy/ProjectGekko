# Phase 3: Production HITL UX (Slack Block Kit + Dashboard Fallback) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-17
**Phase:** 3-Production HITL UX (Slack Block Kit + Dashboard Fallback)
**Areas discussed:** Idempotency mechanism, Quiet hours behavior, Timeout/expiry mechanic, Edit-size + Dashboard /approvals

---

## Idempotency Mechanism (HITL-02)

### Q1 — Where to enforce idempotency

| Option | Description | Selected |
|--------|-------------|----------|
| Both: Slack-action dedup table + state-machine CHECK | Insert (proposal_id, action_id, actor_id) into a dedup table at handler entry; state-machine CHECK is secondary guard. Makes "already handled" an explicit branch. | ✓ |
| State-machine CHECK alone | Rely on existing PENDING→APPROVED transition guard + deterministic client_order_id. Cheaper, but couples idempotency to error path. | |
| respond_url + X-Slack-Retry-Num only | Track Slack's retry header. Lightest; doesn't defend against genuine double-click. | |

**User's choice:** Both — dedup table + state-machine.
**Notes:** Belt-and-suspenders. Makes the "already handled" code path explicit rather than translating a state-transition error into UX.

### Q2 — Dedup key composition

| Option | Description | Selected |
|--------|-------------|----------|
| (proposal_id, action_id, actor_slack_user_id) | Strongest semantic key. Allows cross-user actions; pairs with identity-split. | ✓ |
| (proposal_id, action_id) only | "One approve total per proposal". Prevents legitimate cross-user actions. | |
| Slack interaction_payload_id (trigger_id) alone | Slack's own delivery-unique ID. Defeats retries but loses semantic meaning. | |

**User's choice:** (proposal_id, action_id, actor_slack_user_id).
**Notes:** Locks identity-split awareness (slack_user_id from callback) and allows cross-user behavior. Extends to dashboard via the source column (D-56).

### Q3 — Second-click UX

| Option | Description | Selected |
|--------|-------------|----------|
| Ephemeral Slack message showing current status | Only visible to clicker; uses Slack respond_url. | ✓ |
| Silent ignore | No message; original card stays. | |
| Update proposal card to current status | Visible to all viewers; risky in shared channels. | |

**User's choice:** Ephemeral message.
**Notes:** Clear feedback, no channel noise.

### Q4 — Race policy (edit-size + approve quick succession)

| Option | Description | Selected |
|--------|-------------|----------|
| First-write-wins by INSERT timestamp | Dedup table ordering is the source of truth; predictable. | ✓ |
| Edit-size always blocks approve (intent-precedence) | Prefer edit intent; complex, Slack reordering makes "first" ambiguous. | |
| Approve always wins (action-precedence) | Risky — stale approve fires on un-edited size. | |

**User's choice:** First-write-wins.
**Notes:** Same rule applies to all races (Slack vs dashboard, sweep vs click). One audit primitive explains every race.

---

## Quiet Hours Behavior (HITL-05)

### Q1 — Suppression mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| Pause the agent loop during quiet hours | APScheduler skips research + decision cycles. Resumes on window-open with fresh market data. | ✓ |
| Queue: create proposals, defer DM, drain at window-open | Stale-proposal risk on wake. | |
| Hybrid — pause scheduler + queue manually-triggered proposals | Manual gekko run is intent; queue for window-open DM. | |

**User's choice:** Pause the loop.
**Notes:** Simplest state, no stale-proposal-on-wake. Manual gekko run overrides naturally.

### Q2 — Scope (where do quiet hours live)

| Option | Description | Selected |
|--------|-------------|----------|
| Per-user only (single window for everything) | Simplest model: one window, all strategies inherit. | |
| Per-strategy only | Granular; over-design for v1 US-equities scope. | |
| Both — per-user default + per-strategy override | Most flexible; needs merge rule. | ✓ |

**User's choice:** Both.
**Notes:** Required the follow-up Q3 to lock merge semantics.

### Q3 — Merge rule (per-user vs per-strategy)

| Option | Description | Selected |
|--------|-------------|----------|
| Union: strategy is silent if EITHER window is active | Strategy can only narrow awake-time. Safest. | |
| Strategy override wins | Only strategy window is active when set; otherwise user. Most flexible; risks 2am ping if misconfigured. | ✓ |
| Intersection: silent only when BOTH active | Most permissive, most dangerous. | |

**User's choice:** Strategy override wins.
**Notes:** Operator accepts that strategy can widen awake-time. CONTEXT.md D-47 mandates a dashboard warning when strategy is narrower than user window — operator-in-the-driver's-seat without paternalistic narrowing.

### Q4 — DM bypass categories (multi-select)

| Option | Description | Selected |
|--------|-------------|----------|
| kill_active state changes | Critical safety. | ✓ |
| Executor errors (BrokerOrderError, OrderGuardRejected, MarketClosed retries) | Silent post-approval failure is the v1.0 carry-forward problem. | ✓ |
| First-live-trade FILLS | Load-bearing real-money first trade. | ✓ |
| Daily P&L summary and routine fill confirmations | Informational, not urgent. | |

**User's choice:** kill_active + executor errors + first-live fills.
**Notes:** Daily P&L stays at fixed 4:30pm ET regardless.

### Q5 — Timezone source

| Option | Description | Selected |
|--------|-------------|----------|
| Explicit per-user IANA TZ on User row | Validated against zoneinfo; DST automatic. | ✓ |
| System TZ | Fragile, container/headless installs report UTC. | |
| UTC offsets only | DST-broken. | |

**User's choice:** IANA TZ.
**Notes:** Default 'America/New_York'. 30-min expiry timer always in UTC.

---

## Timeout/Expiry Mechanic (HITL-03)

### Q1 — Fire mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| APScheduler periodic sweep (60s, expire-where-due) | Cheap, in-stack; max 60s latency past timeout. | ✓ |
| Per-proposal one-shot APScheduler job | Tighter latency; thousands of jobs to manage. | |
| Lazy: check expires_at on next interaction | No background fire; user never sees explicit expiry signal. | |

**User's choice:** APScheduler sweep.
**Notes:** APScheduler 4.x persists jobs in SQLite; survives restart.

### Q2 — Override scope

| Option | Description | Selected |
|--------|-------------|----------|
| Global default (30 min) + per-strategy override | Strategy-level fits config shape. | ✓ |
| Global default only (fixed 30) | Inflexible. | |
| Per-proposal LLM-suggested timeout | LLM hallucination risk. | |

**User's choice:** Global + per-strategy.
**Notes:** No per-proposal override.

### Q3 — Live-mode timeout

| Option | Description | Selected |
|--------|-------------|----------|
| Same default (30 min) for paper and live | Timeout is about rationale-freshness; OrderGuard + first-live gate already handle real-money safety. | ✓ |
| Shorter for live (e.g., 15 min) | Pressures first-live dual-channel flow. | |
| Longer for live (e.g., 60 min) | Rationale staleness risk grows. | |

**User's choice:** Same default for both.
**Notes:** One mental model, fewer corner cases.

### Q4 — Expiry UX

| Option | Description | Selected |
|--------|-------------|----------|
| chat.update card in-place to EXPIRED + separate DM notice | Best UX feedback loop; explicit signal. | ✓ |
| Update card only (silent) | User wonders if proposal expired. | |
| Separate DM only (leave card looking PENDING) | Confusing stale card behind. | |

**User's choice:** Card update + separate DM.
**Notes:** Greyed-out card + disabled-style status line; DM cites configured timeout + strategy name.

---

## Edit-size + Dashboard /approvals (HITL-04, DASH-04)

### Q1 — Edit-size UI

| Option | Description | Selected |
|--------|-------------|----------|
| Modal (views_open) with qty input + new-notional preview + drift display | Best UX; matches use case ("50 shares not 47"); contains drift error visually. | ✓ |
| Inline +/- buttons (+10%, ×2, ÷2) | Faster but limited to fixed deltas; no fractional qty. | |
| Two-step: modal → submit returns to card → click Approve separately | Most deliberate; introduces race with dedup table. | |

**User's choice:** Modal.
**Notes:** Submit = approve-with-edit; re-runs OrderGuard 2% drift check (D-27 invariant); modal re-renders with red drift-exceeded error on failure.

### Q2 — Dashboard /approvals shape

| Option | Description | Selected |
|--------|-------------|----------|
| Full mirror: HTMX cards 1:1 with Slack | Single source of truth for card schema; kills drift. | ✓ |
| List-view with click-to-detail | Simpler index; worse when there's one proposal. | |
| Hybrid (list + click-to-detail for edit) | Spreads UX across two pages. | |

**User's choice:** Full mirror.
**Notes:** Shared Jinja2 proposal-card template; render-context flag swaps Slack vs dashboard transport.

### Q3 — Cross-surface race resolution

| Option | Description | Selected |
|--------|-------------|----------|
| Extend dedup table with `source` column; first-write-wins enforced by state-machine | Same race primitive across all surfaces; source column for audit visibility. | ✓ |
| Dashboard always wins | Couples surfaces; offline behavior hard to reason about. | |
| Slack always wins | Same problem, reversed. | |

**User's choice:** Extend with source column.
**Notes:** Source column is for audit, not dedup semantics. State machine enforces first-writer.

### Q4 — Dashboard auth in P3 (before P6 magic-link)

| Option | Description | Selected |
|--------|-------------|----------|
| Localhost-only + SQLCipher-passphrase-on-first-load session cookie | Aligns with REG-03 single-user; passphrase is auth secret; P6 swaps without route change. | ✓ |
| Tunnel-only access (Tailscale / Cloudflare Tunnel) + no app-layer auth | Operator can set up tunnel for remote; app-layer auth still needed on-machine. | |
| Bearer-token in URL (one-time link emailed/Slack-DM'd per session) | Reinvents P6's magic-link prematurely. | |

**User's choice:** Localhost + passphrase cookie.
**Notes:** P6 (Web Dashboard & Multi-User Auth) swaps to fastapi-users magic-link without changing route shape.

---

## Claude's Discretion

Items deferred to research / planning (not user-decisions):

- Exact `slack_action_dedup` table schema + Alembic migration sequencing
- Exact dashboard auth cookie middleware (FastAPI SessionMiddleware vs fastapi-users CookieTransport shim)
- Slack modal `views_open` payload shape for edit-size
- APScheduler sweep job persistence + hot-reload double-fire semantics
- HTMX-level patterns for the modal swap on `/approvals` edit-size
- Daily P&L DM block format + exact post-close fire time
- Quiet-hours validation UX on per-strategy override form
- Sweep job registration location (alongside P1 jobs in `dashboard.app.lifespan`)
- Ephemeral message `respond_url` lifetime constraints

## Deferred Ideas

Captured for future phases (not lost, not acted on now):

- Per-proposal LLM-suggested timeout (P4 candidate if Decision output tightens)
- Bearer-token magic-link `/approvals` URL (P3.5 only if remote dashboard demand emerges before P6)
- Tunnel-only access (operator-level concern, not application-layer)
- Per-strategy timezone override (v2.x if international-markets strategies ship)
- Intersection/union merge of quiet hours (post-launch follow-up if strategy-override misconfigured)
- Hybrid quiet-hours: pause-scheduler + queue manually-triggered (operator chose pure pause)
- Inline +/- edit-size buttons (post-launch additive; not replacement)
- Cost-ceiling soft-warning DMs (P4 territory)
- Anomaly-demote DMs (P5 Trust Ladder territory)
- Drainable DM queue cleanup TTL (planner discretion; flag for review)

---

# Gap-Closure Discussion — Edit-Size Legibility Redesign

**Date:** 2026-06-22
**Trigger:** Live UAT Test 2 reopened (edit-size still not digestible for a non-technical operator)
**Areas discussed:** Slider vs surfaces, slider range & readout, cap calibration

## Surface split (slider only renders on web)

| Option | Description | Selected |
|--------|-------------|----------|
| Slider on dashboard; Slack "Edit on dashboard" button | Slack keeps Approve/Reject; "Edit size" deep-links to dashboard slider | ✓ |
| Slider on dashboard; Slack preset buttons | Dashboard slider; Slack swaps number field for $ preset radio options | |
| Dashboard-first, Slack edit later | Build dashboard slider now; leave Slack modal as-is | |

**User's choice:** Slider on dashboard; Slack "Edit size" deep-links to it.
**Notes:** Operator opened with the vision — "make it digestible for a non-technical user, maybe a slider bar... make the information tangible so they understand how much money they're investing." Aligns with existing D-60 escalate-to-dashboard URL-button pattern.

## Slider range & live readout

| Option | Description | Selected |
|--------|-------------|----------|
| 1 share → cap; readout = shares + $ + % equity | Range 1..cap-max-shares, handle at agent qty, "N sh ≈ $X — Y% of $Z" | ✓ |
| 1 share → cap; readout = shares + $ only | Same range, drop the % | |
| $0 → cap in dollars; shares derived | Dollar-denominated slider, shares secondary | |

**User's choice:** 1 share → cap; readout shows shares + $ + % of equity. Whole-share snaps. CTA "Approve at this size."

## Cap calibration ("is the safety net too low?")

| Option | Description | Selected |
|--------|-------------|----------|
| Bump the demo strategy's cap | Raise max_position_pct 0.05 → ~0.10–0.15 for headroom | |
| Keep cap; accept down-mostly resize | No calibration change; slider's visual indicator carries legibility | ✓ (Claude's call, user deferred) |
| Make the cap editable in the dashboard | Surface max_position_pct in strategy editor | deferred → Phase 6 |

**User's choice:** Deferred to Claude — "we may be okay if we have the slider now since it will give a clear visual indicator, whatever you think makes the most sense."
**Claude's decision:** Keep the cap unchanged this round. The slider's handle position relative to the right edge is the visual legibility fix; the at-cap worst case renders "This is your maximum for this strategy — N shares ≈ $X." User-editable cap → Phase 6.

## Claude's Discretion
- Slider widget mechanics (native range input + inline JS readout vs HTMX round-trip; no-build constraint favors client-side compute, server-side cap check authoritative).
- Equity-fetch-failure rendering (shares-only + cap-unconfirmed note; server still runs _check_edit_size_caps).
- Retiring the Slack view_submission edit handlers and wiring the URL button (mirror D-60).

## Deferred Ideas (routed to Phase 6)
- User-editable max_position_pct in the dashboard strategy editor (sets the slider range).
- /approvals state segmentation (expired in own section / tabs).
- Persistent site-wide dashboard nav toolbar.
