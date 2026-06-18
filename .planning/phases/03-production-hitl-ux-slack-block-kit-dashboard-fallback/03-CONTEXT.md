# Phase 3: Production HITL UX (Slack Block Kit + Dashboard Fallback) - Context

**Gathered:** 2026-06-17
**Status:** Ready for planning

<domain>
## Phase Boundary

A production-grade approval surface: Slack buttons that survive at-least-once delivery without double-execution, configurable quiet hours that pause the agent loop overnight, a configurable timeout that auto-rejects stale proposals, an edit-size flow that re-enters OrderGuard's 2% drift check, and a dashboard `/approvals` page that mirrors Slack 1:1 so the user can complete approve/reject/edit from the browser when Slack is wedged.

**5 requirements in scope** — HITL-02 (idempotent Slack buttons under Slack's at-least-once delivery), HITL-03 (timeout = REJECT default 30 min, configurable; auto-reject + notify on expiry), HITL-05 (quiet hours configurable, no 2am pings), DASH-04 (web approval fallback when Slack is unavailable), REPT-01 (Slack DM categories: trade proposals, executions, daily P&L, errors / operational alerts).

**Plus carry-forward from v1.0:** executor-error → Slack DM surfacing on `MarketClosed` / `BrokerOrderError`. Both already DM the operator as of Phase 2 fixes (`executor.py` lines 454 and 654 respectively). Treated as substantially CLOSED. P3 audit: confirm coverage and add any missing error classes the executor raises silently.

**Out of scope for P3** (lives in later phases): two-tier cost ceiling + prompt-injection red-teaming (P4); trust ladder propose-only ↔ auto-within-caps + portfolio-level caps + anomaly-demote (P5); full magic-link multi-user auth + per-user dashboard onboarding (P6); supervisors + heartbeat + NTP + reconciliation + market-hours-aware scheduler infra (P7); IBKR + Schwab + Robinhood + Fidelity brokers (P8/P9). HITL-06 first-live-trade dual-channel gate, kill switch's three surfaces with typed confirmation, and the LIVE-banner visual treatment all SHIPPED in P2 (D-32, D-33, D-38) — P3 does NOT touch them.

</domain>

<decisions>
## Implementation Decisions

### A. Idempotency Mechanism (HITL-02)

- **D-41: Belt-and-suspenders idempotency — Slack-action dedup table + state-machine CHECK.** New `slack_action_dedup` table (per-user SQLCipher DB) with rows keyed on `(proposal_id, action_id, actor_slack_user_id)` and a UNIQUE constraint over that triplet. The Slack handler INSERTs at the top of `handle_approve` / `handle_reject` / `handle_edit_size` / `handle_escalate`; `IntegrityError` on the INSERT means duplicate — branch into the "already handled" path immediately without re-running the state-machine. The existing `transition_status` CHECK (PENDING → APPROVED guarded; line 126 in `approval/proposals.py`) is the secondary defense for code paths that bypass the dedup table (e.g., dashboard, CLI). This split makes the duplicate branch an explicit handler path with a clean UX message instead of a "state transition failed" error to translate.

- **D-42: Dedup key is `(proposal_id, action_id, actor_slack_user_id)`.** Three-tuple is the strongest semantic key — captures "which proposal, which button, which clicker". Allows cross-user behavior (User A approves, User B independently fires reject — both register because actor differs); aligned with the v1.0 identity-split lesson (`gekko_user_id` ≠ `slack_user_id`). The `slack_user_id` column stores the Slack user the callback came from; the `proposal_id` and `action_id` are the discriminators within that. Extends cleanly into P6 multi-user (dashboard actor becomes `dashboard_user_id` mapped to `gekko_user_id`; same table, see D-47).

- **D-43: Dup-click UX = ephemeral Slack message with current proposal status.** The duplicate click handler responds via Slack's `respond_url` with `response_type="ephemeral"`. Message format: `✅ Already approved by @<slack_user> at <HH:MM>. Status: <current_status>.` (with rejection / edit / expiry variants). Ephemeral means only the clicker sees it — no channel noise. This is the cleanest signal that "your click registered, but the action was already taken".

- **D-44: First-write-wins race policy by INSERT timestamp on the dedup table.** When user fires Edit-size then Approve in quick succession (or Slack reorders callbacks), the INSERT ordering into `slack_action_dedup` is the source of truth. Whichever callback our server processes first commits its row and locks the state transition; the second callback's INSERT either succeeds with a different `action_id` (separate intent) but then sees the proposal no longer PENDING and dedup'd-out at the state-machine layer, OR fails as a duplicate of itself (Slack retry). No special-casing per action type. Predictable, race-safe, and audit-grep-able.

- **D-45: Dedup table audit shape.** Columns: `id (pk)`, `proposal_id (fk)`, `action_id` (e.g., `'approve_proposal'`, `'reject_proposal'`, `'edit_size'`, `'escalate'`), `actor_slack_user_id`, `actor_gekko_user_id` (resolved via identity-split lookup at INSERT time), `source` (`'slack'` or `'dashboard'` — see D-47), `slack_trigger_id` (nullable, only populated for Slack callbacks; useful for retry-header debugging), `inserted_at`, `result` (`'first_write'` / `'duplicate'`). The duplicate-resolution path also emits a `dedup_click` event into the existing audit log so the audit chain (D-14 from P1) preserves the dup-attempt history.

### B. Quiet Hours Behavior (HITL-05)

- **D-46: Quiet hours pause the agent loop entirely; no proposals are created during the window.** APScheduler skips both Researcher and Decision cycles when current time is in-window. When the window opens, normal cadence resumes and the Researcher runs against fresh market data — no stale 9-hour-old proposals lying in PENDING. Simplest state, no "wake to a stack of expired proposals" awkwardness. Matches the v1.0-confirmed swing/long-horizon cadence (no sub-minute scheduling); preserves the spirit of "no 2am pings" by removing the trigger source, not by post-suppressing notifications.

  **Manual override:** the existing `gekko run <strategy>` CLI subcommand bypasses the quiet-hours skip when invoked interactively (operator-initiated = intent). The agent loop's auto-cadence is what pauses.

- **D-47: Quiet hours storage = per-user default + per-strategy override; strategy override WINS when set.** Add `User.quiet_hours_start: time | null`, `User.quiet_hours_end: time | null`, `User.timezone: str` (IANA name, e.g., `'America/New_York'`). Add `Strategy.quiet_hours_start: time | null`, `Strategy.quiet_hours_end: time | null` (no `Strategy.timezone` — strategy inherits the user's TZ to keep the mental model coherent). At scheduling check-time:
  - If `strategy.quiet_hours_start` is set, that pair defines the window.
  - Otherwise, fall back to `user.quiet_hours_start`.
  - If neither is set, no quiet hours; agent runs 24/7.

  **Invariant note (operator-facing):** the strategy override CAN widen awake-time (e.g., user is 10pm-7am, strategy is 11pm-6am). The dashboard's per-strategy quiet-hours form MUST display a warning when the strategy window is narrower than the user window, calling out that the strategy will ping during the user's normal silent window. This puts the operator in the driver's seat without enforcing a paternalistic narrowing rule.

- **D-48: Quiet-hours bypass categories for DMs (the 'pager' channel).** Quiet hours pause the agent LOOP but do NOT suppress these DM categories — these always fire regardless of window:
  1. **`kill_active` state changes** — kill fires, auto-demotion triggers, kill-active-on-boot. Safety-critical.
  2. **Executor errors** — `BrokerOrderError`, `OrderGuardRejected`, `MarketClosed` retry exhaustion. The v1.0 "operator sees silence" failure mode; never suppress.
  3. **First-live-trade FILLS** — the load-bearing real-money first trade on a strategy. Operator wants to know within minutes, not at 7am.

  **Suppressed during quiet hours (DM at window-open or fixed post-close time):**
  - Routine paper-trade fill confirmations (REPT-01 informational)
  - Daily P&L summary (REPT-01 informational — fixed at 4:30pm post-close ET regardless)
  - Cost-ceiling soft-warning DMs (P4 territory anyway)

- **D-49: Timezone is per-user IANA name stored on the User row.** Validated against `zoneinfo.available_timezones()` at config time. DST is handled automatically by `zoneinfo`. The 30-min proposal expiry timer (D-52) always lives in UTC — only the quiet-hours window comparison converts to the user TZ via `datetime.now(zoneinfo.ZoneInfo(user.timezone))`. Default for v1 US-equities scope: `'America/New_York'`.

### C. Timeout / Expiry Mechanic (HITL-03)

- **D-50: APScheduler periodic sweep, every 60 seconds, expires-where-due.** A registered APScheduler interval job runs `expire_stale_proposals()` every 60s, which `SELECT id, user_id, ... FROM proposals WHERE status='PENDING' AND expires_at <= now() FOR UPDATE` and for each: `transition_status(PENDING → EXPIRED)` + write an `expiration` audit event with payload `{reason: 'timeout', expired_at, configured_timeout_minutes}` + chat.update the Slack card to the EXPIRED visual + DM the operator. Max latency = 60s past the configured timeout, fully acceptable for swing-horizon strategies. Survives process restart (APScheduler 4.x persists jobs in the same SQLite DB).

- **D-51: Timeout default 30 min, per-strategy override.** Schema: `Strategy.proposal_timeout_minutes: int | null` (default `null` → use the global `PROPOSAL_TIMEOUT_DEFAULT_MIN = 30` constant). LLM doesn't pick the timeout (no per-proposal override) — keeps the surface predictable. Strategy-level fits the existing config shape (HardCaps, watchlist, quiet_hours_*). At proposal-build time, ProposalWriter computes `expires_at = utcnow() + timedelta(minutes = strategy.proposal_timeout_minutes or PROPOSAL_TIMEOUT_DEFAULT_MIN)` and persists it on the proposal row.

- **D-52: Same timeout for paper and live proposals.** The timeout is about rationale-freshness (price moved, news broke), not about real-money urgency. OrderGuard's 2% drift check (D-27) and the P2-shipped first-live-trade dual-channel gate (D-32) already enforce real-money safety. A shorter live-mode timeout would create perverse time pressure on the deliberate first-live dual-channel flow ("you have 15 min to find the dashboard tab and click confirm" is wrong). One mental model, fewer corner cases.

- **D-53: Expiry UX = `chat.update` the original card in-place + separate DM notice.** The expired card swaps to a greyed-out header `⏰ Proposal expired at <HH:MM> — not executed (timeout=REJECT after 30 min).` with buttons replaced by a disabled-style status line. A separate DM lands: `Your <TICKER> <SIDE> proposal expired without action. Reason: timeout=REJECT (configured at <N>min on strategy <name>).` Both surfaces. Reasoning: the in-place card update kills stale-looking PENDING cards in the channel; the separate DM gives an explicit "I expired" signal that's grep-able in DM history.

  **Edge case (race between sweep and a click landing at ~30:00):** the sweep takes `FOR UPDATE`; a button click landing simultaneously hits the dedup table OR the state-machine CHECK — first-writer wins. If the sweep wins, the user click sees "already handled, status=EXPIRED" via the D-43 ephemeral. If the click wins, the sweep's `transition_status` no-ops (proposal is no longer PENDING). Either order is safe.

### D. Edit-size + Dashboard /approvals (HITL-04, DASH-04)

- **D-54: Edit-size = Slack modal (slack-bolt `views_open`) with qty input + live new-notional preview + 2% drift re-check.** Clicking "Edit size" on the proposal card opens a Slack modal with three elements: (1) a number input pre-filled with `tp.qty`, labelled "New quantity"; (2) a live-computed read-only line `New notional = qty × <ref_price> = $<computed>` and `Drift vs target_notional_usd = <pct>%`; (3) a submit button labelled "Approve at this size". Submit triggers `handle_edit_size_submit` which:
  1. INSERTs the dedup row (same D-41 mechanism, `action_id='edit_size'`)
  2. Validates the edited qty against OrderGuard's 2% drift check on the existing `target_notional_usd` (D-27); if `abs((qty × ref_price) - target_notional_usd) / target_notional_usd > 0.02`, the modal re-renders with a red `❌ Drift X% exceeds the 2% safety bound. The agent set target=$<N>; this qty would be $<M>. Adjust qty or re-run the strategy.` error block. No state change, no audit event.
  3. On pass: write `edit_size` audit event with `{old_qty, new_qty, old_notional, new_notional, drift_pct}`, update `proposal.qty`, then transition `PENDING → APPROVED` and dispatch to executor — single interaction, no two-step. This is the only path that mutates `qty` on a PENDING proposal.

  **Why modal, not inline +/- buttons:** the use case is "I want 50 shares not 47" — fractional/specific qty, not a fixed delta. Modal also makes the drift error a contained UI moment (red block in the modal) instead of a confusing card update.

- **D-55: Dashboard `/approvals` = full mirror of the Slack card (HTMX cards, 1:1).** Each PENDING proposal renders as an HTMX card on `/approvals` with the same buttons (Approve / Reject / Edit-size / Escalate) and the same rationale + evidence blocks. Single Jinja2 template renders the proposal-card schema for both surfaces (with a small render-context flag that swaps `Approve` from "respond to Slack interaction" to "POST `/approvals/{proposal_id}/approve`"). The drift between Slack and dashboard is the killer for users; mirroring eliminates the drift. Matches v1.0's HTMX-only no-build pattern. Edit-size on dashboard opens a modal (HTMX `hx-target` swap with the same qty input + drift preview); same `handle_edit_size_submit` server logic, just different transport.

  **`/live-confirm/{proposal_id}` (already exists from P2)** is the dedicated route for the first-live dual-channel gate; `/approvals` is the general index. They coexist — `/live-confirm` is the URL DM'd to the operator on a first-live; `/approvals` is what the operator browses to see all PENDING.

- **D-56: Cross-surface race = extend the dedup table with `source` column; state-machine enforces first-write-wins.** The `slack_action_dedup` table picks up a `source` column with values `'slack'`, `'dashboard'`, `'cli'` (CLI reserved for future). When the dashboard handler INSERTs, it uses the same `(proposal_id, action_id, actor_gekko_user_id, source)` key — actor for cross-actor cases, source for cross-surface audit visibility. The `source` column is for AUDIT, not for dedup semantics: same user clicking Approve in Slack and then in dashboard yields two rows, but the second row's downstream state transition fails (proposal no longer PENDING) and the user sees the D-43 "already handled" ephemeral / HTMX-swap. First-write-wins is enforced at the state-machine layer; the dedup table is the observability shim.

- **D-57: Dashboard auth in P3 = localhost-only + SQLCipher-passphrase-on-first-load session cookie.** The FastAPI app binds `127.0.0.1:<port>` only (already the v1 deployment shape — single-operator, single-machine per REG-03). First GET to `/approvals` (or any authenticated route) shows a passphrase prompt page; correct passphrase derives the SQLCipher key, unlocks the per-user DB, mints a session cookie bound to that `user_id` with 8-hour idle expiry. Cookie is `HttpOnly`, `Secure=False` (HTTP on localhost is fine), `SameSite=Strict`. The SQLCipher passphrase is the auth secret — no new credential to manage. P6 (Web Dashboard & Multi-User Auth) swaps this for magic-link auth via `fastapi-users` without changing the route shape (the dashboard logic is auth-agnostic; only the dependency injection of "current user" changes).

  **Why not tunnel-only:** Tailscale / Cloudflare Tunnel solves remote access (which the first-live `/live-confirm` already needs P2), but P3's `/approvals` only needs to work on-machine for the fallback-when-Slack-down case. Operator can already set up a tunnel if remote dashboard access is desired (P2 README's walking-skeleton demo covers this).

### E. REPT-01 DM Categories (Claude's Discretion at planning time)

The 5 DM categories specified by REPT-01 with their wire-status:
- **Trade proposals** — HITL-01, shipped in P1 (`build_proposal_card`); P3 adds idempotency + quiet-hours-gating per A/B above.
- **Trade executions** — wired in P1 executor (`_send_slack_dm` on fill); P3 adds quiet-hours-suppression for routine paper fills, bypass for first-live fills per D-48.
- **Daily P&L summary** — NEW in P3. Fixed schedule: 4:30pm America/New_York post-close (regardless of user quiet hours). Content shape is Claude's discretion at planning time, but should include at minimum: today's filled trades count, gross P&L, per-strategy realized P&L, open positions snapshot, any errors/rejections that hit the audit log today.
- **Errors and operational alerts** — `BrokerOrderError` / `OrderGuardRejected` / `MarketClosed` already DM (P2 fixes, `executor.py` lines 454 + 654). P3 audit: confirm coverage; add severity tier on DMs (`⚠️` = informational, `❌` = error, `🚫` = kill-state). Cost-ceiling alerts (P4 territory) and trust-ladder anomaly-demotion DMs (P5 territory) are out of scope.
- **Carry-forward audit:** confirm `executor.py` and `kill_switch.py` cover every error path that transitions a proposal to FAILED with an operator-visible DM. Any silent FAILED transitions are bugs P3 closes.

### Claude's Discretion

Items left to research / planning that don't need user input now:

- Exact `slack_action_dedup` table schema and Alembic migration sequencing (column types, indexes, FK cascade rules) — planner.
- Exact dashboard auth cookie implementation (FastAPI `SessionMiddleware` vs `fastapi-users` `CookieTransport` shim) — planner with researcher input.
- Slack modal `views_open` payload shape for edit-size — researcher confirms current slack-bolt API.
- APScheduler sweep job persistence + idempotency on restart (does the 60s job double-fire on hot reload?) — researcher confirms APScheduler 4.x semantics.
- HTMX-level patterns for the modal swap on `/approvals` edit-size (existing dashboard templates use `hx-target` + `hx-swap="outerHTML"` — confirm reuse) — planner via codebase scout.
- Daily P&L DM block format + exact post-close fire time (4:30pm ET? Or after `pandas_market_calendars` confirms close?) — planner.
- Quiet-hours validation UX on the per-strategy override form (the narrower-than-user warning) — planner / UI-phase if `/gsd-ui-phase 3` is invoked.
- Where the `expire_stale_proposals` sweep job is registered (`dashboard.app.lifespan` already wires APScheduler for P1; add another job alongside) — planner.
- Ephemeral message timing and `respond_url` lifetime constraints (Slack docs say ~30 min) — researcher.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project context
- `.planning/PROJECT.md` — Project intent, constraints (multi-tenant, HITL mandatory, regulatory posture, single-tenant-per-instance per D-18)
- `.planning/milestones/v1.0-REQUIREMENTS.md` — 108 v1 requirements; **5 mapped to Phase 3** (HITL-02, HITL-03, HITL-05, DASH-04, REPT-01)
- `.planning/STATE.md` — Current project state; Phase 2 closeout (15/16 plans complete, 4 demos deferred)
- `.planning/ROADMAP.md` — 9-phase roadmap; Phase 3 success criteria (5 items) + carry-forward note
- `.planning/MILESTONES.md` — v1.0 archived; v2.0 in progress (Phases 2-5)

### Phase 2 carry-forward (locked decisions D-26..D-40, especially the dual-channel + LIVE-banner pieces that P3 must not duplicate)
- `.planning/phases/02-orderguard-real-money-alpaca-live-safety-floor/02-CONTEXT.md` — D-26 OrderGuard decorator, D-27 target_notional_usd (2% drift); D-32 AWAITING_2ND_CHANNEL + APPROVED_LIVE states (already shipped — P3 reuses); D-33 LIVE banner visual treatment (already shipped — P3 must preserve in dashboard + Slack); D-38 three kill surfaces with typed confirmation (already shipped)
- `.planning/phases/02-orderguard-real-money-alpaca-live-safety-floor/02-VERIFICATION.md` — Phase 2 close-out status (passed_with_deferred); 4 demos pending (kill 5s SLA, cross-restart, dashboard typed-KILL, real $1)
- `.planning/phases/02-orderguard-real-money-alpaca-live-safety-floor/deferred-items.md` — pre-existing failing tests (test_doctor_missing_envvar, test_missing_anthropic_key, test_finnhub_news_degrades_gracefully) — NOT P3's job to fix

### Phase 1 foundation (the integration substrate)
- `.planning/phases/01-foundation-vertical-slice-alpaca-paper-slack-hitl/01-CONTEXT.md` — D-14 audit event vocabulary, D-19 SQLCipher vault, D-20 deterministic client_order_id, D-25 credential redaction
- `quick/260612-nlv/SUMMARY.md` — identity-split fix (`gekko_user_id` ≠ `slack_user_id`); pattern locked across `_send_slack_dm` paths; P3 must preserve when introducing dashboard auth (D-57)

### Research outputs
- `.planning/research/SUMMARY.md` — Consolidated findings; HITL UX patterns
- `.planning/research/STACK.md` — slack-bolt async, APScheduler 4.x, FastAPI, HTMX, fastapi-users
- `.planning/research/FEATURES.md` — Quiet hours, timeout patterns, idempotent button design
- `.planning/research/ARCHITECTURE.md` — State-machine extensions, dashboard surfaces
- `.planning/research/PITFALLS.md` — Slack at-least-once delivery, time-zone DST, expiry-race semantics

### Code (the integration substrate P3 plugs into)
- `src/gekko/approval/proposals.py` — state-machine + `transition_status` + idempotency CHECK (line 126); P3 adds `EXPIRED` enum + sweep helper + `slack_action_dedup` table
- `src/gekko/approval/slack_handler.py` — `handle_approve` + `handle_edit_size_stub` (line 414, currently DMs "coming in Phase 3"); P3 fully wires edit-size with modal + 2% drift check (D-54)
- `src/gekko/slack/commands.py` + `src/gekko/slack/interactivity.py` — slash + button handlers; P3 adds dup-click ephemeral response + edit-size modal handlers + `views_open` calls
- `src/gekko/slack/app.py` — slack-bolt app instance + Socket Mode wiring; P3 registers the new modal `view_submission` listener
- `src/gekko/dashboard/routes.py` — FastAPI routes; P3 adds GET `/approvals` (full index), POST `/approvals/{id}/approve`, POST `/approvals/{id}/reject`, POST `/approvals/{id}/edit-size`, GET `/login` (passphrase prompt), POST `/login` (mint session cookie)
- `src/gekko/dashboard/app.py` — FastAPI app + lifespan + APScheduler wiring; P3 registers `expire_stale_proposals` interval job (D-50) + `daily_pnl_summary` cron job (D-48 / E)
- `src/gekko/dashboard/templates/` — Jinja2 templates; P3 adds `approvals_index.html`, `proposal_card.html` (shared with Slack rendering via a render-context flag), `login.html`, edit-size modal partial
- `src/gekko/reporter/slack.py` — `build_proposal_card`; P3 extends the EXPIRED-state branch (greyed card + disabled buttons), respects D-33 LIVE banner
- `src/gekko/execution/executor.py` — `_send_slack_dm` (line 188); already wired through identity-split. P3 does NOT modify the seam, only adds quiet-hours-aware wrapper at the call sites where REPT-01 informational DMs are sent (D-48)
- `src/gekko/db/models.py` — `Event.event_type` CHECK constraint; P3 adds `expiration`, `dedup_click`, `edit_size`, `daily_pnl` to the enum. `User` model: add `quiet_hours_start`, `quiet_hours_end`, `timezone`. `Strategy` model: add `quiet_hours_start`, `quiet_hours_end`, `proposal_timeout_minutes`. New table: `slack_action_dedup` (D-45 schema)
- `src/gekko/schemas/strategy.py` — `Strategy` Pydantic schema; P3 adds the three new optional fields
- `src/gekko/db/migrations/versions/` — P3 ships at least one new Alembic revision (e.g., `0004_p3_hitl_ux.py`) covering the schema additions

### External documentation (research-cited)
- slack-bolt docs (Python, async): https://slack.dev/bolt-python/concepts — `views_open` modal API, `respond_url` ephemeral response, `chat.update` card mutation, Socket Mode app handler
- Slack at-least-once delivery: https://api.slack.com/interactivity/handling — `X-Slack-Retry-Num` header, 3-second ack contract, idempotency guidance
- APScheduler 4.x docs: https://apscheduler.readthedocs.io/en/master/ — IntervalTrigger, CronTrigger, persistent job store in SQLite, restart semantics
- Python `zoneinfo`: https://docs.python.org/3/library/zoneinfo.html — IANA tz lookup, DST handling, `available_timezones()` validation
- FastAPI `SessionMiddleware`: https://fastapi.tiangolo.com/advanced/middleware/ — cookie-based session, HttpOnly + SameSite
- HTMX modal patterns: https://htmx.org/examples/modal-bootstrap/ — `hx-target` swap for modal open/close
- Anthropic Slack-button idempotency commentary (PITFALLS.md §"Slack at-least-once"): canonical failure mode this phase defends against

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **State-machine + transition_status (`approval/proposals.py`).** Already idempotent at the transition layer (line 126: same-status no-op). `EXPIRED` is already in the `STATE_TRANSITIONS` frozenset for `AWAITING_2ND_CHANNEL → EXPIRED` (reserved by P2). P3 extends to `PENDING → EXPIRED` for the sweep path.
- **`build_proposal_card(account_mode=...)` (`reporter/slack.py`).** Already parameterized for PAPER/LIVE. P3 adds an `expired=True` rendering branch (greyed-out, no buttons).
- **`handle_edit_size_stub` (`approval/slack_handler.py:414`).** Currently DMs "coming in Phase 3". P3 replaces with full modal flow per D-54. The stub's signature is preserved; the body is rewritten.
- **`_send_slack_dm` seam (`execution/executor.py:188`).** Identity-split-aware (`gekko_user_id` → `slack_user_id` translation). P3 introduces a quiet-hours-aware wrapper `_send_slack_dm_respecting_quiet_hours(user_id, text, category)` that consults `User.quiet_hours_*` and routes routine categories to a deferred-DM queue per D-48.
- **APScheduler wiring in `dashboard/app.py` lifespan.** P1 already registers research + decide jobs. P3 adds two more: `expire_stale_proposals` (IntervalTrigger 60s) and `daily_pnl_summary` (CronTrigger at 16:30 America/New_York).
- **HTMX + Jinja2 templates in `dashboard/templates/`.** P2 added `/live-confirm/{proposal_id}` template. P3 extends the directory: `approvals_index.html`, `login.html`, `proposal_card.html` (shared partial).
- **SQLCipher passphrase cache (`vault/passphrase.py`).** P3's dashboard login uses the same cache; correct passphrase entry on `/login` writes to the cache + mints the session cookie.

### Established Patterns

- **Pydantic v2 schemas with `Decimal` money fields.** P3 doesn't touch money math; new schema fields are `time | null` (quiet hours), `int | null` (timeout minutes), `str` (timezone IANA name) — all standard.
- **Per-user SQLCipher engine + `_get_session_factory(user_id)`.** P3's new `slack_action_dedup` table lives in the per-user DB. Dashboard auth resolves `current_user` to a session-bound `gekko_user_id` that downstream queries use as the engine key.
- **Test seams as module-level callables.** P3's new sweep function `_expire_stale_proposals(now: datetime)` and `_resolve_quiet_hours(user_id, now: datetime) → bool` follow this pattern — monkey-patch-friendly.
- **structlog credential redaction (D-25).** P3's new dashboard auth must not log passphrase or session cookie value; rely on the existing `_redact` processor + add `passphrase` and `session_cookie` to the redaction list.
- **Audit event append pattern.** P3's new event types (`expiration`, `dedup_click`, `edit_size`, `daily_pnl`) use the existing `append_event` call with `normalize_decimals` on payloads.
- **identity-split (`gekko_user_id` ≠ `slack_user_id`) class-of-bug pattern.** P3's dedup table stores `actor_slack_user_id` AS REPORTED by Slack and ALSO resolves to `actor_gekko_user_id` at INSERT time (via `User.slack_user_id` lookup). Never conflates the two.

### Integration Points

- **Slack flow.** `handle_approve` / `handle_reject` / new `handle_edit_size_submit` / new `handle_dup_click_ephemeral_response`. All INSERT the dedup row at the top; branch into ephemeral response on `IntegrityError`. Slack view-submission listener registered for the edit-size modal.
- **Dashboard flow.** New `/login` GET + POST (passphrase form). New `/approvals` GET (HTMX-rendered card list, mirror of Slack cards). New POST handlers for approve / reject / edit-size at `/approvals/{id}/{action}` — same server logic as Slack handlers, different transport, same dedup table with `source='dashboard'`.
- **Executor → DM.** Existing fill / error DMs (already wired). P3 adds the quiet-hours-aware wrapper for routine fills + daily P&L. The wrapper consults bypass-categories per D-48; non-bypass-category DMs during quiet hours are deferred to a small queue and drained when the window opens (the only "queue" mechanism in P3 — for DMs only, NOT for proposals).
- **Scheduler.** Two new APScheduler jobs registered in `dashboard.app.lifespan` alongside the existing P1 jobs. `expire_stale_proposals` is per-user-iterating (each user's engine consulted in turn) — bounded by the v1 single-user-per-instance shape but defensive for multi-user-later.
- **State machine.** New transitions: `PENDING → EXPIRED` (sweep path), `PENDING → APPROVED` after edit-size (the edit collapses into approve in a single transition for D-54). Extend `STATE_TRANSITIONS` frozenset.

</code_context>

<specifics>
## Specific Ideas

- **First-write-wins is the universal race rule.** D-44 (Slack edit-size vs approve race), D-53 (sweep vs click at-the-bell), D-56 (Slack vs dashboard cross-surface race) — all resolved by the same primitive: dedup-table INSERT ordering + state-machine CHECK guarantee. Single mental model; one audit primitive (`slack_action_dedup` rows + `transition_status` no-ops) explains every race.

- **Quiet hours pause the agent, not the user.** D-46 + D-48 — the LOOP pauses, but DM categories that matter (kill, errors, first-live fills) always fire. The operator can still issue `gekko run <strategy>` manually during quiet hours and the proposal will be queued for window-open DM — manual override is intent. This matches the v1.0-confirmed "operator is the source of truth" deployment shape.

- **Edit-size MUST re-enter OrderGuard's 2% drift check.** D-54 — the user can edit qty but cannot bypass the off-by-magnitude defense (D-27 from P2). The modal renders a red drift-exceeded error block in-modal; no state transition fires on failed validation. This preserves the Knight Capital defense that P2 built and prevents edit-size from becoming an OrderGuard bypass.

- **Dashboard auth is intentionally minimal in P3 (localhost + passphrase cookie).** D-57 — the only consumer that needs dashboard auth in v2 is the single-operator-on-single-machine deployment. P6 swaps to magic-link `fastapi-users` without changing route shape. Don't over-engineer auth before P6 makes it real.

- **REPT-01's daily P&L is the only NEW DM category P3 ships.** Other categories (proposals, executions, errors) already exist; P3 just adds idempotency, quiet-hours-gating, and the routine-vs-bypass split. Daily P&L is genuinely new; planner / UI-phase decides content shape.

</specifics>

<deferred>
## Deferred Ideas

Captured during Phase 3 discussion for later phases — do not lose them, do not act on them now.

- **Per-proposal LLM-suggested timeout** — discussed in C2. Rejected for P3 (LLM hallucinates 0 or 9999). Could revisit in P4 if Decision-agent output structure tightens enough to trust a `timeout_minutes` field.
- **Bearer-token magic-link `/approvals` URL** — discussed in D4 as P3/P6 bridge. Rejected for P3 because localhost + passphrase is simpler and P6 will replace the auth layer anyway. Revisit only if a Phase 3.5 demand for remote dashboard access emerges before P6.
- **Tunnel-only access (Tailscale / Cloudflare Tunnel)** — discussed in D4. Operator can already set this up at the OS / network layer per the P2 walking-skeleton README; not a P3 application-layer concern.
- **Per-strategy timezone override** — discussed adjacent to D-47. Rejected for P3 (strategy inherits user TZ to keep the model coherent). Revisit in v2.x if an international-markets strategy ships.
- **Intersection or "union" merge of per-user + per-strategy quiet hours** — discussed in B-merge-rule. Operator explicitly chose strategy-override-wins (D-47), accepting that the strategy can widen awake-time with a dashboard warning. If post-launch the operator reports unwanted late-night pings from a misconfigured strategy override, revisit the merge rule (could narrow to union-only) in a P3 follow-up.
- **Hybrid quiet-hours: pause-scheduler + queue manually-triggered proposals** — discussed in B-Q1. Operator picked pure pause; manual `gekko run` overrides naturally. The hybrid is more state for no clear user gain.
- **Inline +/- edit-size buttons (e.g., +10%, ×2)** — discussed in D-Q1. Modal is the chosen UX. If post-launch users request faster edits, an inline-button complement could ship in a small follow-up plan (additive, not replacement).
- **Cost-ceiling soft-warning DMs (REPT-01 adjacent)** — Phase 4 territory; P3 does not introduce cost-aware DMs. Mentioned only to note exclusion.
- **Anomaly-demote DMs (REPT-01 adjacent)** — Phase 5 territory (Trust Ladder); P3 does not introduce trust-state-change DMs.
- **Drainable DM queue cleanup TTL** — D-48 implies a small queue holds quiet-hours-deferred informational DMs; a sensible TTL (e.g., drop DMs older than 24h on the next drain to avoid resurrecting yesterday's stale fills) is planner discretion but should be flagged for review.

</deferred>

---

*Phase: 3-Production HITL UX (Slack Block Kit + Dashboard Fallback)*
*Context gathered: 2026-06-17*
