---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
reviewed: 2026-06-18T00:00:00Z
depth: standard
files_reviewed: 18
files_reviewed_list:
  - migrations/versions/0004_p3_hitl_ux.py
  - src/gekko/agent/proposal_writer.py
  - src/gekko/agent/runtime.py
  - src/gekko/approval/dedup.py
  - src/gekko/approval/expiry.py
  - src/gekko/approval/proposals.py
  - src/gekko/approval/quiet_hours.py
  - src/gekko/approval/slack_handler.py
  - src/gekko/dashboard/app.py
  - src/gekko/dashboard/routes.py
  - src/gekko/db/models.py
  - src/gekko/execution/executor.py
  - src/gekko/reporter/daily_pnl.py
  - src/gekko/reporter/slack.py
  - src/gekko/scheduler/jobs.py
  - src/gekko/schemas/strategy.py
findings:
  critical: 4
  warning: 9
  info: 5
  total: 18
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-06-18T00:00:00Z
**Depth:** standard
**Files Reviewed:** 18 source files (+ supporting tests read for context)
**Status:** issues_found

## Summary

Phase 3 wires the production HITL UX: Slack action dedup/idempotency, quiet-hours +
DM routing, the stale-proposal expiry sweep, the dashboard approval fallback with
passphrase auth, and the daily P&L digest. The dedup/idempotency core (`claim_action`,
`transition_status`) and the OrderGuard 2% drift re-check on edit-size are well-built
and the audit-chain integrity is preserved across the race paths.

However, the review surfaced **four BLOCKERs**, the most serious being a wide-open
authentication gap: the entire live-money confirmation surface (`/live-confirm`),
the kill switch (`/kill`, `/unkill`), the live-promotion route, the agent-trigger
route, and all `/strategies` routes are mounted **without** the `require_session`
auth dependency. On a real-money trading agent this means anyone who can reach the
dashboard port can confirm a live trade, disable the kill switch, or promote a
strategy to live — defeating the dual-channel HITL-06 gate entirely. Two correctness
BLOCKERs concern the daily-P&L digest reporting wrong numbers because the fill audit
event never carries `strategy_name` or `side`, and a silent-FAILED path where an
expired/rejected proposal during quiet hours produces no operator signal.

The findings below are ordered by severity.

## Critical Issues

### CR-01: Live-confirm, kill switch, promote-to-live, trigger, and strategy routes are UNAUTHENTICATED

**File:** `src/gekko/dashboard/routes.py` (routes at lines 814–1518); auth dep defined at `routes.py:79`
**Issue:** Only seven handlers declare `user_id: str = Depends(require_session)`
(`/approvals` GET 224, approve 261, reject 345, edit-size GET 421, edit-submit 487,
settings GET 689, settings POST 727). The following safety-critical routes have **no**
auth dependency and there is no app-level/router-level auth middleware
(`app.py`'s only `@app.middleware("http")` is `_inject_banner_state`, which does not
enforce auth):

- `POST /live-confirm/{proposal_id}` (`routes.py:1386`) — the HITL-06 second-channel
  gate that transitions `AWAITING_2ND_CHANNEL → APPROVED_LIVE` and dispatches the
  **live-money** executor.
- `GET /live-confirm/{proposal_id}` (`routes.py:1308`) — leaks full proposal detail
  (ticker, side, qty, notional, rationale) to any unauthenticated caller.
- `POST /kill` (`1123`), `POST /unkill` (`1166`), `GET /kill/state` (`1194`) — anyone
  can flip the operator safety kill switch on or off.
- `POST /strategies/{name}/promote-to-live` (`1265`) — anyone can promote a strategy
  to live-eligible.
- `POST /trigger/{name}` (`1041`) — anyone can fire an agent run (spends Claude API
  budget; produces real proposals).
- `GET /strategies`, `GET /strategies/{name}/edit`, `POST /strategies/{name}/save`
  (`825`, `912`, `973`) — read/write strategy config with no session check; these
  read `request.app.state.engine` directly and trust `settings.gekko_user_id`.

This nullifies the dual-channel "second channel must be authenticated" premise of
HITL-06 and the kill-switch operator-safety guarantee. Localhost-only binding is a
mitigation but not a substitute for auth (any local process, any other local user,
SSRF from another local service, or a misconfigured bind reaches these).

**Fix:** Add the auth dependency to every state-changing / sensitive route. Prefer a
router-level dependency so new routes are secure by default:
```python
router = APIRouter(dependencies=[Depends(require_session)])
# then drop the per-route Depends, and explicitly exempt /login and /healthz:
@router.get("/login", dependencies=[])  # public
@router.get("/healthz", dependencies=[])  # public
```
For the `/strategies*`, `/trigger`, `/kill*`, `/promote-to-live`, and `/live-confirm*`
handlers specifically, add `user_id: str = Depends(require_session)` and use that
`user_id` instead of re-reading `settings.gekko_user_id`.

### CR-02: Daily P&L digest reports wrong per-strategy P&L because fill events lack `strategy_name` and `side`

**File:** `src/gekko/reporter/daily_pnl.py:165-190` (consumer); `src/gekko/execution/executor.py:849-860` (producer)
**Issue:** `_aggregate_today_events` reads `payload.get("strategy_name", "_unknown_")`
and `payload.get("side", "buy")` from each `fill` audit event. But the only production
writer of `fill` events — `on_fill_event` in `executor.py` — builds the payload as:
```python
fill_payload = normalize_decimals({
    "event_kind": "fill",
    "client_order_id": ..., "broker_order_id": ...,
    "filled_qty": ..., "filled_avg_price": ..., "ticker": ticker,
})  # NO strategy_name, NO side
```
Consequences on real fills:
1. Every fill aggregates under the literal bucket `"_unknown_"`, so the per-strategy
   breakdown in the digest is meaningless.
2. `side` defaults to `"buy"`, so **every** fill is treated as a cash outflow
   (`-(price*qty)`). A profitable SELL is reported as a large negative P&L. The
   headline "Gross P&L" number the operator reads is wrong (and wrong-signed).

The aggregation unit test passes only because it hand-seeds events that include
`strategy_name` and `side` (`test_daily_pnl_aggregation.py:177-205`) — the test fixture
does not match what `on_fill_event` actually writes.

**Fix:** Add `strategy_name` and `side` to the `fill` payload in `on_fill_event`. Both
are already available there (`tp_persisted.strategy_name`, and `side`/`tp.side`):
```python
fill_payload = normalize_decimals({
    "event_kind": "fill",
    "client_order_id": payload.get("client_order_id", ""),
    "broker_order_id": payload.get("broker_order_id", ""),
    "filled_qty": str(payload.get("filled_qty", "0")),
    "filled_avg_price": str(payload.get("filled_avg_price", "")),
    "ticker": ticker,
    "strategy_name": (tp_persisted.strategy_name if tp_persisted else ""),
    "side": (str(tp_persisted.side) if tp_persisted else payload.get("side", "")),
})
```
Note this changes the canonical hashed payload shape — coordinate with the audit-chain
contract, but it is required for the digest to be correct.

### CR-03: `daily_pnl` audit event records "digest sent" even when the DM was suppressed by quiet hours

**File:** `src/gekko/reporter/daily_pnl.py:415-449`
**Issue:** `send_daily_pnl_digest` calls `_send_dm_blocks_respecting_quiet_hours(...)`
which **silently returns without sending** when `_resolve_quiet_hours` is True
(`daily_pnl.py:358-364`). Immediately after, the function unconditionally appends a
`daily_pnl` audit event (`425-439`) and returns `True`. The audit log therefore records
a delivered digest that the operator never received. For a system whose core value
prop is a trustworthy auditable record, the audit chain asserting a notification fired
when it did not is a correctness/integrity defect (the digest is also the operator's
daily proof-of-life that the agent is alive — a silent skip with a "sent" audit row
masks an outage).

**Fix:** Have the wrapper report whether it actually dispatched, and record the
suppression in the audit event (or skip the "sent" event and write a
`daily_pnl_suppressed` marker):
```python
dispatched = await _send_dm_blocks_respecting_quiet_hours(..., category="daily_pnl")
...
payload={..., "delivered": dispatched, "suppressed_by_quiet_hours": not dispatched}
```

### CR-04: Expired and rejected-in-quiet-hours proposals can leave the operator with no signal that the trade was dropped

**File:** `src/gekko/approval/expiry.py:371-388`
**Issue:** When the sweep expires a `PENDING`/`AWAITING_2ND_CHANNEL` proposal it DMs
the operator with `category="routine_fill"` (`expiry.py:374-382`). `routine_fill` is a
routine category, so `_send_slack_dm_respecting_quiet_hours` **suppresses** it during
quiet hours (`executor.py:249,274-280`). The `chat.update` of the original card is also
best-effort and is skipped when `slack_message_ts`/`channel` were never captured
(`expiry.py:116-123`). The net effect: a trade the operator was asked to approve can
silently transition to EXPIRED (timeout = REJECT semantics — the trade does NOT
execute) with **zero** operator-visible signal if the expiry lands in the quiet window
and the card coordinates are missing. The phase brief explicitly flags "any path where
a FAILED transition could be silent (no operator DM)" as a must-catch. Expiry is
functionally a reject of a real-money decision and should not be silently swallowable.

**Fix:** Treat proposal expiry as a non-suppressible category (it is an action-needed /
decision-dropped signal, not a routine fill confirmation). Either send via a bypass
category or add an `expiry` category to the bypass set:
```python
await _send_slack_dm_respecting_quiet_hours(
    user_id, dm_text, category="executor_error",  # or a new non-suppressible "proposal_expired"
)
```
At minimum, do not classify a dropped trade decision as `routine_fill`.

## Warnings

### WR-01: `expire_stale_proposals` SELECTs candidate rows and releases the lock before transitioning, so `with_for_update()` provides no protection

**File:** `src/gekko/approval/expiry.py:261-275`
**Issue:** The candidate SELECT runs inside `async with sf() as session:` and the
session is **closed at line 276 before** the per-row transition loop opens *new*
sessions (`315`). The `with_for_update()` lock (260, 273) is released the instant that
first session closes, and in any case SQLite does not implement `SELECT ... FOR UPDATE`
row locks. The comment "Explicit locking via with_for_update() — intent-conveying in
SQLite WAL mode" overstates the guarantee. The race is actually handled by the
`transition_status` ValueError catch (`323-332`), which is correct — but the misleading
lock comment invites a future maintainer to rely on protection that isn't there.
**Fix:** Remove `with_for_update()` (it's dead in SQLite) and reword the comment to
state plainly that race-safety comes from the idempotent `transition_status` /
first-write-wins, not from row locks.

### WR-02: Approve retry-gate and dedup race window allows a second approval task to be spawned before the dedup row commits

**File:** `src/gekko/approval/slack_handler.py:194-235`
**Issue:** `handle_approve` runs the X-Slack-Retry-Num gate as a SELECT in one session
(`201-210`), then unconditionally `asyncio.create_task(_approve_workflow(...))`
(`228`). The retry gate only short-circuits when `retry_num > 0` AND a prior dedup row
already exists. On a genuine rapid double-delivery where Slack sends two payloads with
`retry_num == 0` (or the second arrives before the first's `claim_action` commits), two
`_approve_workflow` tasks are spawned. The `claim_action` UNIQUE insert is still the
backstop (one wins, one gets `duplicate`), so exactly-once execution holds — but the
gate's SELECT-then-act is non-atomic and gives a false impression of protection. This
is defended in depth by the DB constraint, hence WARNING not BLOCKER, but the gate
logic should not be trusted as a dedup layer on its own.
**Fix:** Document that `claim_action`'s UNIQUE constraint is the sole authoritative
dedup; treat the retry gate purely as an ephemeral-spam reducer. Consider also gating
on `retry_num == 0` duplicates by relying only on `claim_action`.

### WR-03: `_send_dm_blocks_respecting_quiet_hours` docstring references a function that does not exist

**File:** `src/gekko/reporter/daily_pnl.py:329-335`
**Issue:** The docstring says it "Routes through
`gekko.execution.executor._send_slack_dm_blocks_respecting_quiet_hours`". No such
symbol exists in `executor.py` (only `_send_slack_dm_respecting_quiet_hours` for text
and `_send_slack_dm_blocks` for blocks). The function actually re-implements the
quiet-hours predicate logic locally (`345-369`), duplicating `executor.py:249-284`.
This duplication means the bypass-category set is now defined in two places
(`executor.py:249` and `daily_pnl.py:336`) and can drift.
**Fix:** Either add the blocks-aware wrapper to `executor.py` and call it (single source
of truth for the bypass-category set), or fix the docstring and add a comment noting the
intentional duplication. Do not leave a docstring pointing at a non-existent symbol.

### WR-04: `_format_expiry_dm` hardcodes "timeout=REJECT" but the timeout semantic is not configurable / not verified against strategy intent

**File:** `src/gekko/approval/expiry.py:201-205`
**Issue:** The DM and the expired card both state "Reason: timeout=REJECT". There is no
strategy field encoding whether timeout means REJECT vs. (future) auto-defer, yet the
copy asserts a definitive policy to the operator. If a later phase adds timeout=DEFER,
this copy becomes a lie embedded in operator-facing text. Low risk today, but it
hardcodes a policy decision into a string.
**Fix:** Pull the timeout policy from a named constant (`PROPOSAL_TIMEOUT_POLICY = "REJECT"`)
so the copy and the actual transition target stay coupled.

### WR-05: Dashboard edit-submit derives `ref_price` from operator-influenced `payload_json`, not from a locked execution anchor

**File:** `src/gekko/dashboard/routes.py:562-576`
**Issue:** The Knight-Capital 2% drift check uses `ref_price` derived from the proposal's
`limit_price`/`stop_price`/(`target_notional/original_qty`). For a market order with no
limit/stop, `ref_price = target_notional / original_qty`, which by construction makes
`original_qty * ref_price == target_notional`, so the drift check measures the new qty
against the original qty's implied price. That's the intended behavior, but note the
check is anchored to `target_notional_usd` from `payload_json` — if any earlier path
mutated `target_notional_usd` (e.g. a prior edit), the drift baseline shifts. The Slack
view-submission path (`slack_handler.py:802-816`) takes `ref_price` and
`target_notional` from `private_metadata` set at modal-open time, which is a tighter
anchor. The two surfaces compute the drift baseline differently.
**Fix:** Ensure both surfaces anchor drift to the *original* proposal's
`target_notional_usd` (the value at proposal-build T0), and never to a possibly-mutated
value. Add a test that does two sequential edits and asserts the second edit's drift is
still measured against T0 notional.

### WR-06: `live_confirm_post` 5-second read-timer trusts a client-supplied `page_load_ts` form field

**File:** `src/gekko/dashboard/routes.py:1392,1429-1437`
**Issue:** The "read for 5 seconds" gate computes `elapsed = time.time() - page_load_ts`
where `page_load_ts` is a `Form(...)` field the browser submits. A caller can submit any
value (e.g. a timestamp 10s in the past) to bypass the timer instantly. The docstring
claims "pure server-side timestamp check" (`1427-1428`) but the baseline timestamp is
client-controlled. Combined with CR-01 (route is unauthenticated), the entire
deliberation gate is bypassable. Even with auth fixed, the timer is not server-anchored.
**Fix:** Store the page-load timestamp server-side keyed to the session/proposal (e.g. in
`request.session` on the GET, or a short-lived server cache), and compare against that on
POST. Do not trust a client-submitted timestamp for a safety gate.

### WR-07: `os.urandom(32).hex()` session secret rotates on every restart, silently logging out the operator and complicating multi-worker deploys

**File:** `src/gekko/dashboard/app.py:240-247`
**Issue:** D-58 intentionally uses an ephemeral per-restart secret. That's a defensible
security choice, but it has two operational hazards worth flagging: (1) any restart
(crash/auto-restart under NSSM/launchd, which the project mandates) invalidates the
operator's session mid-trading-day, and a logged-out operator cannot action a live
proposal that just landed — interacting badly with CR-04's silent-expiry path; (2) if a
future deploy ever runs >1 uvicorn worker, each worker has a different secret and
sessions break nondeterministically. `app.py:30` notes `workers=1`, so (2) is currently
contained.
**Fix:** Keep the ephemeral secret if the security trade-off is intended, but document
the "restart = re-login" behavior in operator docs, and add a startup assertion that
`workers == 1`. Consider persisting the secret in the OS keychain (already a dependency
via `keyring`) so restarts don't force re-login.

### WR-08: Slack action `_extract_retry_num` reads headers from `body["headers"]` which may not be populated in Socket Mode

**File:** `src/gekko/approval/slack_handler.py:146-158`
**Issue:** The retry gate depends on `body["headers"]["x-slack-retry-num"]`. The project
runs Slack in **Socket Mode** (`app.py:166-192`, `socket_mode=True` per CLAUDE.md). In
Socket Mode there is no HTTP request and the `X-Slack-Retry-Num` HTTP header does not
exist — retries are delivered over the WebSocket envelope, not HTTP headers. So
`_extract_retry_num` will almost always return `0` in the production transport, making
the retry gate a no-op in production (it only works on the HTTP `/slack/events` path).
Exactly-once is still preserved by `claim_action`, so this is a WARNING — but the gate is
dead code in the primary transport and the duplicate-ephemeral-suppression UX it promises
won't fire.
**Fix:** Verify how slack-bolt surfaces retry metadata in async Socket Mode (envelope
`retry_attempt`) and read from there, or remove the gate and rely solely on `claim_action`
for both dedup and the duplicate ephemeral (the `duplicate` branch already fires the
ephemeral).

### WR-09: Strategy mutation routes re-derive `user_id` from global settings instead of the authenticated session

**File:** `src/gekko/dashboard/routes.py:828,887-906 (list), 918 (edit), 986-988 (save), 1050 (trigger), 1292 (promote), 1321-1323 (live-confirm get)`
**Issue:** These handlers compute `user_id = settings.gekko_user_id` rather than taking it
from `require_session`. In the current single-operator deployment that's equivalent, but
it bakes in the assumption that the process serves exactly one user and removes the
session as the authority for *who* is acting. Combined with CR-01 (no session required on
these routes at all), the multi-tenant isolation invariant (CLAUDE.md: "every DB query
filters by user_id") is enforced only by a global constant, not by the authenticated
principal.
**Fix:** Once CR-01 is fixed, thread the `require_session` return value as the `user_id`
for all DB scoping in these routes, and stop reading `settings.gekko_user_id` for
per-request identity.

## Info

### IN-01: `import time as _time` placed mid-module after route definitions

**File:** `src/gekko/dashboard/routes.py:1249`
**Issue:** `import time as _time` appears at module scope but in the middle of the file
(after many route defs), used by `live_confirm_get/post`. Works, but violates the
imports-at-top convention and is easy to miss.
**Fix:** Move to the top-level import block.

### IN-02: Unused / redundant local import of `HTMLResponse`

**File:** `src/gekko/dashboard/routes.py:675`
**Issue:** `from fastapi.responses import HTMLResponse as _HTMLResponse` is imported inside
`edit_size_submit` but never used (the function returns `templates.TemplateResponse`).
**Fix:** Remove the dead import.

### IN-03: `cap_rejections_count` aggregated but never rendered in the digest

**File:** `src/gekko/reporter/daily_pnl.py:194-195,260-265`
**Issue:** `_aggregate_today_events` counts `cap_rejection` events into
`DigestData.cap_rejections_count`, but `_build_digest_blocks` never surfaces it (the
counts line shows only open positions / fills / errors). OrderGuard cap rejections are
exactly the kind of safety signal an operator wants in the daily digest.
**Fix:** Add cap-rejections to the counts context line, or drop the unused field.

### IN-04: `handle_escalate_stub` is dead code retained for backward compat

**File:** `src/gekko/approval/slack_handler.py:942-961`
**Issue:** Documented as never-called (D-60 converted Escalate to a URL button). It only
logs a warning. Harmless, but it's dead code that still registers a handler shape.
**Fix:** Remove once you confirm no Bolt registration references `handle_escalate_stub`
by name, or keep with the existing clear deprecation note.

### IN-05: Misleading "P&L" naming — the digest computes cash flow, not realized P&L

**File:** `src/gekko/reporter/daily_pnl.py:115-127,170-182`
**Issue:** The "gross P&L" is computed as signed cash flow (BUY = −cost, SELL = +proceeds)
over today's fills only. For an intraday round-trip this approximates realized P&L, but a
day with only BUYs shows a large negative "P&L" that is actually just capital deployed,
not a loss. The docstring acknowledges this, but the operator-facing label "Gross P&L"
will read as a loss. (Out of strict correctness scope, but materially misleading on a
trading dashboard.)
**Fix:** Rename the headline to "Net cash flow today" until true cost-basis P&L is
implemented, or compute realized P&L against position cost basis.

---

_Reviewed: 2026-06-18T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
