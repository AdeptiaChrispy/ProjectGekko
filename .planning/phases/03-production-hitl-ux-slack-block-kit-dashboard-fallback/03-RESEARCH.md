# Phase 3: Production HITL UX (Slack Block Kit + Dashboard Fallback) — Research

**Researched:** 2026-06-17
**Domain:** Slack interactivity hardening, FastAPI dashboard mirroring, APScheduler periodic sweeps, timezone-aware quiet hours, idempotent state-machine transactions
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

All 17 decisions D-41..D-57 in `03-CONTEXT.md` are LOCKED. The planner MUST honor each one verbatim. Restated below in the same shape they appeared in CONTEXT.md so downstream agents see them with no indirection.

**A. Idempotency Mechanism (HITL-02)**

- **D-41: Belt-and-suspenders idempotency.** New `slack_action_dedup` table (per-user SQLCipher DB) with UNIQUE `(proposal_id, action_id, actor_slack_user_id)`. Slack handlers INSERT at the top of `handle_approve` / `handle_reject` / `handle_edit_size` / `handle_escalate`; `IntegrityError` = duplicate → "already handled" path. The existing `transition_status` CHECK (line 126 in `approval/proposals.py`) is the secondary defense for code paths bypassing the dedup table.
- **D-42: Dedup key = `(proposal_id, action_id, actor_slack_user_id)`.** Three-tuple captures "which proposal, which button, which clicker." `actor_slack_user_id` is what Slack sends; identity-split-aware schema also records `actor_gekko_user_id` resolved at INSERT time. Extends cleanly into P6 multi-user.
- **D-43: Dup-click UX = ephemeral Slack message** via `respond_url` with `response_type="ephemeral"`. Format: `✅ Already approved by @<user> at <HH:MM>. Status: <current_status>.`
- **D-44: First-write-wins by INSERT timestamp.** Whichever callback reaches the dedup table first commits its row and locks the state transition; the second's INSERT either succeeds with a different `action_id` (separate intent) but then sees the proposal no longer PENDING, OR fails as a duplicate of itself (Slack retry).
- **D-45: Dedup table audit shape:** `id (pk)`, `proposal_id (fk)`, `action_id` (`'approve_proposal'`/`'reject_proposal'`/`'edit_size'`/`'escalate'`), `actor_slack_user_id`, `actor_gekko_user_id`, `source` (`'slack'`/`'dashboard'`), `slack_trigger_id` (nullable, retry-header debugging), `inserted_at`, `result` (`'first_write'`/`'duplicate'`). Duplicate-resolution path also emits a `dedup_click` event into the audit log.

**B. Quiet Hours Behavior (HITL-05)**

- **D-46: Quiet hours pause the agent loop entirely.** APScheduler skips Researcher + Decision cycles when current time is in-window. Manual `gekko run` CLI bypasses (operator-initiated = intent).
- **D-47: Per-user default + per-strategy override; strategy override WINS when set.** Add `User.quiet_hours_start: time | null`, `User.quiet_hours_end: time | null`, `User.timezone: str` (IANA). Add `Strategy.quiet_hours_start: time | null`, `Strategy.quiet_hours_end: time | null` (no `Strategy.timezone` — strategy inherits user TZ). Dashboard MUST warn when strategy window is narrower than user window.
- **D-48: Quiet-hours bypass categories for DMs (always fire):** (1) `kill_active` state changes; (2) Executor errors (`BrokerOrderError`, `OrderGuardRejected`, `MarketClosed` retry exhaustion); (3) First-live-trade FILLS. Suppressed during quiet hours: routine paper-trade fill confirmations, daily P&L summary, cost-ceiling soft-warnings (P4 territory anyway).
- **D-49: Timezone is per-user IANA name on User row.** Validated against `zoneinfo.available_timezones()` at config time. DST handled automatically by `zoneinfo`. The 30-min proposal expiry timer (D-52) lives in UTC; only the quiet-hours window comparison converts to user TZ. Default v1: `'America/New_York'`.

**C. Timeout / Expiry Mechanic (HITL-03)**

- **D-50: APScheduler periodic sweep every 60s.** `expire_stale_proposals()` SELECTs `PENDING` proposals with `expires_at <= now()`, transitions to EXPIRED, writes `expiration` audit event, chat.updates the Slack card, DMs the operator. Max latency 60s past timeout. Survives process restart.
- **D-51: Timeout default 30 min, per-strategy override.** Schema: `Strategy.proposal_timeout_minutes: int | null` (default null → `PROPOSAL_TIMEOUT_DEFAULT_MIN = 30`). LLM doesn't pick timeout. ProposalWriter sets `expires_at = utcnow() + timedelta(minutes=strategy.proposal_timeout_minutes or 30)`.
- **D-52: Same timeout for paper and live proposals.** One mental model.
- **D-53: Expiry UX = `chat.update` original card in-place + separate DM notice.** Greyed-out header `⏰ Proposal expired at <HH:MM> — not executed (timeout=REJECT after 30 min)`; buttons replaced by disabled-style status line. Separate DM: `Your <TICKER> <SIDE> proposal expired without action. Reason: timeout=REJECT (configured at <N>min on strategy <name>)`. Edge case: sweep-vs-click race resolved by `FOR UPDATE` + dedup-table + state-machine no-op.

**D. Edit-size + Dashboard /approvals (HITL-04, DASH-04)**

- **D-54: Edit-size = Slack modal (`views_open`)** with (1) number input pre-filled with `tp.qty`; (2) read-only `New notional = qty × <ref_price> = $<computed>` and `Drift vs target_notional_usd = <pct>%`; (3) submit "Approve at this size". `handle_edit_size_submit`: (a) INSERTs dedup row (`action_id='edit_size'`); (b) re-runs OrderGuard's 2% drift check on existing `target_notional_usd` — if `abs((qty × ref_price) - target_notional_usd) / target_notional_usd > 0.02`, modal re-renders with red error block, no state change; (c) on pass: write `edit_size` audit event with `{old_qty, new_qty, old_notional, new_notional, drift_pct}`, update `proposal.qty`, transition PENDING → APPROVED, dispatch executor. Single interaction.
- **D-55: Dashboard `/approvals` = full mirror of Slack card** (HTMX cards, 1:1). Same buttons, same rationale + evidence blocks. Single Jinja2 template renders the proposal-card schema for both surfaces. Edit-size on dashboard opens an HTMX modal with same qty input + drift preview; same `handle_edit_size_submit` server logic. `/live-confirm/{proposal_id}` (P2) and `/approvals` coexist.
- **D-56: Cross-surface race resolved by dedup-table `source` column.** Values `'slack'`, `'dashboard'`, `'cli'`. Dedup key on Slack side: `(proposal_id, action_id, actor_slack_user_id)`; on dashboard: `(proposal_id, action_id, actor_gekko_user_id, source)`. `source` is for AUDIT visibility only; first-write-wins is enforced at the state-machine layer.
- **D-57: Dashboard auth = localhost-only + SQLCipher-passphrase-on-first-load session cookie.** FastAPI binds `127.0.0.1:<port>` only. First GET to `/approvals` shows passphrase prompt; correct passphrase derives SQLCipher key, unlocks per-user DB, mints session cookie (8-hour idle expiry, `HttpOnly`, `Secure=False` for HTTP-on-localhost, `SameSite=Strict`). SQLCipher passphrase IS the auth secret. P6 swaps to magic-link via `fastapi-users` without changing route shape.

**E. REPT-01 DM Categories (Claude's Discretion at planning time, scoped here)**

The 5 DM categories: Trade proposals (shipped P1; P3 adds idempotency + quiet-hours-gating per A/B); Trade executions (shipped P1; P3 adds quiet-hours-suppression for routine paper fills, bypass for first-live fills per D-48); **Daily P&L summary** (NEW in P3 — fixed schedule 4:30pm America/New_York post-close, bypasses user quiet hours); Errors and operational alerts (shipped P2 + P2 fixes; P3 adds severity tier on DMs: `⚠️` informational, `❌` error, `🚫` kill-state); Carry-forward audit.

### Claude's Discretion

- Exact `slack_action_dedup` table schema and Alembic migration sequencing (column types, indexes, FK cascade rules) — planner.
- Exact dashboard auth cookie implementation (FastAPI `SessionMiddleware` vs `fastapi-users` `CookieTransport` shim) — planner with researcher input. **This research recommends `SessionMiddleware` for P3** (see §D-57 deep-dive below).
- Slack modal `views_open` payload shape for edit-size — researcher confirms current slack-bolt API (see §D-54 below).
- APScheduler sweep job persistence + idempotency on restart (does the 60s job double-fire on hot reload?) — researcher confirms (see §D-50 / Critical Discrepancy below).
- HTMX-level patterns for the modal swap on `/approvals` edit-size (existing dashboard templates use `hx-target` + `hx-swap="outerHTML"` — confirm reuse) — planner via codebase scout.
- Daily P&L DM block format + exact post-close fire time (4:30pm ET? Or after `pandas_market_calendars` confirms close?) — planner.
- Quiet-hours validation UX on the per-strategy override form (narrower-than-user warning) — planner / UI-phase.
- Where the `expire_stale_proposals` sweep job is registered (`dashboard.app.lifespan` already wires APScheduler for P1 — add another job alongside) — planner.
- Ephemeral message timing and `respond_url` lifetime constraints (Slack docs say ~30 min) — researcher confirms (see §HITL-02 below).

### Deferred Ideas (OUT OF SCOPE)

- Per-proposal LLM-suggested timeout (P4 revisit).
- Bearer-token magic-link `/approvals` URL (replaced by P6 magic-link auth).
- Tunnel-only access (Tailscale / Cloudflare Tunnel) — OS / network layer concern, not P3.
- Per-strategy timezone override (revisit only if international-markets strategy ships).
- Intersection / union merge rule for per-user + per-strategy quiet hours (operator chose strategy-override-wins; revisit if late-night ping reported).
- Hybrid quiet-hours: pause-scheduler + queue manually-triggered proposals (operator chose pure pause).
- Inline +/- edit-size buttons (modal chosen; additive follow-up possible).
- Cost-ceiling soft-warning DMs (P4 territory).
- Anomaly-demote DMs (P5 territory).
- Drainable DM queue cleanup TTL — flagged for planner review.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| **HITL-02** | Slack buttons are idempotent — at-least-once delivery cannot cause double-execution | §HITL-02 below: `slack_action_dedup` table design, `X-Slack-Retry-Num` header semantics, 3-second ack contract, dedup `IntegrityError` branch pattern, state-machine no-op as secondary defense |
| **HITL-03** | Timeout = REJECT default (configurable per strategy); proposals expire after 30 minutes (configurable) | §HITL-03 below: APScheduler 3.x `IntervalTrigger` with `coalesce=True` + `max_instances=1` + `misfire_grace_time` for restart safety; `FOR UPDATE` row-lock pattern; `expires_at` UTC column; `chat.update` card mutation |
| **HITL-05** | Quiet hours configurable per user — proposals outside the window are queued until window opens (no 2am pings) | §HITL-05 below: `zoneinfo.ZoneInfo` IANA validation, DST spring-forward (23h day) / fall-back (25h day) semantics, overnight window comparison (`start > end` wrap), bypass-category routing |
| **DASH-04** | Dashboard provides a web-based approval fallback for HITL when Slack is unavailable | §DASH-04 below: HTMX `hx-swap="outerHTML"` modal pattern, `HX-Trigger` server-driven close events, shared Jinja2 partial rendering Slack-card schema for both surfaces, FastAPI `SessionMiddleware` cookie auth |
| **REPT-01** | Slack DM for: trade proposals (HITL), trade executions, daily P&L summary, errors and operational alerts | §REPT-01 below: APScheduler `CronTrigger(hour=16, minute=30, timezone='America/New_York')`, market-closed-day handling, severity-tier emoji prefix, executor-error-→-DM carry-forward audit |
</phase_requirements>

## Summary

Phase 3 is a glue-and-harden phase: every load-bearing primitive already exists (state machine, Slack Bolt async app, APScheduler 3.x in `dashboard.app.lifespan`, identity-split-aware `_send_slack_dm` seam, Jinja2 templates). P3 adds a **dedup table** in front of the state machine, a **60-second APScheduler sweep** for expiry, a **per-user IANA-timezone-aware quiet-hours predicate**, a **Slack modal + matching HTMX modal** for edit-size with the OrderGuard 2% drift check re-applied, and a **FastAPI session-cookie auth layer** in front of a `/approvals` page that mirrors the Slack card 1:1.

**Critical discrepancy resolved up front:** CONTEXT.md repeatedly says "APScheduler 4.x" but `pyproject.toml` pins `apscheduler>=3.10,<4` and the installed version is **3.11.2**. The 3.x API surface is the one this phase must target. The runtime persistence and restart semantics are different from 4.x; documented below in §HITL-03.

**Primary recommendation:** Land the dedup table + state-machine extensions in Wave 1 (Alembic 0004), the expiry sweep + quiet-hours predicate in Wave 2, the edit-size modal + dashboard `/approvals` + auth in Wave 3, and the REPT-01 daily P&L + carry-forward audit in Wave 4. Every Wave 1 schema change must round-trip Alembic down/up like 0002/0003 already do.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Slack at-least-once idempotency (HITL-02) | DB (per-user SQLCipher `slack_action_dedup` table) | App (state-machine CHECK) | UNIQUE constraint is the atomic primitive; state-machine is the catch-all for non-Slack surfaces (CLI, dashboard) |
| Proposal expiry sweep (HITL-03) | APScheduler (in-process) | DB (`expires_at` column + row-level `FOR UPDATE`) | APScheduler 3.x already lives in `dashboard.app.lifespan`; add second `IntervalTrigger` job alongside the daily-cron jobs |
| Quiet-hours predicate (HITL-05) | App (Python `zoneinfo` predicate `_resolve_quiet_hours(user_id, now) → bool`) | Scheduler (predicate gates job execution) | Time arithmetic is Python's; scheduler is just the consumer |
| Edit-size modal (D-54) | Slack (Block Kit `views_open` + view_submission) | Dashboard (HTMX modal, same server logic) | One surface implementation, two transports; shared `handle_edit_size_submit` |
| `/approvals` index (DASH-04) | Dashboard (FastAPI + HTMX + Jinja2 shared partial) | — | Renders the Slack proposal-card schema to HTML via render-context flag |
| Dashboard auth (D-57) | FastAPI middleware (`SessionMiddleware` itsdangerous-signed cookie) | App (SQLCipher passphrase cache) | Localhost-only single-operator session; minimal P3 surface, swappable in P6 |
| Daily P&L digest (REPT-01) | APScheduler (`CronTrigger(hour=16, minute=30, tz='America/New_York')`) | App (audit-log query + Slack DM render) | Wall-clock fire time + fixed timezone; bypasses quiet hours |
| Carry-forward executor-error DM audit | App (`_send_slack_dm` seam in `executor.py`) | — | Already wired at lines 454 (MarketClosed) and 654 (BrokerOrderError); P3 audits coverage and adds severity-tier prefix |

## Standard Stack

### Core (already installed and pinned)

| Library | Pinned Version | Installed | Purpose | Why Standard |
|---------|---------------|-----------|---------|--------------|
| **`slack-bolt`** | `>=1.18,<2` | **1.28.0** [VERIFIED: `.venv\Lib\site-packages\slack_bolt-1.28.0.dist-info`] | Slack interactivity, Socket Mode, FastAPI adapter, `AsyncApp.view()` decorator for view_submission | Already wired in `src/gekko/slack/app.py` (`AsyncApp`); P3 adds `@app.view("edit_size_modal")` listener |
| **`apscheduler`** | `>=3.10,<4` | **3.11.2** [VERIFIED: `.venv\Lib\site-packages\apscheduler-3.11.2.dist-info`] | Periodic + cron jobs persisted in SQLCipher via `SQLAlchemyJobStore` | Already wired in `gekko.scheduler.jobs.build_scheduler` and `dashboard.app.lifespan`; P3 adds `IntervalTrigger(seconds=60)` + `CronTrigger(hour=16, minute=30, ...)` jobs |
| **`fastapi`** | `>=0.115,<0.120` | (Phase-1 install) | Dashboard + routes + middleware | `SessionMiddleware` from Starlette is the auth substrate |
| **Starlette `SessionMiddleware`** | (via FastAPI) | (Phase-1 install) | itsdangerous-signed session cookie for localhost auth | Standard pattern; P6 swaps to fastapi-users without route changes [CITED: https://fastapi.tiangolo.com/advanced/middleware/] |
| **`itsdangerous`** | (transitive of `SessionMiddleware`) | (Phase-1 install) | Cookie signing | Required by `SessionMiddleware` — already in tree as a Starlette/FastAPI transitive |
| **HTMX** | 2.0.4 (vendored) | 2.0.4 [VERIFIED: `src/gekko/dashboard/static/htmx.min.js` SHA-384] | `hx-swap="outerHTML"` modal pattern, `HX-Trigger` server-driven close | Already vendored with SRI gate; no new asset needed |
| **`python-zoneinfo`** | stdlib | (stdlib) | IANA timezone resolution with DST | Already used in `gekko.scheduler.jobs._parse_schedule_time` and `gekko.schemas.strategy._validate_schedule_time`. Requires `tzdata` on Windows (already pinned). |
| **`tzdata`** | (pyproject pin) | (Phase-1 install) | Windows tz database backing for `zoneinfo` | Already pinned (Pitfall 5) |
| **`sqlalchemy`** | `>=2.0,<3` | (Phase-1 install) | Async session + `UNIQUE` constraint INSERT raises `sqlalchemy.exc.IntegrityError` | The dedup-table INSERT exception is the load-bearing branch point |
| **`alembic`** | (Phase-1 install) | (Phase-1 install) | Schema migration for new tables + columns + extended CHECK constraints | Phase-1 + Phase-2 migrations (0001, 0002, 0003 if shipped) are the precedent |

### No new third-party packages required

The phase is wired entirely from existing dependencies. **No `pip install` step is needed.** The package legitimacy audit below confirms.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `SessionMiddleware` | `fastapi-users` `CookieTransport` | P3 doesn't need user registration / magic-link / password hashing; `fastapi-users` is the P6 surface. Using it now means importing the package + DB models for one cookie. Defer per D-57. |
| Dedup table | Redis `SETNX` with TTL | Industry pattern per [CITED: docs.slack.dev/interactivity/handling-user-interaction], but adds a new process to supervise (kill PROJECT.md constraint: "no AWS/Azure dependency for v1"). SQLCipher UNIQUE constraint is local, persistent, and gives us audit-grep-ability per D-45. |
| APScheduler `IntervalTrigger` for expiry | Single APScheduler cron job at strategy's `expires_at` minute | Per-proposal jobs scale to N proposals × persistent rows; the 60s sweep is O(1) jobs and survives restart cleanly. D-50 explicitly chose the sweep model. |
| Slack modal for edit-size | Inline +/- buttons | D-54 explicitly chose modal (fractional/specific qty UX + contained drift-error block). Inline +/- is deferred (not rejected — additive follow-up possible). |
| `chat.update` to grey out expired card | Delete expired card | Operators benefit from the visible "this expired" surface in DM history; `chat.update` preserves the audit trail in Slack itself [CITED: docs.slack.dev/messaging/modifying-messages] |

**Version verification:**
```bash
# Already-installed versions confirmed via:
ls .venv/Lib/site-packages | grep -iE "slack_bolt|apscheduler"
# → apscheduler-3.11.2.dist-info, slack_bolt-1.28.0.dist-info
```

No `npm view` / `pip index versions` step needed for new packages since none are added.

## Package Legitimacy Audit

> P3 introduces **no new third-party packages**. Every library referenced is already pinned in `pyproject.toml` and was cleared by the Phase 1 (Plan 01-01) or Phase 2 (Plan 02-01) PyPI legitimacy audit checkpoints.

| Package | Registry | Age | Source Repo | Phase 1 Audit | Disposition |
|---------|----------|-----|-------------|---------------|-------------|
| slack-bolt | PyPI | 4+ years | github.com/slackapi/bolt-python | Cleared in Plan 01-08 | Approved (unchanged) |
| apscheduler | PyPI | 10+ years | github.com/agronholm/apscheduler | Cleared in Plan 01-09 | Approved (unchanged) |
| fastapi | PyPI | 6+ years | github.com/tiangolo/fastapi | Cleared in Plan 01-01 | Approved (unchanged) |
| starlette (SessionMiddleware) | PyPI | 7+ years | github.com/encode/starlette | Cleared (transitive of FastAPI) | Approved (unchanged) |
| itsdangerous | PyPI | 10+ years | github.com/pallets/itsdangerous | Cleared (transitive of Starlette) | Approved (unchanged) |
| sqlalchemy / alembic | PyPI | 15+ years | github.com/sqlalchemy/sqlalchemy | Cleared in Plan 01-03 | Approved (unchanged) |
| python-zoneinfo / tzdata | stdlib / PyPI | (stdlib) / 4+ years | github.com/python/tzdata | Cleared in Plan 01-09 | Approved (unchanged) |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

*slopcheck was not re-run for this phase because no new package surface is being introduced; the audit gate is the absence of new dependencies, not their presence.*

## Architecture Patterns

### System Architecture Diagram

```
                   Slack workspace (operator's DM)
                              │
                              │  proposal card (HITL-01)
                              ▼
        ┌──────────────────────────────────────┐
        │  Slack Bolt AsyncApp (Socket Mode)   │
        │  src/gekko/slack/app.py              │
        │  src/gekko/slack/interactivity.py    │
        └──────────────────────────────────────┘
                              │
        action: approve / reject / edit_size / escalate
                              │
        ┌─────────────────────▼─────────────────────────────────┐
        │ NEW IN P3 — Dedup gate (slack_action_dedup INSERT)     │
        │ src/gekko/approval/dedup.py                           │
        │   IntegrityError → ephemeral "already handled" DM     │
        │   first_write → continue to state machine             │
        └─────────────────────┬─────────────────────────────────┘
                              │
                              ▼
        ┌──────────────────────────────────────┐
        │ State machine (transition_status)    │
        │ src/gekko/approval/proposals.py      │   ← P3 adds EXPIRED status + 1 edge
        │ PENDING → APPROVED / REJECTED        │      (PENDING → EXPIRED)
        │ PENDING → EXPIRED (sweep path)       │   ← P3 NEW
        │ PENDING → AWAITING_2ND_CHANNEL       │      (existing P2 edge — unchanged)
        └─────────────────────┬─────────────────┘
                              │ APPROVED
                              ▼
        ┌──────────────────────────────────────┐
        │ Executor (OrderGuard → Alpaca)       │
        │ src/gekko/execution/executor.py      │
        │ Existing P2 wiring; P3 audits the    │
        │ silent-FAILED paths and adds         │
        │ severity-tier emoji to DMs           │
        └──────────────────────────────────────┘

        ┌─────────────────── Periodic sweep (NEW IN P3) ───────────────────┐
        │ APScheduler IntervalTrigger(seconds=60)                          │
        │ src/gekko/scheduler/jobs.py: register_expire_stale_sweep(...)    │
        │   SELECT id, user_id FROM proposals                              │
        │     WHERE status='PENDING' AND expires_at <= now()               │
        │     FOR UPDATE                                                   │
        │   for each row:                                                  │
        │     transition_status(PENDING → EXPIRED)                         │
        │     append_event(event_type='expiration', payload={...})         │
        │     chat.update(<ts>, blocks=expired_card)                       │
        │     _send_slack_dm(user_id, "Your <tkr> proposal expired ...")   │
        └──────────────────────────────────────────────────────────────────┘

        ┌────── Quiet-hours predicate (NEW IN P3) ───────┐
        │ src/gekko/approval/quiet_hours.py              │
        │ _resolve_quiet_hours(user_id, now: datetime)   │
        │   → bool (True = in quiet window, skip)        │
        │ Used by:                                       │
        │   1. APScheduler trigger_strategy_run gate     │
        │   2. _send_slack_dm_respecting_quiet_hours()   │
        │      wrapper for routine-DM categories         │
        │ DOES NOT GATE:                                 │
        │   - kill_active DMs (D-48 #1)                  │
        │   - executor error DMs (D-48 #2)               │
        │   - first-live-trade fills (D-48 #3)           │
        │   - 4:30pm ET daily P&L cron (D-48 / E)        │
        └────────────────────────────────────────────────┘

        ┌────────────────── Dashboard /approvals (NEW IN P3) ────────────────┐
        │ src/gekko/dashboard/routes.py                                       │
        │ GET  /login                          (passphrase prompt)            │
        │ POST /login                          (mint signed session cookie)   │
        │ GET  /approvals                      (HTMX-rendered index of        │
        │                                       PENDING proposals — uses the  │
        │                                       SAME proposal-card Jinja2     │
        │                                       partial as Slack)             │
        │ POST /approvals/{id}/approve         (same server logic as          │
        │                                       handle_approve, source=       │
        │                                       'dashboard' in dedup row)     │
        │ POST /approvals/{id}/reject          (same; reject)                 │
        │ GET  /approvals/{id}/edit-modal      (HTMX modal HTML fragment)     │
        │ POST /approvals/{id}/edit-submit     (same drift check as Slack;    │
        │                                       returns errors or commits)    │
        │ Auth: SessionMiddleware signed cookie, HttpOnly, SameSite=Strict    │
        └─────────────────────────────────────────────────────────────────────┘

        ┌──── Daily P&L digest (NEW IN P3 — REPT-01 daily) ─────┐
        │ APScheduler CronTrigger(hour=16, minute=30,            │
        │                          timezone='America/New_York')  │
        │ src/gekko/reporter/daily_pnl.py                        │
        │   1. SELECT today's fills + cap_rejections + errors    │
        │   2. Aggregate per-strategy realized P&L               │
        │   3. Build Block Kit digest card                       │
        │   4. _send_slack_dm_blocks(...)                        │
        │ Bypasses user quiet hours (D-48).                      │
        │ Market-closed days: send minimal "no fills today" DM   │
        │ (planner discretion — see Open Questions).             │
        └────────────────────────────────────────────────────────┘
```

### Recommended Project Structure (P3 additions)

```
src/gekko/
├── approval/
│   ├── proposals.py            # P3: extend STATE_TRANSITIONS + add EXPIRED status
│   ├── slack_handler.py        # P3: rewrite handle_edit_size_stub; add dup-click path
│   ├── dedup.py                # P3 NEW: INSERT helper raising on UNIQUE clash
│   └── quiet_hours.py          # P3 NEW: _resolve_quiet_hours(user_id, now) → bool
├── dashboard/
│   ├── app.py                  # P3: add SessionMiddleware; register 2 new APScheduler jobs in lifespan
│   ├── routes.py               # P3: add /login, /approvals, /approvals/{id}/*, edit-modal HTMX route
│   └── templates/
│       ├── login.html.j2                   # P3 NEW
│       ├── approvals_index.html.j2         # P3 NEW
│       ├── proposal_card.html.j2           # P3 NEW shared partial (Slack-mirror)
│       └── edit_size_modal.html.j2         # P3 NEW HTMX modal
├── db/
│   └── models.py               # P3: add SlackActionDedup table + User.quiet_hours_* + User.timezone + Strategy.quiet_hours_* + Strategy.proposal_timeout_minutes + Proposal.expires_at + extend _PROPOSAL_STATUSES with 'EXPIRED' + extend _EVENT_TYPES with 'expiration', 'dedup_click', 'edit_size', 'daily_pnl'
├── execution/
│   └── executor.py             # P3 AUDIT-ONLY: confirm MarketClosed (line 454) + BrokerOrderError (line 654) DM; add severity-tier prefix; add quiet-hours-aware wrapper for routine fills
├── reporter/
│   ├── slack.py                # P3: add build_expired_card(); add severity-tier helper
│   └── daily_pnl.py            # P3 NEW: build_daily_pnl_digest(...) + cron job entry point
├── scheduler/
│   └── jobs.py                 # P3: register_expire_stale_sweep(scheduler, sync_engine) + register_daily_pnl_cron(scheduler)
├── schemas/
│   └── strategy.py             # P3: add Strategy.quiet_hours_start + Strategy.quiet_hours_end + Strategy.proposal_timeout_minutes (all Optional)

migrations/versions/
└── 0003_p3_hitl_ux.py          # P3 NEW Alembic migration (only one — see §"Alembic migration sequencing")
```

**Note on Alembic numbering:** Phase 2 plan 02-01 created `0002_orderguard.py`. There is no `0003_event_types_phase2.py` in the tree (Phase 2 fit the new event types into `_EVENT_TYPES` by extending 0002's CHECK constraint inline — see `migrations/versions/0002_orderguard.py`). So **P3's migration is `0003_p3_hitl_ux.py`**, not `0004_*` as CONTEXT.md mentions in passing.

### Pattern 1: Belt-and-suspenders idempotency (HITL-02)

**What:** Two independent defenses against double-execution under Slack's at-least-once delivery.

**When to use:** Every Slack action handler that triggers a state transition.

**Example:**
```python
# src/gekko/approval/dedup.py — P3 NEW
async def claim_action(
    session: AsyncSession,
    *,
    proposal_id: str,
    action_id: str,
    actor_slack_user_id: str | None,
    actor_gekko_user_id: str,
    source: Literal["slack", "dashboard", "cli"],
    slack_trigger_id: str | None = None,
) -> Literal["first_write", "duplicate"]:
    """INSERT a dedup row; return 'duplicate' on UNIQUE clash without raising.

    Pattern: catch IntegrityError, ROLLBACK, return 'duplicate'.
    Caller takes the appropriate branch (ephemeral response vs continue).
    """
    try:
        session.add(SlackActionDedup(
            proposal_id=proposal_id,
            action_id=action_id,
            actor_slack_user_id=actor_slack_user_id,
            actor_gekko_user_id=actor_gekko_user_id,
            source=source,
            slack_trigger_id=slack_trigger_id,
            inserted_at=datetime.now(UTC).isoformat(),
            result="first_write",
        ))
        await session.flush()
        return "first_write"
    except IntegrityError:
        # UNIQUE constraint clash — another handler beat us.
        await session.rollback()
        # Emit a dedup_click audit event in a FRESH transaction so the
        # rollback above doesn't lose it.
        await _append_dedup_click_event(...)
        return "duplicate"

# Source: SQLAlchemy 2.x async IntegrityError pattern (Phase 1 Plan 01-07 ProposalWriter
# uses the identical SELECT-then-INSERT-with-IntegrityError-handler shape).
```

**Slack handler integration:**
```python
# src/gekko/approval/slack_handler.py — P3 rewrite of handle_approve
async def handle_approve(*, ack, body, client):
    await ack()  # FIRST per Pitfall 3
    decision_id = body["actions"][0]["value"]
    slack_user_id = body["user"]["id"]
    trigger_id = body.get("trigger_id")  # NEW — store for retry-header debugging
    asyncio.create_task(_approve_workflow(
        decision_id=decision_id,
        slack_user_id=slack_user_id,
        trigger_id=trigger_id,
        respond_url=body.get("response_url"),
        client=client,
    ))

async def _approve_workflow(*, decision_id, slack_user_id, trigger_id, respond_url, client):
    settings = get_settings()
    gekko_user_id = settings.gekko_user_id
    # ... cross-user check (existing) ...
    sf, engine = _get_session_factory(gekko_user_id)
    try:
        async with sf() as session, session.begin():
            outcome = await claim_action(
                session,
                proposal_id=decision_id,
                action_id="approve_proposal",
                actor_slack_user_id=slack_user_id,
                actor_gekko_user_id=gekko_user_id,
                source="slack",
                slack_trigger_id=trigger_id,
            )
            if outcome == "duplicate":
                # D-43: ephemeral response via respond_url with current status.
                # Defer to a post-commit task so the ephemeral fires AFTER the
                # dedup_click event is durable.
                ...
                return
            # ... existing approve / dual-channel divert logic ...
```

### Pattern 2: APScheduler periodic sweep with restart safety (HITL-03)

**What:** A `IntervalTrigger(seconds=60)` job that survives process restart without double-firing.

**When to use:** Any "expire stale rows" pattern where the work is idempotent.

**Example:**
```python
# src/gekko/scheduler/jobs.py — P3 addition
from apscheduler.triggers.interval import IntervalTrigger

def register_expire_stale_sweep(
    scheduler: AsyncIOScheduler, *, user_id: str
) -> str:
    """Register the per-user expire-stale-proposals sweep.

    Per [CITED: apscheduler.readthedocs.io/en/3.x/userguide.html]:
      * coalesce=True  → if scheduler was down and 5 runs piled up,
                         only one runs (we want exactly one sweep, not 5)
      * max_instances=1 → never overlap a sweep with itself; if the
                          previous sweep is still running, drop this tick
      * misfire_grace_time=300 → forgive misses up to 5 minutes
                                  (anything older is "scheduler was down"
                                  and the sweep will pick up the work on
                                  the next interval anyway)
      * replace_existing=True → safe to call register on each lifespan
                                 startup; idempotent at the job-store layer
    """
    job_id = f"expire-stale-{user_id}"
    scheduler.add_job(
        "gekko.approval.expiry:expire_stale_proposals",  # module:fn string per Plan 01-09
        IntervalTrigger(seconds=60),
        kwargs={"user_id": user_id},
        id=job_id,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    return job_id

# Source: docs at apscheduler.readthedocs.io/en/3.x/userguide.html §"Limiting the number of concurrently executing instances of a job"
```

```python
# src/gekko/approval/expiry.py — P3 NEW
async def expire_stale_proposals(*, user_id: str) -> int:
    """Sweep PENDING proposals whose expires_at has passed.

    Returns count of proposals expired (0 most calls).
    """
    sf, engine = _get_session_factory(user_id)
    try:
        now_utc = datetime.now(UTC)
        n = 0
        async with sf() as session, session.begin():
            # SELECT … FOR UPDATE locks the rows so a concurrent
            # button-click cannot race us. SQLCipher backs SQLite which
            # serializes writers via WAL — FOR UPDATE syntactically valid
            # but in practice the per-session BEGIN is the lock.
            stmt = (
                select(Proposal)
                .where(
                    Proposal.status == "PENDING",
                    Proposal.expires_at <= now_utc.isoformat(),
                    Proposal.user_id == user_id,
                )
                .with_for_update()
            )
            rows = (await session.execute(stmt)).scalars().all()
            for row in rows:
                await transition_status(
                    session, row.proposal_id,
                    from_status="PENDING", to_status="EXPIRED",
                )
                await append_event(
                    session,
                    user_id=user_id,
                    strategy_id=row.strategy_id,
                    event_type="expiration",
                    payload={
                        "proposal_id": row.proposal_id,
                        "reason": "timeout",
                        "expired_at": now_utc.isoformat(),
                        "configured_timeout_minutes": _resolve_strategy_timeout(
                            session, row.strategy_id
                        ),
                    },
                )
                n += 1
        # OUTSIDE transaction — chat.update + DM. Best-effort.
        for row in rows:
            await _slack_chat_update_expired(row)
            await _send_slack_dm(
                user_id,
                f"⏰ Your {row.ticker} {row.side} proposal expired without action."
            )
        return n
    finally:
        if engine is not None:
            await engine.dispose()
```

### Pattern 3: Slack modal with view_submission validation (D-54 / HITL-04)

**What:** Open a Slack modal with `views.open`, handle `view_submission` with `response_action="errors"` for validation failures.

**When to use:** Any interactive flow that needs structured input + server-side validation + visible error feedback.

**Example:**
```python
# src/gekko/approval/slack_handler.py — P3 replaces handle_edit_size_stub
async def handle_edit_size(*, ack, body, client):
    """Open the edit-size modal via views.open."""
    await ack()
    decision_id = body["actions"][0]["value"]
    slack_user_id = body["user"]["id"]
    trigger_id = body["trigger_id"]  # required for views.open per Slack docs

    # Load the proposal so we can pre-fill qty and show current notional.
    settings = get_settings()
    gekko_user_id = settings.gekko_user_id
    sf, engine = _get_session_factory(gekko_user_id)
    try:
        async with sf() as session:
            row = await session.get(Proposal, decision_id)
            tp = TradeProposal.model_validate_json(row.payload_json)
    finally:
        if engine is not None:
            await engine.dispose()

    ref_price = tp.limit_price or tp.stop_price or _fetch_ref_price(tp.ticker)
    target = tp.target_notional_usd

    await client.views_open(
        trigger_id=trigger_id,
        view={
            "type": "modal",
            "callback_id": "edit_size_modal",  # MUST match @app.view() listener
            "private_metadata": json.dumps({
                "decision_id": decision_id,
                "ref_price": str(ref_price),
                "target_notional_usd": str(target),
                "original_qty": str(tp.qty),
                "ticker": tp.ticker,
                "response_url": body.get("response_url"),
            }),
            "title": {"type": "plain_text", "text": f"Edit size — {tp.ticker}"},
            "submit": {"type": "plain_text", "text": "Approve at this size"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "qty_block",
                    "label": {"type": "plain_text", "text": "New quantity"},
                    "element": {
                        "type": "number_input",
                        "action_id": "qty_input",
                        "initial_value": str(tp.qty),
                        "is_decimal_allowed": True,
                        "min_value": "0",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*Ref price:* ${ref_price}\n"
                            f"*Target notional:* ${target}\n"
                            f"*Original qty:* {tp.qty} → "
                            f"*Original notional:* ${tp.qty * ref_price}"
                        ),
                    },
                },
                {
                    "type": "context",
                    "elements": [{
                        "type": "mrkdwn",
                        "text": "Drift > 2% will be rejected (OrderGuard).",
                    }],
                },
            ],
        },
    )

# Source: [CITED: tools.slack.dev/bolt-python/concepts/opening-modals/] +
#         [CITED: docs.slack.dev/surfaces/modals/] +
#         [CITED: api.slack.com/reference/block-kit/block-elements/#number]
```

```python
# src/gekko/slack/interactivity.py — P3 NEW listener
@slack_app.view("edit_size_modal")
async def _edit_size_submit(ack, body, client, view):
    """view_submission handler — validates drift, updates proposal, dispatches.

    Returns response_action='errors' with block_id keyed message when drift
    exceeds 2%. Otherwise ack({}) (closes modal) and dispatches via task.
    """
    meta = json.loads(view["private_metadata"])
    decision_id = meta["decision_id"]
    ref_price = Decimal(meta["ref_price"])
    target_notional = Decimal(meta["target_notional_usd"])
    raw_qty = view["state"]["values"]["qty_block"]["qty_input"]["value"]

    try:
        new_qty = Decimal(raw_qty)
    except (InvalidOperation, TypeError):
        await ack({
            "response_action": "errors",
            "errors": {"qty_block": "Please enter a numeric quantity."},
        })
        return

    new_notional = new_qty * ref_price
    drift_pct = abs(new_notional - target_notional) / target_notional

    if drift_pct > Decimal("0.02"):
        # response_action='errors' re-renders the modal with the error
        # under the qty input. NO ROUND-TRIP TO DB. NO STATE CHANGE.
        # Per [CITED: docs.slack.dev/surfaces/modals — Validating submissions].
        await ack({
            "response_action": "errors",
            "errors": {
                "qty_block": (
                    f"Drift {drift_pct:.2%} exceeds the 2% safety bound. "
                    f"Target ${target_notional}; this qty = ${new_notional}. "
                    "Adjust qty or re-run the strategy."
                ),
            },
        })
        return

    # Pass: ack with empty body closes the modal. The state-machine work
    # happens in the background.
    await ack()
    asyncio.create_task(_edit_size_submit_workflow(
        decision_id=decision_id,
        new_qty=new_qty,
        slack_user_id=body["user"]["id"],
        meta=meta,
    ))

async def _edit_size_submit_workflow(*, decision_id, new_qty, slack_user_id, meta):
    """Background: dedup + update proposal qty + transition + dispatch executor."""
    settings = get_settings()
    gekko_user_id = settings.gekko_user_id
    sf, engine = _get_session_factory(gekko_user_id)
    try:
        async with sf() as session, session.begin():
            # D-54 step (a): dedup row
            outcome = await claim_action(
                session,
                proposal_id=decision_id,
                action_id="edit_size",
                actor_slack_user_id=slack_user_id,
                actor_gekko_user_id=gekko_user_id,
                source="slack",
            )
            if outcome == "duplicate":
                return
            # D-54 step (c): write edit_size event, update qty, transition.
            row = await session.get(Proposal, decision_id)
            tp = TradeProposal.model_validate_json(row.payload_json)
            old_qty, old_notional = tp.qty, tp.qty * Decimal(meta["ref_price"])
            new_notional = new_qty * Decimal(meta["ref_price"])
            await append_event(
                session, user_id=gekko_user_id, strategy_id=row.strategy_id,
                event_type="edit_size",
                payload=normalize_decimals({
                    "old_qty": old_qty, "new_qty": new_qty,
                    "old_notional": old_notional, "new_notional": new_notional,
                    "drift_pct": abs(new_notional - Decimal(meta["target_notional_usd"]))
                                  / Decimal(meta["target_notional_usd"]),
                    "actor": slack_user_id,
                }),
            )
            # Update payload_json with new qty (re-serialize TP with new qty).
            tp_updated = tp.model_copy(update={"qty": new_qty})
            row.payload_json = tp_updated.model_dump_json()
            await transition_status(
                session, decision_id,
                from_status="PENDING", to_status="APPROVED",
            )
        asyncio.create_task(execute_proposal(decision_id, gekko_user_id))
    finally:
        if engine is not None:
            await engine.dispose()
```

### Pattern 4: HTMX modal with outerHTML swap + HX-Trigger close (DASH-04)

**What:** Open + close a modal via HTMX without page reload.

**When to use:** Any dashboard modal that needs server-rendered content + server-driven close.

**Example:**
```html
<!-- src/gekko/dashboard/templates/approvals_index.html.j2 -->
<button hx-get="/approvals/{{ proposal.proposal_id }}/edit-modal"
        hx-target="#modal-mount"
        hx-swap="innerHTML">
  Edit size
</button>
<div id="modal-mount"></div>
```

```html
<!-- src/gekko/dashboard/templates/edit_size_modal.html.j2 -->
<div class="modal-backdrop" id="edit-modal">
  <form hx-post="/approvals/{{ proposal_id }}/edit-submit"
        hx-target="#modal-mount"
        hx-swap="innerHTML">
    <label>New quantity
      <input type="number" name="new_qty" step="any" value="{{ qty }}">
    </label>
    <p>Ref price: ${{ ref_price }} · Target: ${{ target_notional_usd }}</p>
    {% if drift_error %}
      <p class="error">{{ drift_error }}</p>
    {% endif %}
    <button type="submit">Approve at this size</button>
    <a hx-get="/modal/close" hx-target="#modal-mount" hx-swap="innerHTML">Cancel</a>
  </form>
</div>
<!-- /modal/close already exists in routes.py (line 347) — reuse it. -->
```

```python
# FastAPI route on success — server-driven close via HX-Trigger response header
# per [CITED: htmx.org/docs#response-headers]
@router.post("/approvals/{proposal_id}/edit-submit", response_class=HTMLResponse)
async def edit_submit(...):
    # ... drift check + state mutation ...
    if drift_pct > Decimal("0.02"):
        # Re-render the modal with the error — innerHTML swap to #modal-mount.
        return templates.TemplateResponse(
            "edit_size_modal.html.j2",
            {"request": request, "drift_error": "...", ...},
        )
    # Success — close modal via HX-Trigger header + return empty.
    response = HTMLResponse("")
    response.headers["HX-Trigger"] = "proposalListRefresh"  # listeners on the page refresh
    return response
```

### Pattern 5: Per-user IANA timezone quiet-hours predicate (HITL-05)

**What:** Compare "now" (timezone-aware) against a configured `time` window on a per-user IANA timezone, correctly handling DST and overnight wrap.

**When to use:** Any "is this in quiet hours?" check.

**Example:**
```python
# src/gekko/approval/quiet_hours.py — P3 NEW
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

async def _resolve_quiet_hours(
    user_id: str,
    *,
    now_utc: datetime | None = None,
    strategy_name: str | None = None,
) -> bool:
    """Return True iff now() is inside the quiet-hours window.

    Resolution order per D-47:
      1. strategy.quiet_hours_start/end (if strategy_name and set)
      2. user.quiet_hours_start/end (if set)
      3. None set → return False (no quiet hours).

    DST handling:
      * 'Now' is converted from UTC to user.timezone via
        zoneinfo.ZoneInfo. Spring-forward (23-hour day) and fall-back
        (25-hour day) are absorbed by the conversion — the predicate
        checks a wall-clock `time` against the converted .time() value.
      * Overnight windows (start > end, e.g. 22:00–07:00) wrap correctly:
        in-window iff t >= start OR t < end.
      * Same-day windows (start <= end) are simple: start <= t < end.
    """
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            user = await session.get(User, user_id)
            qh_start: time | None = user.quiet_hours_start
            qh_end: time | None = user.quiet_hours_end
            tz_name: str = user.timezone or "America/New_York"
            if strategy_name:
                strat_meta = await _load_strategy_quiet_hours(
                    session, user_id, strategy_name
                )
                if strat_meta is not None:
                    qh_start, qh_end = strat_meta
        if qh_start is None or qh_end is None:
            return False
        now_utc = now_utc or datetime.now(timezone.utc)
        local = now_utc.astimezone(ZoneInfo(tz_name)).time()
        if qh_start <= qh_end:
            return qh_start <= local < qh_end
        else:
            # Overnight wrap: 22:00–07:00 includes 23:30 and 06:30.
            return local >= qh_start or local < qh_end
    finally:
        if engine is not None:
            await engine.dispose()

# Source: [CITED: docs.python.org/3/library/zoneinfo.html] + DST handling guide
# at [CITED: dev.to/outdated-dev/daylight-saving-time-handling-strategies]
```

**DST corner cases this predicate handles correctly:**

| Scenario | Wall clock at boundary | Predicate behavior |
|----------|----------------------|---------------------|
| Spring-forward (2nd Sun Mar): 22:00–07:00 window | 02:00 doesn't exist — clock jumps 01:59 → 03:00 | `.time()` after `astimezone` returns 03:00; predicate still correctly returns True (03:00 ≥ start OR < end) |
| Fall-back (1st Sun Nov): 22:00–07:00 window | 01:30 happens twice (fold=0 then fold=1) | `astimezone` resolves both to a single UTC; predicate returns True for both occurrences |
| User in `America/Los_Angeles`, strategy in `America/New_York` schedule_time | (strategy inherits user TZ per D-47 — no per-strategy TZ in P3) | predicate uses `user.timezone`; no ambiguity |

### Pattern 6: FastAPI SessionMiddleware for localhost auth (D-57)

**What:** Sign a session cookie with itsdangerous, stored entirely client-side, read on each request.

**When to use:** Single-operator localhost dashboards where you want auth state without a server-side session store.

**Example:**
```python
# src/gekko/dashboard/app.py — P3 addition to create_app()
from starlette.middleware.sessions import SessionMiddleware
import secrets

def create_app() -> FastAPI:
    app = FastAPI(title="Gekko", lifespan=lifespan)

    # P3 D-57: session cookie auth. The cookie SECRET is derived from
    # the SQLCipher passphrase per the planner's choice (or a separate
    # random secret cached in memory at startup). HttpOnly + SameSite=Strict
    # + Secure=False (HTTP-on-localhost) per D-57.
    #
    # IMPORTANT: SessionMiddleware must be added BEFORE any middleware
    # that needs to read request.session (e.g., the banner_state
    # middleware below) per Starlette's reverse-order execution rule.
    app.add_middleware(
        SessionMiddleware,
        secret_key=_get_session_secret(),  # derived from passphrase OR ephemeral
        session_cookie="gekko_session",
        max_age=8 * 3600,                  # 8-hour idle expiry
        same_site="strict",                # D-57
        https_only=False,                  # HTTP on localhost
        # HttpOnly is implied by the SessionMiddleware contract.
    )

    # ... existing static + routes + banner_state middleware ...
```

```python
# src/gekko/dashboard/routes.py — P3 NEW /login routes
@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("login.html.j2", {"request": request})

@router.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request, passphrase: str = Form(...),
) -> RedirectResponse:
    """Verify passphrase → cache it → mint session cookie."""
    settings = get_settings()
    db_path = settings.db_path_for(settings.gekko_user_id)
    # verify_passphrase opens a quick SQLCipher connection + tries a SELECT.
    from gekko.db.engine import verify_passphrase
    if not verify_passphrase(db_path, passphrase):
        raise HTTPException(status_code=401, detail="Incorrect passphrase.")
    # Cache in process; downstream engine factories read it.
    from gekko.vault.passphrase import set_passphrase
    set_passphrase(passphrase)
    request.session["user_id"] = settings.gekko_user_id
    request.session["logged_in_at"] = datetime.now(UTC).isoformat()
    return RedirectResponse(url="/approvals", status_code=303)


def require_session(request: Request) -> str:
    """FastAPI dependency: returns logged-in user_id or 401."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user_id
```

**Why not `fastapi-users`:** P3 has exactly one user + no registration + no password hashing + no magic-link. Adding `fastapi-users` would import a user-model + DB adapter for one cookie. P6 introduces it for real. Per D-57 the route shape stays unchanged across the swap.

### Anti-Patterns to Avoid

- **Awaiting DB or broker work BEFORE `ack()`** in any Slack handler. Pitfall 3 from P1; Slack's 3-second deadline is hard. P3 must preserve the `await ack()` FIRST invariant in every new handler including `@slack_app.view("edit_size_modal")`. For the view_submission case, the ack response can carry `response_action="errors"` — that ack itself is fast (no DB work), but the followup workflow (update qty + transition + dispatch executor) MUST be in `asyncio.create_task`.
- **Reading `strategy.mode` or `live_mode_eligible` at execute time** instead of from the LOCKED proposal row's `account_mode`. BLOCKER #5 / D-54 — edit-size MUST NOT re-derive account_mode; it inherits from the proposal row.
- **Treating the dedup table as the only defense.** D-41 is belt-AND-suspenders: dedup table for Slack/dashboard path, state-machine CHECK for CLI / future paths.
- **Persisting passphrase in the session cookie value.** D-25 / AUTH-04: never log or store credentials. The cookie stores `user_id` + `logged_in_at`; the passphrase lives in `gekko.vault.passphrase` module cache.
- **Storing `expires_at` in user-local time.** D-49: timer is UTC; only the quiet-hours predicate converts to user TZ. Mixing the two would create DST race conditions.
- **Using `chat.update` to delete the card.** D-53: grey it out, keep the audit-visible "this expired" surface. Replacing the actions block with a status line is the canonical "disabled" approach since Slack's `disabled` button attribute is not supported in Block Kit per [CITED: github.com/slackapi/bolt-js/issues/1891].
- **Letting the 60s sweep DM the operator inside the DB transaction.** The DM-after-transaction pattern is the P2-locked convention (executor `cap_rejection` branch line 601 comment); the sweep must mirror it.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Slack HMAC verification on POST `/slack/events` | Custom signature check | slack-bolt's `AsyncSlackRequestHandler` (already wired) | Already done in P1. Don't add a second verification layer. |
| Cookie signing | Custom HMAC + base64 | Starlette `SessionMiddleware` (itsdangerous-backed) | itsdangerous is the standard; SessionMiddleware is one-line config. [CITED: fastapi.tiangolo.com/advanced/middleware] |
| DST + timezone arithmetic | Custom `dateutil` parsing | `zoneinfo.ZoneInfo` + `astimezone()` (Python stdlib) | `zoneinfo` is the PEP-615 stdlib answer; `tzdata` is already pinned for Windows. |
| Slack at-least-once idempotency keys | Redis SETNX cluster | SQLCipher UNIQUE constraint on `slack_action_dedup` | D-41 chose belt-and-suspenders local idempotency; PROJECT.md forbids new infra. |
| Job persistence across restart | Self-written rerun-pending-jobs table | APScheduler's `SQLAlchemyJobStore` (already pinned + wired in P1 Plan 01-09) | Already in tree; the job-store auto-creates its `apscheduler_jobs` table on first `scheduler.start()`. |
| HTMX modal close animation | Custom CSS classes + JS | `HX-Trigger` response header + innerHTML swap to `#modal-mount` | Native HTMX pattern; matches the existing `/modal/close` route shape at `routes.py:347`. |
| Number input validation in Slack modal | Custom regex on submitted string | Block Kit `number_input` element + `view_submission` `response_action="errors"` | `number_input` enforces decimal-vs-integer + min_value at the client; `response_action="errors"` is the canonical re-render-with-error pattern. [CITED: docs.slack.dev/reference/block-kit/block-elements/#number] + [CITED: docs.slack.dev/surfaces/modals/#updating_views] |

**Key insight:** Every primitive this phase needs is already a stdlib or in-tree dependency. The only "new" code is the glue (dedup helper, expiry sweep, quiet-hours predicate, edit-size handler) plus the migration and templates. No new packages, no new processes to supervise.

## Runtime State Inventory

> Phase 3 is a feature addition, not a rename / refactor / migration. No live runtime state is being renamed. **However:** the schema additions touch existing rows (Proposal.expires_at column added — pre-migration proposals get a sentinel value), and APScheduler's persisted jobs table will gain two new job rows.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | (a) `apscheduler_jobs` SQLite table — auto-managed by APScheduler; restart-safe. P3 adds 2 new rows (`expire-stale-<user>` interval + `daily-pnl-<user>` cron). Both registered with `replace_existing=True` so they idempotently re-register on each lifespan startup. (b) `proposals` table — pre-P3 rows (~22 audit events from P1 demo) have no `expires_at`. Migration sets `expires_at = created_at + INTERVAL '30 minutes'` for backfill OR leaves null and the sweep treats null as "no expiry" (planner discretion — recommend backfill so the existing rows participate). | Alembic 0003 backfills `expires_at` for pre-migration rows; APScheduler job registration is idempotent at lifespan startup. |
| Live service config | None — no n8n / external webhook / Datadog / Tailscale state. The Slack app's slash commands and action handlers register against the bolt singleton at process startup; no Slack-side config changes (the actions `approve_proposal`, `reject_proposal`, `edit_size`, `escalate_to_dashboard` are already registered in P1; P3 only changes their handler bodies). | None. |
| OS-registered state | None for P3. (P7 adds launchd/NSSM service registration; not in scope.) | None. |
| Secrets / env vars | New: planner must decide whether the SessionMiddleware `secret_key` is (a) derived from the SQLCipher passphrase, (b) cached as an ephemeral random secret per process restart (causes existing cookies to invalidate on restart — acceptable per D-57's "8-hour idle expiry"), or (c) read from a new `GEKKO_SESSION_SECRET` env var. **Recommendation: (b) ephemeral random secret** — `secrets.token_urlsafe(64)` at startup. Operators logging in after a restart simply re-enter the passphrase. Matches the "type the passphrase at process start" mental model from D-19. | Planner picks (a/b/c); no new persistent secret if (b). |
| Build artifacts / installed packages | None — no new packages. No `*.egg-info` to invalidate. | None. |

**Nothing found in remaining categories:** Verified by grep over `src/gekko/`, `scripts/`, `migrations/`, and the project root for `n8n|tailscale|datadog|task\s*scheduler|nssm|launchd|systemd` — zero matches relevant to P3.

## Common Pitfalls

### Pitfall 1: Slack retry header on dedup-table second-write
**What goes wrong:** Slack delivers the same button-click payload twice (network blip during ack). The second delivery has `X-Slack-Retry-Num: 1` set. Our `claim_action` returns "duplicate"; we then need to send an ephemeral "already handled" via `respond_url` — but `respond_url` is the SAME URL on both deliveries. Sending the ephemeral twice would itself create double-noise.
**Why it happens:** [CITED: docs.slack.dev/interactivity/handling-user-interaction] — Slack retries up to 3 times on non-200 / non-3s-ack. The retry payload reuses `response_url`.
**How to avoid:** Read `request.headers.get("X-Slack-Retry-Num", "0")` and on `>= 1`, skip the ephemeral DM entirely — Slack already showed the user that their click failed to ack quickly enough, and our dedup row will be `result='duplicate'` for the audit log. Only on `Retry-Num == 0` (first delivery) AND `result='duplicate'` do we send the ephemeral.
**Warning signs:** Operator reports getting two "Already approved" ephemerals after one click.

### Pitfall 2: `respond_url` expires at 30 min
**What goes wrong:** The sweep expires a 30-min-old proposal at minute 30:01. The operator-clicked-Approve event sat in Slack's retry queue and lands at minute 30:30. Our handler tries to send an ephemeral via `respond_url` but Slack returns `expired_trigger_id` (~30 min window per [CITED: docs.slack.dev/interactivity/handling-user-interaction]).
**Why it happens:** `respond_url` is valid for 5 uses within 30 minutes; the sweep + the late click are racing the 30-min boundary.
**How to avoid:** Fallback to `_send_slack_dm` when `respond_url` returns 404 / expired. The DM path is identity-split-aware and persistent. Also: the dedup row + state-machine no-op already prevent the double-execution semantic; the ephemeral is purely UX.
**Warning signs:** Slack-bolt logs show `SlackApiError(expired_trigger_id)` from the dup-click branch.

### Pitfall 3: APScheduler 3.x `SQLAlchemyJobStore` + Windows + concurrent process double-fire
**What goes wrong:** Two `gekko serve` processes accidentally run on the same machine (operator double-launched). APScheduler 3.x's `SQLAlchemyJobStore` does NOT have process-level leader election — both schedulers will read the same `apscheduler_jobs` table and both will fire the 60s sweep. Two concurrent sweeps trying to transition the same proposal row will hit the `transition_status` CHECK and one will lose, but the duplicate DMs will fire.
**Why it happens:** APScheduler 3.x assumes single-process ownership of the job store [CITED: apscheduler.readthedocs.io/en/3.x/userguide.html §"Choosing the right scheduler"].
**How to avoid:** (a) The P2 walking-skeleton README + `gekko serve` already document single-process operation. (b) Use `max_instances=1` and `coalesce=True` per Pattern 2 so even within-process double-firing is impossible. (c) The OS-level supervision phase (P7) will add a PID-file lock; P3 just inherits the existing single-process assumption.
**Warning signs:** Duplicate expiry DMs; audit log shows two `expiration` events for the same proposal_id.

### Pitfall 4: SQLite `SELECT … FOR UPDATE` is parsed but advisory
**What goes wrong:** Operator writes `expire_stale_proposals()` with `.with_for_update()` expecting Postgres-style row locking. In SQLite (which is what SQLCipher wraps), `FOR UPDATE` is parsed but acts as a no-op — the actual concurrency control is at the BEGIN IMMEDIATE / BEGIN EXCLUSIVE transaction layer (WAL mode serializes writers).
**Why it happens:** SQLite docs note `FOR UPDATE` is accepted for compatibility but does nothing.
**How to avoid:** Trust the surrounding `session.begin()` transaction + WAL serialization, not `with_for_update()`. The single-process invariant from Pitfall 3 already prevents concurrent sweeps; the within-process `max_instances=1` prevents overlap; the transaction is the lock.
**Warning signs:** None at single-process; surfaces only if the multi-process invariant breaks.

### Pitfall 5: DST 2:30 AM doesn't exist; quiet-hours window 22:00–07:00 fires at "non-existent" time
**What goes wrong:** On spring-forward day, the local clock jumps 01:59 → 03:00. A user with quiet hours 22:00–07:00 expects "still in quiet hours" at 02:30 — but 02:30 doesn't exist that day. Our predicate is called at 02:30 UTC equivalent and returns False (because there's no 02:30 wall clock) — the agent loop fires when the operator expected silence.
**Why it happens:** `datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York')).time()` on spring-forward day's "missing hour" returns a `.time()` that's actually 03:30 (the wall clock skipped over 02:30). The window check (22:00–07:00 wrap-around) still returns True for 03:30 (because 03:30 < 07:00). So **actually our predicate handles this correctly** — the operator's expectation matches reality.
**How to avoid:** The predicate is correct as written. Document this in the predicate's docstring with a worked example so operators don't try to "fix" it.
**Warning signs:** Operator files a bug "agent fired at 2:30 AM on DST day" — the answer is "2:30 AM didn't exist, the agent fired at 3:30 AM which IS inside your 10pm-7am window."

### Pitfall 6: Cookie secret rotates on every restart, invalidating mid-session work
**What goes wrong:** P3 uses `secrets.token_urlsafe(64)` at startup for the `SessionMiddleware` secret_key (recommendation (b) in §Runtime State Inventory). Operator restarts `gekko serve` mid-session; their browser sends an old cookie that fails signature verification; they get a 302 to `/login`. If they had a half-filled edit-size form, they lose it.
**Why it happens:** Ephemeral secrets are the simplest UX but invalidate cookies on restart.
**How to avoid:** This is fine in P3 — restarts during operator session are rare (the operator typed the passphrase to start the process). The session cookie is for the dashboard fallback when Slack is wedged; "restart broke my dashboard session" is an acceptable trade for "no new persistent secret to manage." Mention in the operator-facing README.
**Warning signs:** Operator reports "I got logged out for no reason" after a restart.

### Pitfall 7: Slack `chat.update` requires the channel + ts of the original message
**What goes wrong:** The expiry sweep wants to grey out the proposal card, but the sweep only has `proposal_id`. The original `chat.postMessage` returned a `ts` and `channel` that we didn't persist anywhere. The sweep can't `chat.update` without them.
**Why it happens:** `gekko.reporter.slack.post_run_result()` posts the card and discards the `chat.postMessage` response.
**How to avoid:** Persist the Slack `ts` + `channel` on the proposal row at HITL-01 card post time. **Schema implication: Proposal table needs `slack_message_ts: str | null` and `slack_message_channel: str | null` columns.** Mention this in the planner's Alembic 0003 scope. The fall-back behavior when these are null (pre-migration rows) is to skip the chat.update and only send the separate DM per D-53 — the DM half is the load-bearing part.
**Warning signs:** Sweep logs "chat.update.skipped: missing ts/channel"; pre-migration proposals never have their card greyed out.

### Pitfall 8: `view_submission` ack body shape changes for response_action
**What goes wrong:** First-time slack-bolt author writes `await ack({"response_action": "errors"})` and Slack rejects with `invalid_arguments`. The correct shape requires both `response_action` AND the `errors` dict.
**Why it happens:** [CITED: docs.slack.dev/surfaces/modals — Updating views in response to submissions] — the ack payload for `response_action="errors"` must include the `errors` object keyed by `block_id` (NOT `action_id`).
**How to avoid:** Pattern 3 above shows the correct shape: `{"response_action": "errors", "errors": {"qty_block": "..."}}`. Test the modal validation path with a real Slack workspace + cassette before shipping.
**Warning signs:** Slack-bolt logs `views_submission failed; response_action='errors' rejected`.

### Pitfall 9: `_send_slack_dm` ignores quiet-hours wrapper if called via `from import`
**What goes wrong:** P3 introduces `_send_slack_dm_respecting_quiet_hours(user_id, text, category)` as the new public seam. A future contributor adds a new fill DM but uses `from gekko.execution.executor import _send_slack_dm` and bypasses the quiet-hours gate.
**Why it happens:** Two seams with similar names; the original is still exported.
**How to avoid:** AST gate — `tests/unit/test_quiet_hours_dm_gate.py` walks every `.py` file under `src/gekko/`, finds `_send_slack_dm` call sites, and asserts each is either (a) inside an executor-error branch (audited list of files+line ranges), (b) inside the kill_switch module (bypass per D-48 #1), or (c) inside the daily P&L cron (bypass per D-48). Any other call site MUST go through `_send_slack_dm_respecting_quiet_hours`.
**Warning signs:** Operator reports getting a routine fill DM at 2 AM. Audit log shows the silent bypass via wrong import path.

## Code Examples

Verified patterns from official sources (selected; see `Pattern N` sections above for full snippets).

### Slack views.open async — modal opening
```python
# Source: https://tools.slack.dev/bolt-python/concepts/opening-modals/
await client.views_open(
    trigger_id=body["trigger_id"],
    view={
        "type": "modal",
        "callback_id": "edit_size_modal",
        "private_metadata": json.dumps({...}),
        "title": {"type": "plain_text", "text": "Edit size"},
        "submit": {"type": "plain_text", "text": "Approve at this size"},
        "blocks": [...],
    },
)
```

### Slack number_input Block Kit element
```python
# Source: https://docs.slack.dev/reference/block-kit/block-elements/#number
{
    "type": "input",
    "block_id": "qty_block",
    "label": {"type": "plain_text", "text": "New quantity"},
    "element": {
        "type": "number_input",
        "action_id": "qty_input",
        "initial_value": "47",
        "is_decimal_allowed": True,
        "min_value": "0",
    },
}
```

### Slack view_submission response_action="errors"
```python
# Source: https://docs.slack.dev/surfaces/modals/#updating_views
# Keyed by block_id (NOT action_id).
await ack({
    "response_action": "errors",
    "errors": {
        "qty_block": "Drift 3.2% exceeds the 2% safety bound. Adjust qty.",
    },
})
```

### APScheduler IntervalTrigger with restart safety
```python
# Source: https://apscheduler.readthedocs.io/en/3.x/userguide.html
scheduler.add_job(
    "gekko.approval.expiry:expire_stale_proposals",
    IntervalTrigger(seconds=60),
    kwargs={"user_id": user_id},
    id="expire-stale-<user_id>",
    replace_existing=True,   # idempotent re-registration on lifespan startup
    coalesce=True,           # collapse missed runs into one
    max_instances=1,         # never overlap (sweep is idempotent but DMs aren't)
    misfire_grace_time=300,  # forgive misses up to 5 min
)
```

### APScheduler CronTrigger with IANA timezone (DST-aware)
```python
# Source: https://apscheduler.readthedocs.io/en/3.x/userguide.html + zoneinfo
from zoneinfo import ZoneInfo
scheduler.add_job(
    "gekko.reporter.daily_pnl:send_daily_digest",
    CronTrigger(hour=16, minute=30, timezone=ZoneInfo("America/New_York")),
    kwargs={"user_id": user_id},
    id="daily-pnl-<user_id>",
    replace_existing=True,
    misfire_grace_time=900,  # forgive 15-min misses
)
# DST is handled automatically by CronTrigger when timezone is a ZoneInfo:
# 4:30 PM Eastern fires at 4:30 PM regardless of EST/EDT.
```

### FastAPI SessionMiddleware
```python
# Source: https://fastapi.tiangolo.com/advanced/middleware/
from starlette.middleware.sessions import SessionMiddleware
app.add_middleware(
    SessionMiddleware,
    secret_key=_get_session_secret(),
    session_cookie="gekko_session",
    max_age=8 * 3600,
    same_site="strict",
    https_only=False,  # HTTP on localhost
)
# In a route: request.session["user_id"] = "..."
# To clear: request.session.clear()
```

### HTMX modal open + close (innerHTML + HX-Trigger)
```html
<!-- Open: button → fragment swapped into #modal-mount -->
<!-- Source: https://htmx.org/examples/modal-custom/ -->
<button hx-get="/approvals/{{ id }}/edit-modal"
        hx-target="#modal-mount"
        hx-swap="innerHTML">Edit</button>
<div id="modal-mount"></div>

<!-- Close: server returns empty + HX-Trigger header -->
<!-- Response: 200 "" with HX-Trigger: closeModal -->
```

### SQLAlchemy IntegrityError handler for UNIQUE clash
```python
# Source: Phase 1 Plan 01-07 ProposalWriter pattern (proven in tree).
from sqlalchemy.exc import IntegrityError
try:
    session.add(SlackActionDedup(...))
    await session.flush()
    return "first_write"
except IntegrityError:
    await session.rollback()
    return "duplicate"
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Phase 1 stub `handle_edit_size_stub` DMs "coming in Phase 3" | Slack modal via `views.open` with number_input + drift check via `response_action="errors"` | This phase | Edit-size becomes a single-interaction approve flow per D-54 |
| Phase 1 single-channel approve only | Phase 2 dual-channel for first live (`AWAITING_2ND_CHANNEL`) | P2 (shipped) | P3 preserves; adds dedup layer in front |
| Phase 1 no proposal expiry — proposals sit PENDING forever | Phase 3 60s APScheduler sweep + per-strategy timeout column | This phase | Stale proposals auto-reject after 30 min default |
| Phase 1 no idempotency table — relied on state-machine no-op only | Phase 3 belt-and-suspenders: `slack_action_dedup` UNIQUE + state-machine CHECK | This phase | Slack at-least-once delivery cannot cause double-execution; D-43 ephemeral feedback |
| Phase 1 dashboard had no /approvals page | Phase 3 `/approvals` mirrors Slack 1:1 | This phase | Fallback approval surface when Slack is wedged |
| Phase 1 dashboard had no auth | Phase 3 SessionMiddleware signed cookie + passphrase prompt | This phase | Localhost-only single-operator session; P6 swaps to magic-link |
| Phase 1 no quiet hours — DMs land at 2 AM | Phase 3 IANA-tz quiet-hours predicate gates loop + routine DMs | This phase | No 2am pings; kill / error / first-live always fire (D-48) |
| Phase 1 no daily P&L digest | Phase 3 4:30 PM ET cron job sends digest DM | This phase | Operator gets close-of-day summary |

**Deprecated/outdated:**
- The `handle_edit_size_stub` and `handle_escalate_stub` functions in `src/gekko/approval/slack_handler.py` lines 414+434 are deprecated. P3 replaces `handle_edit_size_stub` with the real handler per Pattern 3. `handle_escalate_stub` remains a stub for now — D-55 makes the dashboard mirror the Slack surface, so "escalate" is essentially "open the dashboard" — the button can become a Slack URL button pointing at `/approvals/{proposal_id}` (no action_id needed). **Planner discretion** whether to ship the escalate button as a URL button in P3 or punt to P6.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The Slack `chat.postMessage` `ts` and `channel` from the HITL-01 card post are NOT currently persisted on the Proposal row. | §Pitfall 7 | Sweep cannot grey out the expired card. **Mitigation:** verify by reading `gekko.reporter.slack.post_run_result()` and confirming the return value is discarded. If wrong (and we already store ts/channel), the chat.update path is already enabled. If correct, schema migration must add the two columns. | [ASSUMED] — based on reading `reporter/slack.py:post_run_result()` which calls `chat_postMessage(...)` without persisting the returned `ts`. |
| A2 | Operator's deployment is single-process (`gekko serve` launches one uvicorn worker with `workers=1`). | §Pitfall 3, §HITL-03 | If multi-process, sweep + cron + agent-loop jobs would double-fire. **Mitigation:** README + lifespan log a warning if a PID file exists. P7 will harden this. | [VERIFIED: `src/gekko/dashboard/app.py:30-33` docstring "uvicorn with `workers=1` and no `--reload` flag"] |
| A3 | Daily P&L digest fires at exactly 4:30 PM ET regardless of whether the market closed early (half-day holidays). | §REPT-01 | On half-day (Black Friday) the market closes at 1:00 PM ET; firing at 4:30 PM ET still works but operators may expect 1:30 PM. **Mitigation:** planner can add `pandas_market_calendars` half-day awareness — the calendar is already cached in `gekko.execution.market_hours`. | [ASSUMED] — D-48 / E says "4:30pm post-close ET regardless"; this matches the simpler interpretation. The planner / UI-phase can refine. |
| A4 | The dedup table lives in the per-user SQLCipher DB, not in a shared DB. | §HITL-02 | Multi-user-deployment-later assumption — the per-user-isolated v1 deployment shape per PROJECT.md means there is one DB per user; the dedup table doesn't need a `user_id` column for partitioning. **Mitigation:** the SlackActionDedup schema below INCLUDES `actor_gekko_user_id` for audit, but the partition is by-DB-file (each user has their own SQLCipher DB). | [CITED: PROJECT.md "per-user-isolated deployment" + STATE.md "Per-user isolated deployment (selected)"] |
| A5 | `_send_slack_dm_respecting_quiet_hours(user_id, text, category)` is a new wrapper introduced in P3; the existing `_send_slack_dm` seam stays as the lowest-level transport. | §Pitfall 9, §code_context (CONTEXT.md) | If a future plan tries to add quiet-hours logic INSIDE `_send_slack_dm`, it would gate the bypass categories too. **Mitigation:** the wrapper is a wrapping function; the seam stays untouched. CONTEXT.md `code_context` already locks this. | [CITED: 03-CONTEXT.md §"Reusable Assets" — "P3 introduces a quiet-hours-aware wrapper `_send_slack_dm_respecting_quiet_hours(user_id, text, category)`"] |
| A6 | `_PROPOSAL_STATUSES` in `models.py` already contains the string `'EXPIRED'` via Phase 2's frozen vocabulary — but the migration's CHECK constraint at `0002_orderguard.py:_FROZEN_PROPOSAL_STATUSES` also includes it. | §Architecture Patterns | If `'EXPIRED'` is NOT in the DB-level CHECK, the migration must extend it. **Mitigation:** grep confirms `'EXPIRED'` is not in `_PROPOSAL_STATUSES` in `models.py:53-62`. **THE PLANNER MUST add `'EXPIRED'` in Alembic 0003 + extend `_PROPOSAL_STATUSES` in `models.py`.** | [VERIFIED: re-read `src/gekko/db/models.py:53-62` — 8 statuses listed; 'EXPIRED' NOT among them. But `STATE_TRANSITIONS` in `approval/proposals.py:80-101` DOES reference EXPIRED (`AWAITING_2ND_CHANNEL → EXPIRED`). This means the state-machine accepts EXPIRED but the DB CHECK would currently REJECT a write to it.] |
| A7 | The dual-channel `AWAITING_2ND_CHANNEL → EXPIRED` edge that Phase 2 reserved in `STATE_TRANSITIONS` is intentionally also covered by the P3 sweep — meaning AWAITING_2ND_CHANNEL proposals that the operator never confirms on the dashboard within 30 min also auto-expire. | §HITL-03 | If the operator is on vacation and forgot to ack a first-live within 30 min, the trade auto-expires — this is the DESIRED behavior per D-53 + the P2 first-live design. **Mitigation:** Document explicitly in the sweep's docstring + add an integration test for "AWAITING_2ND_CHANNEL → EXPIRED" path. | [VERIFIED: `src/gekko/approval/proposals.py:97-98` lists `("AWAITING_2ND_CHANNEL", "EXPIRED")` in STATE_TRANSITIONS frozenset. So the edge exists; the sweep just needs to include AWAITING_2ND_CHANNEL in its WHERE clause. **Refinement to D-50:** sweep query is `WHERE status IN ('PENDING', 'AWAITING_2ND_CHANNEL') AND expires_at <= now()`.] |
| A8 | The session cookie secret rotates on every process restart (recommendation b in §Runtime State Inventory). | §Pattern 6, §Pitfall 6 | Operator who restarts mid-session loses their cookie. **Mitigation:** Acceptable per D-57 / Pitfall 6 (8-hour idle expiry; restart-during-session is rare; UX cost is "re-enter passphrase"). Planner may choose to derive from passphrase instead. | [ASSUMED] — Planner-discretion choice within D-57 constraint. |
| A9 | CONTEXT.md's references to "APScheduler 4.x" should be read as "APScheduler 3.x" (the installed version). The 3.x → 4.x API differences are substantial (job-store config, scheduler subclasses), and CONTEXT.md's behavioral expectations (interval trigger, persistent job store, restart semantics) are 3.x-compatible. | §Summary (Critical Discrepancy) | If the planner takes "4.x" literally and tries to upgrade, P3 would also need to refactor `scheduler/jobs.py` + `dashboard/app.py` lifespan wiring — outside scope. **Mitigation:** the version pin `apscheduler>=3.10,<4` in `pyproject.toml` is the authoritative answer. | [VERIFIED: `pyproject.toml` + installed `apscheduler-3.11.2.dist-info`] |

**If this table is empty:** Not applicable — 9 assumptions logged. The planner / discuss-phase should review A6 (the `'EXPIRED'` enum extension), A7 (the AWAITING_2ND_CHANNEL inclusion in sweep), and A1 (the missing `slack_message_ts/channel` columns) before drafting plans — these are the items that change the migration scope.

## Open Questions

1. **Should the 60s sweep iterate over all users on a multi-user-later deployment?**
   - What we know: P3 ships in the single-user-per-instance deployment shape (PROJECT.md / D-18). The sweep is registered per-user in the lifespan startup.
   - What's unclear: When the dashboard is later opened to multiple users (P6), does each user get their own sweep job? Or one sweep that iterates over all user DBs?
   - Recommendation: One sweep job per user is the cleanest model; APScheduler can host N IntervalTriggers with distinct job ids. Defer the answer until P6 actually adds multiple users.

2. **Daily P&L digest on market-closed days — empty DM or skip entirely?**
   - What we know: D-48 / E says "4:30pm post-close ET regardless"; the operator gets a digest every weekday.
   - What's unclear: On holidays (Christmas Day on a Wednesday, Thanksgiving Thursday) the market is closed all day. Should the digest still fire (saying "no trades today; market closed")?
   - Recommendation: Use `pandas_market_calendars.get_calendar('NYSE').valid_days(...)` to check; on closed days, skip entirely (no DM). The operator's mental model is "no market = no digest." Worth confirming with the operator in `/gsd-discuss-phase` follow-up if not locked.

3. **Cookie secret derivation — passphrase-derived vs ephemeral random?**
   - What we know: D-57 says "8-hour idle expiry, HttpOnly, SameSite=Strict, Secure=False." Says nothing about secret rotation.
   - What's unclear: Whether the planner picks recommendation (a), (b), or (c) from §Runtime State Inventory.
   - Recommendation: (b) ephemeral random per restart — simplest, no new env var, acceptable UX cost. Mention in the operator-facing README.

4. **Should the `escalate_to_dashboard` button become a URL button to `/approvals/{proposal_id}` in P3, or stay a stub?**
   - What we know: D-55 mirrors the Slack card on `/approvals`. There's no longer a clear distinction between "approve in Slack" and "escalate to dashboard" — they're the same surface.
   - What's unclear: Whether to ship the URL-button version in P3 or defer.
   - Recommendation: Ship the URL-button version in P3 — it's a 5-line change to the `build_proposal_card` and removes a confusing stub. Falls under Claude's discretion.

5. **Should pre-migration proposals (~22 audit events from P1 demo) be backfilled with `expires_at`?**
   - What we know: Alembic 0003 adds `expires_at` to `proposals`. Pre-migration rows have NULL.
   - What's unclear: Backfill with `created_at + 30min` (would immediately expire them on first sweep) vs leave NULL (sweep treats as "no expiry, never expire").
   - Recommendation: Leave NULL + sweep treats as "no expiry." The 22 P1-demo events are FILLED / FAILED / REJECTED terminal states already (not PENDING). For any defensive PENDING row from a crash, the operator can manually reject via the dashboard.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | Runtime | ✓ | 3.12.x | — |
| `slack-bolt` 1.28.0 | view_submission listener, `views.open` modal | ✓ | 1.28.0 | — |
| `apscheduler` 3.11.2 | IntervalTrigger sweep + CronTrigger daily | ✓ | 3.11.2 | — |
| `fastapi` 0.115+ | New routes, SessionMiddleware | ✓ | (Phase 1) | — |
| `starlette` SessionMiddleware | Cookie auth | ✓ | (Phase 1 transitive) | — |
| `itsdangerous` | Cookie signing | ✓ | (transitive) | — |
| `python-zoneinfo` (stdlib) | IANA tz lookup | ✓ | stdlib | — |
| `tzdata` PyPI package | Windows zoneinfo backing | ✓ | (Phase 1 pin) | — |
| HTMX 2.0.4 vendored | Modal swap pattern | ✓ | (Phase 1 vendored) | — |
| SQLCipher (sqlcipher3-wheels) | Per-user DB + dedup table | ✓ | 0.5.7+ | — |
| `alembic` | Migration | ✓ | (Phase 1) | — |
| Alpaca account + market data | (For drift-check ref_price fetch) | ✓ | (Phase 1 wired) | — |
| Slack workspace + bot token | Real Slack tests | ✓ | (operator-provisioned) | Cassettes for unit/integration |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** none.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio + respx + freezegun |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` (Phase 1 wired) |
| Quick run command | `uv run pytest tests/unit/test_*phase3*.py -x` |
| Full suite command | `uv run pytest -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| HITL-02 | First-write claim_action returns 'first_write' | unit | `pytest tests/unit/test_slack_action_dedup.py::test_first_click_first_write -x` | ❌ Wave 0 |
| HITL-02 | Second-write claim_action returns 'duplicate' + raises IntegrityError handled | unit | `pytest tests/unit/test_slack_action_dedup.py::test_second_click_duplicate -x` | ❌ Wave 0 |
| HITL-02 | Slack retry with X-Slack-Retry-Num >= 1 skips ephemeral DM | unit | `pytest tests/unit/test_slack_retry_header.py -x` | ❌ Wave 0 |
| HITL-02 | Approve + Reject race resolves first-write-wins | integration | `pytest tests/integration/test_dedup_race.py -x` (cassette) | ❌ Wave 0 |
| HITL-03 | expire_stale_proposals transitions PENDING → EXPIRED | unit | `pytest tests/unit/test_expire_stale_proposals.py::test_basic_sweep -x` (freezegun) | ❌ Wave 0 |
| HITL-03 | Sweep skips proposals with expires_at > now | unit | `pytest tests/unit/test_expire_stale_proposals.py::test_skips_unexpired -x` | ❌ Wave 0 |
| HITL-03 | Sweep covers AWAITING_2ND_CHANNEL → EXPIRED (A7) | unit | `pytest tests/unit/test_expire_stale_proposals.py::test_awaiting_2nd_channel_expires -x` | ❌ Wave 0 |
| HITL-03 | APScheduler IntervalTrigger restart safety (coalesce + max_instances) | integration | `pytest tests/integration/test_sweep_persistence.py -x` (subprocess restart) | ❌ Wave 0 |
| HITL-03 | Strategy.proposal_timeout_minutes override picked up at proposal-build | unit | `pytest tests/unit/test_proposal_writer_timeout.py -x` | ❌ Wave 0 |
| HITL-03 | chat.update of expired card succeeds when slack_message_ts present | unit | `pytest tests/unit/test_chat_update_expired.py -x` (respx) | ❌ Wave 0 |
| HITL-05 | _resolve_quiet_hours returns True inside overnight window 22:00-07:00 | unit | `pytest tests/unit/test_quiet_hours_predicate.py::test_overnight_in_window -x` | ❌ Wave 0 |
| HITL-05 | _resolve_quiet_hours returns False outside window | unit | `pytest tests/unit/test_quiet_hours_predicate.py::test_outside_window -x` | ❌ Wave 0 |
| HITL-05 | _resolve_quiet_hours strategy override wins over user default | unit | `pytest tests/unit/test_quiet_hours_predicate.py::test_strategy_override -x` | ❌ Wave 0 |
| HITL-05 | _resolve_quiet_hours DST spring-forward boundary | unit | `pytest tests/unit/test_quiet_hours_predicate.py::test_dst_spring_forward -x` (freezegun + ZoneInfo) | ❌ Wave 0 |
| HITL-05 | _resolve_quiet_hours DST fall-back boundary (fold disambiguation) | unit | `pytest tests/unit/test_quiet_hours_predicate.py::test_dst_fall_back -x` | ❌ Wave 0 |
| HITL-05 | Bypass categories DM during quiet hours (kill, error, first-live) | unit | `pytest tests/unit/test_dm_bypass_categories.py -x` | ❌ Wave 0 |
| HITL-05 | Routine fill DM suppressed during quiet hours | unit | `pytest tests/unit/test_dm_routine_suppressed.py -x` | ❌ Wave 0 |
| HITL-05 | Scheduler skips trigger_strategy_run during quiet hours | integration | `pytest tests/integration/test_scheduler_quiet_hours.py -x` | ❌ Wave 0 |
| DASH-04 | GET /login renders passphrase form | unit | `pytest tests/unit/test_dashboard_login.py::test_get_login -x` | ❌ Wave 0 |
| DASH-04 | POST /login with correct passphrase mints session cookie | unit | `pytest tests/unit/test_dashboard_login.py::test_post_login_success -x` | ❌ Wave 0 |
| DASH-04 | POST /login with wrong passphrase → 401 | unit | `pytest tests/unit/test_dashboard_login.py::test_post_login_wrong_passphrase -x` | ❌ Wave 0 |
| DASH-04 | GET /approvals without session → redirect to /login | unit | `pytest tests/unit/test_dashboard_approvals.py::test_unauth_redirects -x` | ❌ Wave 0 |
| DASH-04 | GET /approvals with session lists PENDING proposals | unit | `pytest tests/unit/test_dashboard_approvals.py::test_lists_pending -x` | ❌ Wave 0 |
| DASH-04 | POST /approvals/{id}/approve transitions PENDING → APPROVED + dispatches | integration | `pytest tests/integration/test_dashboard_approve_flow.py -x` | ❌ Wave 0 |
| DASH-04 | POST /approvals/{id}/edit-submit with drift > 2% returns error fragment | unit | `pytest tests/unit/test_dashboard_edit_size.py::test_drift_rejected -x` | ❌ Wave 0 |
| DASH-04 | POST /approvals/{id}/edit-submit with drift ≤ 2% transitions + closes modal | integration | `pytest tests/integration/test_dashboard_edit_size_happy.py -x` | ❌ Wave 0 |
| DASH-04 | Same proposal-card schema renders in Slack + dashboard (snapshot) | unit | `pytest tests/unit/test_proposal_card_shared_partial.py -x` | ❌ Wave 0 |
| REPT-01 | Daily P&L cron query aggregates today's fills + rejections | unit | `pytest tests/unit/test_daily_pnl_aggregation.py -x` | ❌ Wave 0 |
| REPT-01 | Daily P&L digest bypasses user quiet hours | unit | `pytest tests/unit/test_daily_pnl_bypasses_quiet.py -x` | ❌ Wave 0 |
| REPT-01 | Severity-tier emoji prefix on error DMs | unit | `pytest tests/unit/test_severity_tier_dm.py -x` | ❌ Wave 0 |
| REPT-01 | Audit: MarketClosed (line 454) + BrokerOrderError (line 654) both DM | unit | `pytest tests/unit/test_executor_error_dms_coverage.py -x` | ❌ Wave 0 |
| All | AST gate: every `_send_slack_dm` call site classified | unit | `pytest tests/unit/test_quiet_hours_dm_gate.py -x` | ❌ Wave 0 (per Pitfall 9) |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/unit/test_*phase3*.py -x` (~5s)
- **Per wave merge:** `uv run pytest tests/unit -x && uv run pytest tests/integration -x` (~60s incl. cassettes)
- **Phase gate:** Full suite green + manual walking-skeleton demo (operator clicks dup Approve in real Slack; expiry sweep observed; quiet hours observed across a midnight wall-clock crossing).

### Wave 0 Gaps

The phase introduces ~30 new test files. All listed in the table above. Specifically:

- [ ] `tests/unit/test_slack_action_dedup.py` — first-write / duplicate behavior, IntegrityError handling
- [ ] `tests/unit/test_slack_retry_header.py` — X-Slack-Retry-Num gating of ephemeral
- [ ] `tests/unit/test_expire_stale_proposals.py` — sweep correctness with freezegun
- [ ] `tests/unit/test_quiet_hours_predicate.py` — overnight wrap + DST corner cases
- [ ] `tests/unit/test_dm_bypass_categories.py` — kill/error/first-live always fire
- [ ] `tests/unit/test_dm_routine_suppressed.py` — routine fill suppression during quiet hours
- [ ] `tests/unit/test_dashboard_login.py` — passphrase prompt + cookie mint
- [ ] `tests/unit/test_dashboard_approvals.py` — index renders + auth redirect
- [ ] `tests/unit/test_dashboard_edit_size.py` — drift error fragment, happy-path modal close
- [ ] `tests/unit/test_proposal_card_shared_partial.py` — Slack-card schema parity
- [ ] `tests/unit/test_daily_pnl_aggregation.py` — query + render
- [ ] `tests/unit/test_severity_tier_dm.py` — emoji prefix
- [ ] `tests/unit/test_executor_error_dms_coverage.py` — line 454 + line 654 covered
- [ ] `tests/unit/test_chat_update_expired.py` — respx mock of Slack chat.update
- [ ] `tests/unit/test_quiet_hours_dm_gate.py` — AST walk over `_send_slack_dm` call sites (per Pitfall 9)
- [ ] `tests/unit/test_proposal_writer_timeout.py` — Strategy.proposal_timeout_minutes stamped on expires_at at T0
- [ ] `tests/integration/test_dedup_race.py` — cassette: approve + edit race
- [ ] `tests/integration/test_sweep_persistence.py` — subprocess restart + sweep coalesce
- [ ] `tests/integration/test_dashboard_approve_flow.py` — full HTMX cycle ending in executor dispatch
- [ ] `tests/integration/test_dashboard_edit_size_happy.py` — HTMX modal end-to-end happy path
- [ ] `tests/integration/test_scheduler_quiet_hours.py` — APScheduler trigger_strategy_run skipped in window

**Framework install:** none — pytest infrastructure already exists.

**Cassette + fixture additions:** `tests/conftest.py` may need a `quiet_hours_user` fixture (a User row with `quiet_hours_start=time(22,0), quiet_hours_end=time(7,0), timezone='America/New_York'`) and a `expired_proposal` fixture (Proposal with `status='PENDING'`, `expires_at` in the past).

**Manual / wall-clock evidence required:**

| Item | Why manual | When |
|------|-----------|------|
| Real Slack dup-click survives at-least-once delivery | Wall-clock + real Slack retry | End-of-phase demo |
| 60s sweep latency observed on real wall clock | Cannot mock APScheduler clock + Slack at same time | End-of-phase demo |
| Quiet hours predicate crosses a real DST boundary | freezegun + ZoneInfo together is fragile across DST; real clock is the truth | Defer to Spring 2027 OR run a CI job with date set to 2027-03-13 (DST day) |
| Dashboard `/approvals` end-to-end in browser | HTMX behavior + cookie + real Slack-down scenario | End-of-phase demo |

**AST gates for invariants (per Pitfall 9 + the Phase 2 plan-02-03 / 02-06 grep-gate convention):**

| Gate | What it asserts | File |
|------|----------------|------|
| Every `_send_slack_dm(...)` call site is classified | Routine-category calls go through `_send_slack_dm_respecting_quiet_hours`; bypass-category calls go direct | `tests/unit/test_quiet_hours_dm_gate.py` |
| Every `transition_status(...)` caller catches the CHECK violation correctly | `ValueError` is propagated or handled deliberately; no silent swallow | `tests/unit/test_transition_status_callers.py` |
| `@slack_app.action("edit_size")` handler does NOT call `place_order` directly | Edit-size goes through the state machine + executor | `tests/unit/test_edit_size_not_direct_broker.py` |
| `expire_stale_proposals` import chain does NOT import `claude_agent_sdk` | Sweep is deterministic Python firewall — no LLM | `tests/unit/test_expiry_no_sdk_import.py` |
| `SessionMiddleware` is added BEFORE the banner-state middleware in `create_app` | Starlette reverse-order execution rule | `tests/unit/test_dashboard_middleware_order.py` |

## Security Domain

`security_enforcement: true` per `.planning/config.json`; ASVS Level 1 is the target.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Session cookie via SessionMiddleware (itsdangerous-signed); passphrase verification via `gekko.db.engine.verify_passphrase`. Single operator per instance per D-18 / D-57. |
| V3 Session Management | yes | `SessionMiddleware` with `max_age=8*3600`, `same_site="strict"`, `https_only=False` (HTTP-on-localhost-only), `HttpOnly` implied. Session cleared on POST `/logout` (new route — planner discretion). |
| V4 Access Control | yes | Cross-user defense from P1 preserved: Slack handlers check `body["user"]["id"] == settings.slack_user_id`; dashboard routes check `request.session["user_id"] == settings.gekko_user_id`. |
| V5 Input Validation | yes | Slack number_input enforces decimal-vs-integer + min_value; view_submission body validated via Pydantic before processing. Dashboard edit-size form re-validates server-side. |
| V6 Cryptography | yes | itsdangerous (cookie signing) + SQLCipher (DB-at-rest). No new crypto code. |
| V7 Errors & Logging | yes | structlog credential-redaction (`_REDACT_KEYS` extended with `passphrase` and `session_cookie` — per CONTEXT.md `code_context`). |
| V8 Data Protection | yes | SQLCipher whole-DB encryption (existing). Dedup table + expires_at column inherit it. |
| V9 Communications | yes | HTTP on localhost only (D-57). Slack HMAC verification handled by slack-bolt. |
| V10 Malicious Code | yes | Decision agent's tool list does NOT include any of the new P3 surfaces (handler functions, sweep, daily P&L). The LLM has zero path into approving/rejecting/expiring proposals (preserved P1 / P2 invariant). |

### Known Threat Patterns for {Slack + FastAPI + SQLCipher stack}

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Slack at-least-once delivery causes double-execution | Repudiation / Tampering | dedup table UNIQUE constraint + state-machine no-op (D-41) |
| Slack request spoof | Spoofing | slack-bolt automatic HMAC verification (P1 + preserved) |
| Slack callback for proposal owned by another user | Information Disclosure | Cross-user check `body["user"]["id"] == settings.slack_user_id` (P1 + preserved) |
| Dashboard cookie tamper | Tampering | itsdangerous signature verification on every request |
| Dashboard CSRF | Tampering | `SameSite=Strict` cookie (D-57) + form-only POST endpoints |
| Edit-size bypasses OrderGuard 2% drift | Tampering / Elevation of privilege | Drift check re-applied in `handle_edit_size_submit` BEFORE state transition (D-54 step b) |
| Sweep races with operator click at minute 30:00 | Data race | First-write-wins via `transition_status` CHECK + dedup table (D-53 edge case) |
| Quiet-hours bypass via routine-fill DM during 2 AM | Information Disclosure (operator sleep) | `_send_slack_dm_respecting_quiet_hours` wrapper + AST gate (Pitfall 9) |
| Cookie secret leak via `SECRET_KEY` env var commit | Information Disclosure | Ephemeral random per restart (Assumption A8) — no persistent secret to leak |
| LLM-influenced edit-size value | Spoofing | LLM has NO path to the edit-size flow; the modal is operator-initiated. The qty in `tp.target_notional_usd` was LLM-authored at proposal time (Phase 2 ProposalWriter) but the operator's modal-submitted qty is operator input, not LLM. |
| Slack-message `ts` / `channel` persisted on proposal row could leak in audit dump | Information Disclosure | Both fields are operational metadata, not PII. Standard `__repr__` exclusion sufficient. |

## Sources

### Primary (HIGH confidence)

- **slack-bolt-python — async modal opening + view_submissions:** [tools.slack.dev/bolt-python/concepts/opening-modals](https://tools.slack.dev/bolt-python/concepts/opening-modals/) — modal `views.open`, `private_metadata`, `callback_id` matching with `@app.view()`.
- **slack-bolt-python — view_submission listener + response_action:** [tools.slack.dev/bolt-python/concepts/view_submissions](https://tools.slack.dev/bolt-python/concepts/view_submissions/) — `response_action="errors"`, error dict keyed by `block_id`.
- **slack-bolt-python — lazy listener / 3-second ack contract:** [docs.slack.dev/tools/bolt-python/concepts/lazy-listeners](https://docs.slack.dev/tools/bolt-python/concepts/lazy-listeners/) — ack-first invariant; lazy function pattern. P3 reuses the existing `asyncio.create_task` pattern from P1, not the lazy listener (which is for FaaS).
- **Slack interactivity / at-least-once delivery:** [docs.slack.dev/interactivity/handling-user-interaction](https://docs.slack.dev/interactivity/handling-user-interaction/) — `X-Slack-Retry-Num` header, 3-second timeout, retry up to 3 times.
- **Slack response_url / ephemeral lifetime:** [docs.slack.dev/interactivity/handling-user-interaction](https://docs.slack.dev/interactivity/handling-user-interaction/) — 5 uses within 30 minutes; falls back to chat.postMessage afterward.
- **Slack chat.update:** [docs.slack.dev/reference/methods/chat.update](https://docs.slack.dev/reference/methods/chat.update/) + [docs.slack.dev/messaging/modifying-messages](https://docs.slack.dev/messaging/modifying-messages/) — required scope `chat:write`, channel+ts required.
- **Slack number_input element:** [docs.slack.dev/reference/block-kit/block-elements](https://docs.slack.dev/reference/block-kit/block-elements/) — `is_decimal_allowed`, `min_value`, `initial_value`.
- **Slack modals — validating submissions:** [docs.slack.dev/surfaces/modals](https://docs.slack.dev/surfaces/modals/) — `response_action="errors"` for re-render with error block.
- **APScheduler 3.x user guide:** [apscheduler.readthedocs.io/en/3.x/userguide.html](https://apscheduler.readthedocs.io/en/3.x/userguide.html) — `coalesce`, `max_instances`, `misfire_grace_time`, `SQLAlchemyJobStore`, restart semantics. (3.x is the installed version — see §Summary Critical Discrepancy.)
- **Python zoneinfo:** [docs.python.org/3/library/zoneinfo.html](https://docs.python.org/3/library/zoneinfo.html) — IANA tz lookup, `available_timezones()`, DST handling, `fold` parameter for fall-back ambiguity.
- **FastAPI middleware (SessionMiddleware):** [fastapi.tiangolo.com/advanced/middleware](https://fastapi.tiangolo.com/advanced/middleware/) — Starlette SessionMiddleware with itsdangerous.
- **HTMX modal pattern:** [htmx.org/examples/modal-custom](https://htmx.org/examples/modal-custom/) — `hx-target` + `hx-swap` for modal mount + HX-Trigger response header for server-driven close.

### Secondary (MEDIUM confidence — verified with above primary sources)

- **DST handling strategies in Python:** [dev.to/outdated-dev/daylight-saving-time-handling-strategies](https://dev.to/outdated-dev/daylight-saving-time-handling-strategies-a-guide-for-c-and-python-developers-2oe0) — confirms spring-forward / fall-back behavior with zoneinfo.
- **Slack idempotency + retry header in practice:** [questionbase.com/resources/blog/slack-api-integration-handling-errors-retries](https://www.questionbase.com/resources/blog/slack-api-integration-handling-errors-retries) — operational guidance on `X-Slack-Retry-Num` short-circuiting.

### Tertiary (LOW confidence)

- None used. Every claim in this research is backed by a HIGH or MEDIUM source or VERIFIED against the codebase.

### Cross-referenced project artifacts

- `.planning/PROJECT.md` — per-user-isolated deployment shape (Assumption A4).
- `.planning/STATE.md` — Phase 2 close (Plan 02-06 + 02-07 completed); APScheduler 3.x pin (Assumption A9).
- `.planning/phases/01-foundation-vertical-slice-alpaca-paper-slack-hitl/01-CONTEXT.md` — D-19 SQLCipher, D-20 deterministic COID, D-25 credential redaction (carried through P3 quiet-hours + cookie auth).
- `.planning/phases/02-orderguard-real-money-alpaca-live-safety-floor/02-CONTEXT.md` — D-27 target_notional_usd, D-32 AWAITING_2ND_CHANNEL, D-33 LIVE banner (preserved in P3 `/approvals` mirror), D-38 kill surfaces (preserved).
- `src/gekko/approval/proposals.py:80-101` — STATE_TRANSITIONS frozenset incl. the AWAITING_2ND_CHANNEL → EXPIRED edge (Assumption A7).
- `src/gekko/db/models.py:53-62` — `_PROPOSAL_STATUSES` tuple — `'EXPIRED'` NOT present (Assumption A6).
- `migrations/versions/0002_orderguard.py` — Alembic batch_alter_table + CHECK constraint replacement pattern (template for 0003).

## Metadata

**Confidence breakdown:**

- Standard stack: **HIGH** — every dependency is already installed and version-verified against `.venv/Lib/site-packages`. No new packages.
- Architecture: **HIGH** — every pattern is either (a) backed by official slack-bolt / APScheduler / FastAPI docs, or (b) is a direct extension of an existing in-tree pattern (P1 ProposalWriter IntegrityError handler; P2 `_build_broker` + `_send_slack_dm_blocks` seams; P2 banner-state middleware).
- Pitfalls: **HIGH** — 9 pitfalls catalogued; each ties to a specific documented Slack / APScheduler / SQLite / DST / cookie quirk OR an Assumption that the planner must verify in the codebase.
- Assumptions: 3 ASSUMED + 6 VERIFIED / CITED. The 3 ASSUMED items (A1 chat.postMessage ts/channel persistence, A3 4:30pm regardless of half-day, A8 cookie secret rotation) are flagged for /gsd-discuss-phase user confirmation.

**Research date:** 2026-06-17
**Valid until:** 2026-07-17 (30 days — stable substrate; the slack-bolt and APScheduler APIs in scope have multi-year stability windows.)
