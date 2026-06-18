---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
verified: 2026-06-18T00:00:00Z
status: gaps_found
score: 2/5
overrides_applied: 0
gaps:
  - truth: "Slack Block Kit proposal card with idempotent buttons — clicking the same button twice results in EXACTLY ONE action, never double-execution"
    status: partial
    reason: "The claim_action UNIQUE-INSERT backstop (dedup.py) is correct and verified. However, the X-Slack-Retry-Num retry gate at handle_approve/handle_reject reads from body['headers']['x-slack-retry-num'] which does not exist in Socket Mode (the production transport). In Socket Mode retries arrive via WebSocket envelope, not HTTP headers. The gate always returns 0 and is effectively dead code in production. Exactly-once execution holds via claim_action alone, but the gate adds no protection and may mislead future maintainers. Additionally, dashboard /live-confirm/{id} POST (the second-channel confirmation) has NO require_session dependency — any unauthenticated caller on the dashboard port can confirm a live-money trade. This is the HITL-06 dual-channel gate and its auth assumption is violated."
    artifacts:
      - path: "src/gekko/approval/slack_handler.py"
        issue: "_extract_retry_num reads body['headers']['x-slack-retry-num'] — always returns 0 in Socket Mode (production transport); retry gate is dead code"
      - path: "src/gekko/dashboard/routes.py"
        issue: "live_confirm_post at line 1386 has no Depends(require_session) — unauthenticated POST can trigger AWAITING_2ND_CHANNEL -> APPROVED_LIVE and dispatch real-money executor"
    missing:
      - "Fix _extract_retry_num to read Socket Mode retry metadata (envelope retry_attempt) or remove gate and rely solely on claim_action"
      - "Add user_id: str = Depends(require_session) to live_confirm_get (line 1308) and live_confirm_post (line 1386)"

  - truth: "Dashboard fallback: user can complete the same approve/reject/edit flow via /approvals page and the order executes identically"
    status: failed
    reason: "CR-01 (CONFIRMED): the safety-critical routes POST /live-confirm/{id}, GET /live-confirm/{id}, POST /kill, POST /unkill, GET /kill/state, POST /strategies/{name}/promote-to-live, POST /trigger/{name}, GET /strategies, GET /strategies/{name}/edit, and POST /strategies/{name}/save all lack Depends(require_session). The only middleware is _inject_banner_state which does NOT enforce auth. The /approvals approve/reject/edit-size routes ARE gated (lines 261, 345, 421, 487), so dashboard HITL approve/reject works correctly when authenticated. But the live-confirm dual-channel gate, the kill switch, and the promote-to-live route are wide open. Any process that can reach the dashboard port can disable the kill switch, promote a strategy to live, or confirm a real-money trade without credentials."
    artifacts:
      - path: "src/gekko/dashboard/routes.py"
        issue: "POST /live-confirm/{proposal_id} (line 1386): no require_session — HITL-06 second-channel gate is unauthenticated"
      - path: "src/gekko/dashboard/routes.py"
        issue: "POST /kill (line 1123), POST /unkill (line 1166): kill switch routes unauthenticated — any local process can toggle"
      - path: "src/gekko/dashboard/routes.py"
        issue: "POST /strategies/{name}/promote-to-live (line 1265): promotion route unauthenticated"
      - path: "src/gekko/dashboard/routes.py"
        issue: "POST /trigger/{name} (line 1041): agent trigger unauthenticated — spends Claude API budget with no auth"
      - path: "src/gekko/dashboard/app.py"
        issue: "app.py middleware (_inject_banner_state) does not enforce auth — it is purely a state-injection read path"
    missing:
      - "Add user_id: str = Depends(require_session) to all state-changing and sensitive routes listed in CR-01"
      - "Preferred: use router-level dependency [Depends(require_session)] with explicit exemptions for /login, /healthz, and /slack/events"

  - truth: "Daily P&L digest with correct per-strategy P&L and sign-correct headline gross P&L"
    status: failed
    reason: "CR-02: on_fill_event in executor.py builds fill_payload (lines 849-860) with keys event_kind, client_order_id, broker_order_id, filled_qty, filled_avg_price, ticker — NO strategy_name and NO side fields. The daily_pnl.py aggregator reads payload.get('strategy_name', '_unknown_') and payload.get('side', 'buy'). On real fills: every fill buckets under '_unknown_' (per-strategy breakdown is meaningless) and every fill is treated as a BUY (-(price*qty)), so profitable SELLs show as large negative P&L. The headline 'Gross P&L' number shown to the operator is wrong-signed on SELL fills. CR-03: send_daily_pnl_digest (daily_pnl.py lines 415-449) calls _send_dm_blocks_respecting_quiet_hours which silently returns when in quiet window, then unconditionally writes a 'daily_pnl' audit event recording the digest as sent. Audit log asserts delivery that never happened. CR-04: expiry.py line 381 sends the expiry DM with category='routine_fill' — this category is suppressed during quiet hours, so a trade expiry (a real-money decision being dropped) can produce zero operator signal if the sweep fires during the quiet window. The phase brief explicitly flags this class of silent failure."
    artifacts:
      - path: "src/gekko/execution/executor.py"
        issue: "fill_payload dict at lines 849-860 omits strategy_name and side — both available from tp_persisted but not written"
      - path: "src/gekko/reporter/daily_pnl.py"
        issue: "lines 167 + 177: reads strategy_name and side from fill payload that never contains them; all fills aggregate under _unknown_, all treated as BUYs"
      - path: "src/gekko/reporter/daily_pnl.py"
        issue: "lines 415-449: writes daily_pnl audit event unconditionally after _send_dm_blocks_respecting_quiet_hours which may have silently returned — audit claims sent when suppressed"
      - path: "src/gekko/approval/expiry.py"
        issue: "line 381: expiry DM sent with category='routine_fill' — suppressed during quiet hours, so a dropped real-money trade decision can produce zero operator signal"
    missing:
      - "Add strategy_name and side to fill_payload in on_fill_event (both available from tp_persisted)"
      - "Have _send_dm_blocks_respecting_quiet_hours return bool (dispatched True/False); use that to conditionally write audit event or write daily_pnl_suppressed marker"
      - "Change expiry DM category from routine_fill to executor_error (non-suppressible) or add a new non-suppressible proposal_expired category"

  - truth: "Carry-forward: executor-error Slack DM on MarketClosed / BrokerOrderError with no silent failures"
    status: partial
    reason: "Severity-tier emoji prefixes (warning emoji on MarketClosed, error emoji on BrokerOrderError) are implemented in executor.py. The AST gate in test_executor_error_dms_coverage.py verifies every FAILED transition has a sibling _send_slack_dm call. However, CR-04 represents a new silent-failure class: proposal expiry during quiet hours. The expiry DM uses category='routine_fill' which is suppressible — a real-money proposal that expires during the quiet window transitions to EXPIRED with no operator notification. This is the same 'no silent failures' class the carry-forward was designed to close."
    artifacts:
      - path: "src/gekko/approval/expiry.py"
        issue: "line 381: expiry DM category='routine_fill' is suppressible; proposal EXPIRED transitions can be silent during quiet hours"
    missing:
      - "Change expiry DM to use a non-suppressible category (executor_error or a new proposal_expired bypass category)"
---

# Phase 3: Production HITL UX Verification Report

**Phase Goal:** User has a production-grade approval surface — idempotent Slack buttons that survive at-least-once delivery, configurable quiet hours, timeout=REJECT default, edit-size and escalate-to-dashboard options, stale-proposal expiry, dashboard fallback, and a daily P&L digest with severity-tier executor-error DMs.
**Verified:** 2026-06-18T00:00:00Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Slack Block Kit card with idempotent approve/reject/edit-size/escalate buttons; clicking same button twice = exactly one action | PARTIAL | claim_action UNIQUE-INSERT backstop is correct and wired to both Slack and dashboard approve/reject (dedup.py verified). X-Slack-Retry-Num gate is dead code in Socket Mode (WR-08 confirmed). Dashboard /live-confirm POST — the HITL-06 second-channel gate — has NO require_session. |
| 2 | Quiet hours configurable; proposals queue during window, delivered when it opens | VERIFIED | quiet_hours.py _resolve_quiet_hours is correct (IANA tz, DST, overnight wrap, strategy-override-wins). User.quiet_hours_start/end/timezone columns present in DB. All DM routes checked: _send_slack_dm_respecting_quiet_hours wired in executor.py; _send_dm_blocks_respecting_quiet_hours wired in daily_pnl.py. Queuing behavior: quiet-hours suppression applies to PENDING proposal DMs. |
| 3 | Proposal expires after configurable timeout (default 30 min), auto-rejects with notification; timeout=EXECUTE not configurable | VERIFIED | expire_stale_proposals sweep in expiry.py exists, wired to 60s IntervalTrigger in scheduler/jobs.py, registered in dashboard lifespan. PROPOSAL_TIMEOUT_DEFAULT_MIN=30 in proposal_writer.py. expires_at stamped server-side after model_validate (LLM cannot influence). STATE_TRANSITIONS includes (PENDING, EXPIRED). No timeout=EXECUTE option in strategy schema. Expiry DM fires (though suppressible — see CR-04 gap). |
| 4 | User can edit proposed order size from Slack card and approve in a single interaction with audit record | VERIFIED | _drift_check shared helper (actions.py) used by both Slack view_submission handler and dashboard edit-submit. 2% drift guard present. Slack views.open modal with private_metadata round-trip confirmed in slack_handler.py. Dashboard /approvals/{id}/edit-submit has require_session, claim_action dedup with source='dashboard', edit_size audit event. |
| 5 | Dashboard /approvals page: same approve/reject/edit flow executes identically when Slack unavailable | FAILED | /approvals GET/POST routes correctly gated with require_session and claim_action (lines 224, 261, 345, 421, 487, 689, 727). However /live-confirm GET+POST, /kill, /unkill, /promote-to-live, /trigger — all on the same router — are UNAUTHENTICATED (CR-01 confirmed). The "identical execution" claim fails because the second-channel confirmation gate (HITL-06) has no auth, defeating the dual-channel safety guarantee. Walking-skeleton test_dashboard_fallback does not cover live-confirm route. |

**Score:** 2/5 truths fully verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/gekko/approval/dedup.py` | claim_action UNIQUE-INSERT idempotency | VERIFIED | Substantive — 200 lines, correct IntegrityError + rollback + fresh-session pattern |
| `src/gekko/approval/quiet_hours.py` | _resolve_quiet_hours predicate | VERIFIED | Substantive — IANA tz, overnight wrap, strategy-override, DST handling present |
| `src/gekko/approval/expiry.py` | expire_stale_proposals sweep | VERIFIED (with warning) | Substantive — sweep + chat.update + DM. Wired to scheduler. CR-04: DM category is suppressible. |
| `src/gekko/approval/proposals.py` | STATE_TRANSITIONS + transition_status | VERIFIED | EXPIRED edge present; transition_status is atomic |
| `src/gekko/dashboard/routes.py` | /approvals + /login + /live-confirm routes | PARTIAL | /approvals approve/reject/edit-size gated. /live-confirm GET+POST, /kill, /unkill, /promote-to-live, /trigger NOT gated — CR-01 BLOCKER |
| `src/gekko/reporter/daily_pnl.py` | Daily P&L digest aggregation + Block Kit | STUB (data disconnected) | Exists and substantive. Wired to APScheduler. But fill_payload in executor.py never writes strategy_name or side — all fills aggregate under _unknown_, SELLs sign-flipped |
| `src/gekko/execution/executor.py` | on_fill_event fill payload with strategy_name+side | MISSING FIELDS | fill_payload at lines 849-860 has 5 keys: no strategy_name, no side. CR-02 BLOCKER confirmed |
| `src/gekko/approval/actions.py` | _drift_check shared helper | VERIFIED | Used by both Slack and dashboard edit-size paths |
| `src/gekko/dashboard/app.py` | SessionMiddleware + auth middleware | PARTIAL | SessionMiddleware present. _inject_banner_state middleware does NOT enforce auth — only injects banner state. No global auth gate. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| Slack approve button | executor.place_order | claim_action -> transition_status -> execute_proposal | WIRED | Verified in slack_handler.py + dedup.py chain |
| Dashboard /approvals/approve | executor.place_order | require_session -> claim_action -> execute_proposal | WIRED | Verified with require_session at line 261 |
| Dashboard /live-confirm POST | executor.place_order | (no auth) -> transition_status -> execute_proposal | WIRED (UNSAFE) | Route exists and executes correctly, but has no auth dependency — CR-01 |
| on_fill_event | daily_pnl aggregation | fill audit event payload | BROKEN | fill_payload omits strategy_name + side — aggregation reads fields that are never written |
| _send_daily_pnl_digest | audit log | daily_pnl event | INCORRECT | Audit event written unconditionally even when DM suppressed by quiet hours — CR-03 |
| expire_stale_proposals | operator DM | category=routine_fill | SUPPRESSIBLE | Expiry DM can be silenced during quiet hours — CR-04 |
| expire_stale_proposals | APScheduler | 60s IntervalTrigger | WIRED | Confirmed in scheduler/jobs.py + app.py lifespan |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| `daily_pnl.py _aggregate_today_events` | strategy_name (per-strategy breakdown) | fill audit event payload.get('strategy_name') | NO — field never written by on_fill_event | DISCONNECTED |
| `daily_pnl.py _aggregate_today_events` | side (sign of P&L) | fill audit event payload.get('side', 'buy') | NO — field never written; defaults to 'buy' for all fills | HOLLOW_PROP |
| `daily_pnl.py send_daily_pnl_digest` | dispatched (DM actually sent) | return value of _send_dm_blocks_respecting_quiet_hours | NO — function returns None; caller cannot know if DM was suppressed | STATIC |

### Behavioral Spot-Checks

Step 7b: SKIPPED — no runnable entry points accessible without active SQLCipher DB and Slack credentials. Walking-skeleton cassette tests substitute (Plan 03-07 confirms 4/4 pass).

### Probe Execution

No probe-*.sh files defined for Phase 3. Walking-skeleton cassette (`tests/integration/test_p3_walking_skeleton.py`) used instead per 03-07-SUMMARY.md. SUMMARY claims 4/4 pass. Note: P&L digest assertion was relaxed from strategy-name check to 'fills in text' (documented in 03-07-SUMMARY.md) — this means the cassette does NOT catch CR-02 (the strategy bucketing failure). The cassette is passing precisely because it was weakened to tolerate the bug.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|---------|
| HITL-02 | 03-02 | Slack buttons idempotent — at-least-once delivery cannot cause double-execution | PARTIAL | claim_action backstop verified. X-Slack-Retry-Num gate dead in Socket Mode (WR-08). |
| HITL-03 | 03-04 | Timeout = REJECT default; proposals expire after 30 min configurable | SATISFIED | Sweep + expires_at + STATE_TRANSITIONS verified. Expiry DM suppressible is a warning. |
| HITL-05 | 03-03 | Quiet hours configurable; no 2am pings | SATISFIED | _resolve_quiet_hours + _send_slack_dm_respecting_quiet_hours wired. |
| DASH-04 | 03-05 | Web dashboard approval fallback | PARTIAL | /approvals gated correctly. /live-confirm, /kill, /promote-to-live unauthenticated — CR-01 BLOCKER. |
| REPT-01 | 03-06 | Slack DM: daily P&L digest + executor errors + alerts | PARTIAL | Digest fires, Block Kit built, NYSE gate, APScheduler cron wired. But fill payload missing strategy_name+side — digest numbers wrong. Severity-tier emoji prefixes present. |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `src/gekko/dashboard/routes.py` | 1308, 1386 | GET+POST /live-confirm has no require_session | BLOCKER | Any unauthenticated caller can confirm a real-money HITL-06 gate — defeats dual-channel safety |
| `src/gekko/dashboard/routes.py` | 1123, 1166 | POST /kill, POST /unkill has no require_session | BLOCKER | Kill switch toggle open to any local process |
| `src/gekko/dashboard/routes.py` | 1265 | POST /strategies/{name}/promote-to-live has no require_session | BLOCKER | Live promotion unauthenticated |
| `src/gekko/dashboard/routes.py` | 1041 | POST /trigger/{name} has no require_session | BLOCKER | Agent trigger unauthenticated — spends Claude budget |
| `src/gekko/execution/executor.py` | 849-860 | fill_payload dict omits strategy_name and side | BLOCKER | Daily P&L digest reads these fields; all fills bucket under _unknown_, all SELLs sign-flipped |
| `src/gekko/reporter/daily_pnl.py` | 415-449 | daily_pnl audit event written unconditionally after possibly-suppressed DM | BLOCKER | Audit log claims digest sent when operator never received it — audit integrity violation |
| `src/gekko/approval/expiry.py` | 381 | Expiry DM uses category='routine_fill' | BLOCKER | Real-money trade expiry can be silently dropped with zero operator signal during quiet hours |
| `src/gekko/approval/expiry.py` | 260-276 | with_for_update() on SQLite — misleading comment | WARNING | No actual row lock in SQLite WAL mode; race safety is via transition_status ValueError catch (correct). Misleading comment for future maintainers. |
| `src/gekko/approval/slack_handler.py` | 146-158 | _extract_retry_num reads body['headers'] — not present in Socket Mode | WARNING | Retry gate is dead code in production transport; dedup relies solely on claim_action (which is correct) |
| `src/gekko/dashboard/routes.py` | 1392, 1429 | page_load_ts read from Form(...) — client-controlled | WARNING | 5-second read timer uses client-supplied baseline despite docstring claiming "pure server-side"; bypassed by supplying past timestamp |
| `src/gekko/dashboard/app.py` | 240-247 | os.urandom(32) session secret rotates on every restart | WARNING | Restart under NSSM/launchd (mandatory in project) invalidates operator session mid-day; can interact with CR-04 silent-expiry path |
| `src/gekko/dashboard/routes.py` | 1249 | import time as _time placed mid-module | INFO | Convention violation — imports at top |
| `src/gekko/reporter/daily_pnl.py` | 329-335 | Docstring references _send_slack_dm_blocks_respecting_quiet_hours which does not exist in executor.py | INFO | Misleading docstring pointing at non-existent symbol; logic is re-implemented locally (intentional to avoid circular import but undocumented) |

### Human Verification Required

#### 1. Slack Block Kit card rendering

**Test:** Trigger a strategy run via `/gekko trigger <name>`. Observe the Slack DM in the operator's workspace.
**Expected:** Proposal card appears with approve / reject / edit-size / escalate-to-dashboard buttons; card is visually distinct for paper vs. live (paper chip vs. live chip).
**Why human:** Visual appearance and live Slack API response cannot be verified via grep.

#### 2. Edit-size modal interaction

**Test:** Click edit-size on a pending proposal card in Slack. Modify the quantity within 2% drift. Submit.
**Expected:** Modal closes, card updates to APPROVED state, executor fires in background.
**Why human:** Slack modal interaction requires live workspace; cannot simulate view_submission flow without Slack credentials.

#### 3. Quiet-hours queuing behavior

**Test:** Configure quiet hours covering the current time. Trigger a strategy run. Wait until the quiet window closes.
**Expected:** No Slack DM arrives during the quiet window; DM arrives when the window opens.
**Why human:** Real-time behavior over 2+ hours cannot be verified statically.

#### 4. Dashboard fallback end-to-end (Slack unavailable)

**Test:** Disable the Slack socket connection. Navigate to /approvals. Log in. Approve a pending proposal.
**Expected:** Proposal transitions to APPROVED, executor fires, fill recorded in audit log — identical to Slack path.
**Why human:** Requires live dashboard session + executor running; cannot simulate ASGI transport without full app stack.

#### 5. Daily P&L digest at 16:30 ET (NYSE trading day)

**Test:** On a NYSE trading day, observe the 16:30 ET APScheduler cron. Verify DM arrives in Slack with correct P&L figures.
**Expected:** Block Kit digest with gross P&L = sum of today's fills (BUYs subtract, SELLs add); per-strategy breakdown by strategy name.
**Why human:** Requires real fill events with the fix in place (strategy_name + side in fill_payload); without the CR-02 fix the digest will show _unknown_ and wrong sign on SELLs regardless.

### Gaps Summary

Phase 3 delivers correct dedup/idempotency (claim_action), correct quiet-hours predicate, correct expiry sweep timing, correct edit-size drift check, and substantive Block Kit card rendering. The infrastructure is real and the happy path (Slack approve -> execute -> fill) works.

Four BLOCKERs prevent the phase goal from being achieved:

**BLOCKER 1 (CR-01) — Dashboard auth gap:** Eight safety-critical routes have no `require_session` dependency: `/live-confirm` GET+POST (the HITL-06 second-channel gate), `/kill`, `/unkill`, `/strategies/{name}/promote-to-live`, `/trigger/{name}`, and strategy CRUD routes. Anyone who can reach the dashboard port can confirm a real-money trade, flip the kill switch, or promote a strategy to live. This directly defeats the dual-channel safety guarantee that DASH-04 and HITL-06 depend on. The SUMMARY claims the phase closes HITL-06 and DASH-04 but these require auth on the second channel; without auth the second channel is not meaningfully separate from the first.

**BLOCKER 2 (CR-02) — Fill payload missing strategy_name+side:** `on_fill_event` writes fill audit events without `strategy_name` or `side` fields. The daily P&L aggregator reads those exact fields. Every fill aggregates under `_unknown_` (per-strategy breakdown useless) and every SELL fill is reported as negative P&L (sign-flipped). The headline Gross P&L figure shown to the operator is wrong on any day with sell activity. This directly breaks the "daily P&L digest" success criterion under REPT-01.

**BLOCKER 3 (CR-03) — Audit integrity violation:** `send_daily_pnl_digest` records a `daily_pnl` audit event claiming the digest was sent even when `_send_dm_blocks_respecting_quiet_hours` silently suppressed the DM due to quiet hours. The audit log is the project's "trustworthy auditable record" (core value prop). An audit event claiming delivery of a communication that was never received is a correctness defect.

**BLOCKER 4 (CR-04) — Silent proposal expiry during quiet hours:** The expiry sweep DMs the operator with `category="routine_fill"`. The quiet-hours gate suppresses `routine_fill` category DMs. If a proposal expires during the operator's quiet window and the Slack card update fails (missing coords — best-effort), the operator receives zero signal that a trade decision was dropped. The phase brief explicitly identifies "any path where a FAILED transition could be silent" as a must-catch. Proposal expiry is a real-money decision rejection; it must use a non-suppressible DM category.

Structured gaps above include the specific file/line/fix for each BLOCKER.

---

_Verified: 2026-06-18T00:00:00Z_
_Verifier: Claude (gsd-verifier)_
