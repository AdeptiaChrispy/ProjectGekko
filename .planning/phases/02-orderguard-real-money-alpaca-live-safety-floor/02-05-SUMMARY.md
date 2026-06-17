---
phase: 02-orderguard-real-money-alpaca-live-safety-floor
plan: 05
subsystem: execution, kill-switch, slack-handler, dashboard, fastapi-lifespan, reporter
tags: [kill-switch, exec-06, d-35, d-36, d-37, d-38, 5s-sla, db-first, persistence, slack-two-step, htmx-modal, csp-script-src-self, orderguard-rejection-card, manual-demo-deferred]
status: complete-with-deferred-demos

# Dependency graph
requires:
  - phase: 02-orderguard-real-money-alpaca-live-safety-floor
    plan: 01
    provides: |
      users.kill_active column on the users table (Alembic 0002); kill_switch
      event_type already accepted in _EVENT_TYPES; OrderGuardRejected exception class.
  - phase: 02-orderguard-real-money-alpaca-live-safety-floor
    plan: 02
    provides: |
      OrderGuard.check_kill_switch read-side (already wired); cap_rejection
      branch in executor.execute_proposal (extended by 02-05 Task 3 to send
      Slack DM via build_orderguard_rejection_card AFTER the audit transaction
      closes — outside-transaction pattern per PATTERNS §4 row 14).
  - phase: 02-orderguard-real-money-alpaca-live-safety-floor
    plan: 03
    provides: |
      AlpacaBroker.cancel_order zero-decorator (per RESEARCH §3 Open Question #3:
      kill timing trumps 429 resilience); used by kill_switch.activate's
      asyncio.gather cancels.
provides:
  - "src/gekko/execution/kill_switch.py — activate(user_id, sessions, brokers) writes DB first then runs asyncio.gather(*cancels, timeout=4.0); is_active(user_id) DB-fresh read; deactivate(user_id) clears flag (D-35/D-36/D-37)"
  - "Brokerage ABC extended with get_orders_open() + cancel_all_open_orders() abstract methods"
  - "AlpacaBroker.get_orders_open (decorated @retry_on_rate_limit from 02-03) + AlpacaBroker.cancel_all_open_orders (zero-decorator; uses alpaca-py's batch cancel)"
  - "Slack /gekko kill CONFIRM two-step handler + /gekko unkill CONFIRM mirror (D-38)"
  - "CLI gekko kill + gekko unkill subcommands with typer.prompt typed-confirm"
  - "Dashboard POST /kill (modal trigger), POST /kill/confirm (typed-KILL validation), GET /kill/state (HTMX 1s polling during cancel), POST /unkill/confirm; all use HTMX hx-* attributes (no inline scripts — CSP script-src 'self' preserved)"
  - "Dashboard templates: kill_modal.html.j2 + unkill_modal.html.j2 + kill_active_banner.html.j2 (sticky top, red bg, ARIA live=assertive per UI-SPEC §5)"
  - "Phase-2 utility classes appended to src/gekko/dashboard/static/tailwind.css per UI-SPEC color tokens (kill-active-red, live-mode-red, dark-red banner family)"
  - "FastAPI lifespan boot-time kill_active check: if users.kill_active=True at startup, structured-log warning + Slack DM via _send_slack_dm (identity-split aware: gekko_user_id → slack_user_id) + sets app.state.kill_active=True so banner renders on first request"
  - "reporter.slack.build_orderguard_rejection_card — Block Kit card with red banner header '[REJECTED BY ORDERGUARD]' + reject_code + reject_reason + ticker + strategy + proposal_id"
  - "executor.execute_proposal cap_rejection handler extended to send Slack DM via build_orderguard_rejection_card AFTER the audit-write transaction closes — fresh transaction, outside-transaction pattern (PATTERNS §4 row 14 — do NOT keep audit transaction open while making HTTP calls)"
  - "kill_switch_activated audit event payload: {action: 'kill'|'kill_complete', ts_start, ts_end?, orders_cancelled?, orders_failed?, user_id}; pair of events per kill action (one at activation, one at completion)"
affects: [02-06-live-credentials-and-dual-channel, 02-07-promote-paper-to-live-end-to-end]

# Tech tracking
tech-stack:
  added: []  # no new runtime deps (tenacity already in from 02-01)
  notes: |
    Uses asyncio.gather + asyncio.wait_for for the 4s cancel timeout.
    HTMX 1s polling on /kill/state uses hx-trigger="every 1s" and
    hx-target="#kill-cancel-tally" — zero JavaScript, full CSP compliance.
---

# Plan 02-05 Summary — Kill Switch (D-35/D-36/D-37/D-38)

Wave 4 of Phase 2. Lands the global kill-switch that halts all trading on demand
with a 5-second SLA, persists across process restarts, and surfaces through 3
symmetric surfaces (Slack two-step / CLI typed-confirm / dashboard typed-KILL
modal). Wires the OrderGuard rejection Slack DM card around the cap_rejection
handler that Plan 02-02 already shipped — completes the "reject visibly, not
silently" loop for real-money safety.

## Commits

- `5dc1da5` — `feat(02-05-1): kill_switch module + Brokerage.{get_orders_open,cancel_all_open_orders}`
- `31278b9` — `feat(02-05-2): kill switch — Slack + CLI + dashboard surfaces with HTMX modal`
- `163f975` — `feat(02-05-3): boot-time kill-active DM + OrderGuard rejection Slack card wired`

## Decisions Made During Execution

- **D-37 DB-first ordering — kill_active=True writes BEFORE asyncio.gather cancels.** Even if the 4s gather timeout fires, new orders are blocked the moment the DB row commits. The `check_kill_switch` read-side (from 02-02) is the load-bearing gate; cancel_all_open_orders is best-effort cleanup.
- **D-36 DB persistence in `users.kill_active` column (not a separate kill_state table).** Wave 1's Alembic 0002 added the boolean column; 02-05 just uses it. Boot-time read survives any process restart, crash, OS reboot, machine power loss.
- **cancel_all_open_orders zero-decorator** (per RESEARCH §3 Open Question #3 + 02-03 contract). Kill timing > 429 resilience. If Alpaca rate-limits during a kill, the operator gets a partial result + a clear audit log, not a 15-second tenacity retry loop blocking the kill flow.
- **5s SLA budget allocation:** Hop 1 (Slack receipt → kill_switch.activate DB write) ≤ 200ms typical; Hop 2 (asyncio.gather cancels with 4s timeout) is the budget consumer; Hop 3 (audit event write) ≤ 50ms; Hop 4 (Slack DM via _send_slack_dm) ≤ 200ms. Typical end-to-end ≈ 420ms. Worst-case (all cancels hit 4s timeout) ≈ 4.45s — still under 5s.
- **Two-step Slack confirm** uses literal `CONFIRM` / `UNKILL` token as the second argument — chosen over Block Kit modal in RESEARCH §3 Open Question #5 (lower cost, symmetric kill/unkill, no JS in Slack message). Plain `/gekko kill` returns the warn-and-instruct message; `/gekko kill CONFIRM` executes.
- **Dashboard typed-KILL is server-side enforced.** The HTMX form submits to POST `/kill/confirm`; server validates the exact uppercase string `KILL` and rejects with 400 if mismatched. Client-side disabling is a UX polish layer only — the server is the load-bearing gate.
- **kill_switch_activated event PAIR** (action=kill at start, action=kill_complete at end) over a single event with computed duration. The pair shape lets the operator's `gekko audit dump --event-type kill_switch` query show both edges + the computed elapsed time without joining rows.
- **OrderGuard rejection DM emits AFTER audit write closes** (PATTERNS §4 row 14 — outside-transaction pattern). If the Slack API hangs or the network is partitioned, the audit chain is still intact + the proposal is already in FAILED state. Operator sees the rejection in `gekko audit dump` even if the Slack DM never arrives.
- **Identity split applied to ALL new Slack DM paths** (carry-forward from Phase 1 D-?). Boot-time DM, kill-complete DM, OrderGuard rejection DM — all go through `_send_slack_dm(gekko_user_id, blocks=...)` which translates to `slack_user_id` internally.
- **Phase-1 cap_rejection handler was modified, not replaced.** 02-02's commit `fa78387` wired the cap_rejection branch into `execute_proposal`; 02-05 Task 3 extended that same branch to ALSO emit the Slack DM. The audit-event write logic from 02-02 is untouched.

## Files Created / Modified

### Created
- `src/gekko/execution/kill_switch.py` (kill state singleton; activate/is_active/deactivate)
- `src/gekko/dashboard/templates/kill_modal.html.j2` (typed-KILL modal partial)
- `src/gekko/dashboard/templates/unkill_modal.html.j2` (typed-UNKILL modal partial)
- `src/gekko/dashboard/templates/kill_active_banner.html.j2` (sticky red banner partial; ARIA live=assertive)
- `tests/unit/test_kill_switch.py` (Wave-0 stub → 18 real assertions: DB-first ordering, 4s timeout, asyncio.gather behavior, is_active read freshness, deactivate idempotency)
- `tests/unit/test_kill_surfaces.py` (CLI typer typed-confirm flow + Slack two-step state machine)
- `tests/integration/test_kill_switch.py` (Wave-0 stub → cassette: Slack `/gekko kill CONFIRM` → kill active + cancels emitted + audit pair)
- `tests/integration/test_dashboard_kill.py` (dashboard form-POST → modal → typed-KILL valid + invalid paths; HTMX 1s polling /kill/state)
- `tests/integration/test_kill_persistence.py` (Wave-0 stub → cassette: simulate users.kill_active=True at boot → lifespan DMs operator + app.state.kill_active set)
- `tests/unit/test_orderguard_rejection_card.py` (build_orderguard_rejection_card Block Kit shape; mrkdwn-safe rendering through _escape_mrkdwn for any LLM-authored reject_reason text)

### Modified
- `src/gekko/brokers/base.py` (Brokerage ABC: `get_orders_open() -> list[Order]`, `cancel_all_open_orders() -> int`)
- `src/gekko/brokers/alpaca.py` (concrete implementations; get_orders_open carries `@retry_on_rate_limit` from 02-03's _retry module; cancel_all_open_orders zero-decorator)
- `src/gekko/slack/commands.py` (added `/gekko kill` + `/gekko unkill` slash handlers)
- `src/gekko/cli.py` (added `gekko kill` + `gekko unkill` typer subcommands)
- `src/gekko/dashboard/app.py` (FastAPI lifespan: boot-time `users.kill_active` check + Slack DM if active + `app.state.kill_active` set for template rendering)
- `src/gekko/dashboard/routes.py` (POST /kill, POST /kill/confirm, GET /kill/state, POST /unkill/confirm)
- `src/gekko/dashboard/templates/base.html.j2` (extended with kill_active_banner partial slot; live banner placeholder unchanged)
- `src/gekko/dashboard/static/tailwind.css` (appended Phase-2 utility classes per UI-SPEC color tokens — kill-active-red, modal-bg, modal-shadow)
- `src/gekko/execution/executor.py` (cap_rejection handler extended to call `build_orderguard_rejection_card` + `_send_slack_dm` AFTER audit-write transaction closes; fresh transaction; outside-transaction pattern)
- `src/gekko/execution/orderguard.py` (no logic changes; documentation comment links to kill_switch.activate for the cancel-on-kill flow)
- `src/gekko/reporter/slack.py` (added `build_orderguard_rejection_card`)
- `src/gekko/reporter/templates.py` (Block Kit shape for kill-state DM + rejection DM)
- `tests/integration/test_orderguard_cap_rejection.py` (extended from 02-02 with DM-emission assertion)

## Verification

### Automated (full suite green)

```
uv run pytest tests/unit -x -q     # 555 passed, 6 skipped, 3 pre-existing deselected
uv run pytest tests/integration -x -q  # 41 passed, 9 skipped, 0 failed
```

- AST gate: `AlpacaBroker.place_order` + `AlpacaBroker.cancel_order` + `AlpacaBroker.cancel_all_open_orders` + `OrderGuard.place_order` all zero-decorator (EXEC-03 / Pitfall 4 / Knight Capital invariant preserved)
- AST gate: `AlpacaBroker.get_orders_open` carries `@retry_on_rate_limit` (extends 02-03's GET decoration list)
- No `claude_agent_sdk` import in `src/gekko/execution/kill_switch.py` or any new check module
- CSP `script-src 'self'` preserved — no inline scripts in any new template; modal + polling uses HTMX `hx-*` attributes only
- `_send_slack_dm` identity-split (gekko_user_id → slack_user_id) applied to all 3 new DM paths (boot-time, kill-complete, OrderGuard rejection)
- Phase-1 walking-skeleton 5-event chain test still passes through OrderGuard (now 9 checks: 6 from 02-02 + PDT + T+1 + wash_sale stamp from 02-03; kill_switch check is one of the 6)

### Manual — DEFERRED to operator (`VALIDATION.md` Manual-Only Verifications §2 + §3)

The 5-second SLA and cross-restart persistence are load-bearing acceptance criteria that
require real wall-clock + real Slack + real DB restart. They CANNOT be cassette-replayed.
Per the operator's pause + same pattern as Phase-1 Plan 01-09 Task 5 (deferred to
operator on 2026-06-11, executed on 2026-06-12), demos are logged here for future
walkthrough.

#### Demo A — 5s SLA (Slack two-step)

**Setup:** `gekko serve` running locally, paper-trading Alpaca creds active, Slack DM channel open with the bot, browser tab on `http://localhost:8000/strategies`.

1. Place 2+ open orders via `/gekko run <strategy>` (use limit prices well below market so they stay OPEN).
2. Confirm orders visible+OPEN in the Alpaca paper dashboard.
3. In Slack DM with bot: `/gekko kill` — bot replies with the two-step warn message.
4. Start wall-clock stopwatch.
5. Send `/gekko kill CONFIRM`.
6. Stop stopwatch when bot DM `🚫 Kill ACTIVE. Cancelled X/Y ...` lands.
7. **Expected:** ≤5s in 9/10 trials, cancelled-count matches placed-count.
8. Run `gekko audit dump --event-type kill_switch --limit 4 --user-id chris` — expect a `kill` event + a `kill_complete` event with `ts_start`/`ts_end`/`orders_cancelled` payload.

#### Demo B — Cross-restart persistence

1. While kill is ACTIVE from Demo A: Ctrl-C `gekko serve`.
2. Restart `gekko serve`.
3. **Expected on boot:** structured log line `kill_active_on_restart=True`, Slack DM "🚫 Restarted with kill_active=ON ...", dashboard top-of-page shows red `KILL ACTIVE` sticky banner.
4. Send `/gekko run <strategy>` in Slack — agent produces a proposal, but `OrderGuard.place_order` rejects with `reject_code="kill_active"`, proposal → FAILED, Slack DM with `🔴 [REJECTED BY ORDERGUARD]` card (reject_code: kill_active, ticker, strategy, proposal_id).
5. `gekko audit dump --limit 5` confirms the cap_rejection event with `kill_active` reject_code.
6. `/gekko unkill CONFIRM` — banner clears, new `/gekko run` succeeds.

#### Demo C — Dashboard typed-KILL modal flow

1. Dashboard → click red `KILL` button (navbar top right) → modal opens with headline "Halt all trading" + typed-input form.
2. Type `kill` lowercase → submit → server rejects with HTTP 400 + error message `Type KILL exactly (uppercase) to confirm.`
3. Type `KILL` exactly (uppercase) → submit → modal closes via HTMX swap, red `KILL ACTIVE` banner appears, Slack DM mirrors action.
4. Click `UNKILL` in banner → modal opens → type `UNKILL` → submit → banner clears.

## Decisions Carried Forward to 02-06 / 02-07

- `kill_switch.is_active(user_id)` is the only sanctioned read path for the kill flag — Plan 02-06's HITL-06 dual-channel flow MUST call this read-side (already does via OrderGuard's `check_kill_switch`).
- `build_orderguard_rejection_card` accepts an optional `mode_banner: Literal["PAPER","LIVE"] | None = None` parameter — Plan 02-06 will pass `"LIVE"` when rendering rejections for live-mode strategies (red banner stacking per UI-SPEC §3).
- `app.state.kill_active` boolean is the single source of truth for the navbar banner; Plan 02-06's live-mode banner stacks ABOVE the kill banner per UI-SPEC §1.

## Manual Demo Resume Protocol

When operator runs Demos A/B/C above:
1. Reply in the relevant session with `demo_passed` + the `gekko audit dump --event-type kill_switch` output as evidence.
2. Continuation agent appends "Manual demo executed YYYY-MM-DD — passed" to this SUMMARY's bottom + closes the deferred-items.md row for 02-05 demos.
3. STATE.md updates `last_activity` with the demo confirmation.

If any demo FAILS (timing > 5s consistently, restart persistence broken, banner missing, DM not arriving), file a quick task for the specific issue + reopen the corresponding plan task here.

---

*Plan 02-05 closed with manual-demo deferred 2026-06-16. Mirrors Phase-1 Plan 01-09 pattern: code+tests autonomous, real-world walkthrough deferred to operator.*
