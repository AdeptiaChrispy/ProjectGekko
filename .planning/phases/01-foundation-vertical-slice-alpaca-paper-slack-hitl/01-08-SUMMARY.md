---
phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl
plan: 08
subsystem: slack-executor
tags: [slack-bolt, hitl, block-kit, market-hours, executor, fill-stream, hitl-01, hitl-04, exec-10, d-06, d-14, d-15, d-20, anti-pattern-1, pitfall-3, pitfall-4, pitfall-6, t-01-08-01, t-01-08-04, reg-01]
requires:
  - phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl
    plan: 03
    provides: |
      gekko.db.engine.get_async_engine + gekko.db.session.make_session_factory
      (per-user SQLCipher engine; both _approve_workflow and execute_proposal
      use these via the module-level _get_session_factory accessor that tests
      monkeypatch); gekko.db.models.Proposal status column (D-11 lifecycle —
      this plan walks PENDING -> APPROVED -> EXECUTING -> FILLED) +
      broker_order_id column (populated by the Executor for fill correlation).
  - phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl
    plan: 04
    provides: |
      gekko.audit.log.append_event (Slack approval / rejection events;
      Executor's order_submitted + error events; on_fill_event's fill event —
      all four event types ride the same SHA-256 hash chain);
      gekko.audit.canonical.normalize_decimals (Pitfall 6 — Executor's
      order_submitted payload + on_fill_event's fill payload pass through
      this before append_event).
  - phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl
    plan: 05
    provides: |
      gekko.brokers.alpaca.AlpacaBroker.place_order (paper-only constructor
      guard + duplicate-id 422 handler); gekko.brokers.base.OrderRequest /
      OrderResult / Brokerage ABC (the Executor's broker surface);
      gekko.brokers.stream.AlpacaFillStream (Plan 01-09 wires on_fill_event
      as the FillCallback when constructing the per-user stream).
  - phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl
    plan: 06
    provides: |
      gekko.schemas.proposal.TradeProposal / NoActionProposal (the Slack card
      reads off the fields; the Executor re-validates from payload_json);
      gekko.schemas.event.OrderSubmittedEventPayload (the order_submitted
      audit event payload conforms to this Pydantic model — verified by
      unit test #8).
  - phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl
    plan: 07
    provides: |
      gekko.agent.runtime.trigger_strategy_run (the /gekko run slash command
      fires this via asyncio.create_task; the agent run emits its own
      proposal + decision events into the audit chain BEFORE the user clicks
      Approve); gekko.agent.runtime._get_passphrase (the Executor and Slack
      handler import this — the same SQLCipher passphrase indirection that
      Plan 01-09's CLI bootstrap closes).
provides:
  - "gekko.reporter.slack.build_proposal_card(proposal, account_mode='PAPER', company_name=None, sector=None) -> list[dict] — HITL-01 verbose Block Kit card. Header banner (🟢 PAPER / 🔴 LIVE); primary-fields section (Ticker / Company / Sector / Side / Qty / Type@Price / Confidence / Strategy) with _unknown_ italicized fallback when company_name or sector is None; Rationale, Evidence (3-5 bullets with mrkdwn links), Alternatives Considered (1+ bullets); four buttons (Approve, Reject, Edit Size, Escalate) with action_ids and value=decision_id; REG-01 disclosure context block."
  - "gekko.reporter.slack.build_no_action_message(no_action, cost_usd=None) -> str — D-09 verbose no_action line with REG-01 disclosure appended."
  - "gekko.reporter.slack.build_fill_confirmation(*, client_order_id, broker_order_id, filled_qty, filled_avg_price, ticker, strategy_name, side) -> str — single-line DM text per SKELETON Demo Script."
  - "gekko.reporter.slack._escape_mrkdwn — defangs prompt-injected mrkdwn metacharacters (`< > * _ ~ | `) in LLM-authored fields (rationale, evidence.summary, alternatives_considered, company_name, sector, strategy_name, NoActionProposal.rationale + factors_considered). Trusted fields (HttpUrl, Literal source_type, Decimal, schema ids/tickers) are NOT escaped."
  - "gekko.execution.market_hours.is_market_open(now=None) + next_market_open(now=None) — pandas_market_calendars NYSE schedule (EXEC-10). Half-day aware (Black Friday 1pm close caught). Accepts tz-aware OR tz-naive datetimes (naive treated as UTC). Calendar instance cached via lru_cache."
  - "gekko.approval.proposals.STATE_TRANSITIONS — the canonical (from, to) set for the proposal lifecycle. Includes APPROVED -> FAILED for the market-hours rejection path."
  - "gekko.approval.proposals.transition_status(session, proposal_id, *, from_status, to_status) -> ProposalRow — atomic SELECT + UPDATE. Idempotent same-state; raises ValueError on invalid transitions (defense in depth)."
  - "gekko.approval.proposals.approve_proposal / reject_proposal — transition + append the matching audit event (`approval` / `rejection`) inside the caller's transaction. Event payload carries {proposal_id, actor (Slack user id), slack_action_id} so the audit chain captures WHO clicked WHICH button."
  - "gekko.slack.commands.handle_gekko_command(ack, command, respond) — the /gekko run <strategy> slash command (D-06 trigger surface). ack() FIRST per Pitfall 3; trigger_strategy_run dispatched via asyncio.create_task; empty/unknown subcommands return help; bare 'run' returns usage."
  - "gekko.approval.slack_handler.handle_approve / handle_reject — HITL-04 action handlers. ack-first; the DB transition + Executor dispatch (or rejection event) runs in a background _approve_workflow / _reject_workflow task. Cross-user defense: if body['user']['id'] != proposal.user_id the handler DMs 'not the owner' and exits without state mutation (T-01-08-01)."
  - "gekko.approval.slack_handler.handle_edit_size_stub / handle_escalate_stub — P3-deferred buttons. Each DMs 'coming in Phase 3' and logs structlog.warning feature.deferred."
  - "gekko.slack.app — process-wide slack-bolt AsyncApp + AsyncSlackRequestHandler singleton constructed at module import. Plan 01-09 mounts the handler at POST /slack/events via FastAPI."
  - "gekko.slack.interactivity — side-effect-only module that registers the four @slack_app.action handlers + the /gekko slash command against the slack_app singleton. Plan 01-09's lifespan imports this during startup."
  - "gekko.execution.executor.execute_proposal(proposal_id, user_id) -> None — the deterministic Python firewall. Loads + validates the APPROVED row; runs is_market_open (EXEC-10); constructs OrderRequest with the persisted deterministic client_order_id (D-20); calls AlpacaBroker.place_order; on BrokerOrderError appends error event + APPROVED -> FAILED + DMs the user; on success appends order_submitted event + APPROVED -> EXECUTING + persists broker_order_id for fill correlation. NO claude_agent_sdk imports (architectural firewall per Anti-Pattern 1)."
  - "gekko.execution.executor.on_fill_event(payload, *, user_id) -> None — TradingStream callback. Looks up the proposal by client_order_id; appends fill event with normalize_decimals; transitions EXECUTING -> FILLED; sends Slack DM via build_fill_confirmation. Unmatched fills (no proposal row) are logged but do not raise."
  - "Test seams on gekko.execution.executor + gekko.approval.slack_handler: _get_session_factory(user_id) -> (sf, engine_or_None); _build_broker(user_id) -> Brokerage; _send_slack_dm(user_id, text). All three are module-level so tests monkeypatch them without touching production wiring."
affects:
  - 01-09 (CLI + dashboard + scheduler — gekko.slack.app.slack_handler is the FastAPI route handler Plan 01-09 mounts at POST /slack/events; the same lifespan that calls runtime.set_passphrase MUST also call _set_executor_passphrase if Plan 01-09 introduces a fork; AlpacaFillStream is constructed in the lifespan with on_fill=on_fill_event)
  - 02 (P2 OrderGuard — wraps Brokerage.place_order. The Executor's call site becomes guard.place_order(req) with no other code changes — the OrderRequest construction, market-hours guard, audit events, and state-machine transitions are all upstream of the guard)
  - 03 (P3 HITL UX hardening — edit_size and escalate_to_dashboard stubs become real handlers; the slack approval workflow gains an idempotency_key column on proposals so duplicate clicks deterministically converge instead of relying on at-least-once delivery tolerance; SKELETON.md T-01-08-05 disposition flips from 'accept' to 'mitigate')
  - 07 (P7 schedule-aware scheduling — the after-hours rejection path becomes a deferred-retry-on-next-open path. The 'error' event with context 'executor.market_closed' becomes the deferral marker the scheduler watches for.)
tech-stack:
  added:
    - "aiohttp>=3.9 — slack-bolt's AsyncApp imports aiohttp at module-load even when only the FastAPI adapter is in use. Pinning it explicitly stops slack.app from failing to import."
  patterns:
    - "ack-first + fire-and-forget background workflow. Every Slack handler calls await ack() as the FIRST statement, then schedules the actual DB/Executor work via asyncio.create_task. This keeps the Slack 3-second timeout safe (Pitfall 3) and lets the agent run / broker call take however long they take without Slack retrying the request."
    - "Cross-user defense at handler boundary. body['user']['id'] is the click-source identity; proposal.user_id is the row's owner. The handler refuses (DM 'not the owner') when they don't match. The check is INSIDE the workflow (after ack) so a malicious approve click still costs one DB lookup; the broker call never fires."
    - "Module-level test seams (_get_session_factory, _build_broker, _send_slack_dm) instead of constructor injection. Both slack_handler and executor work this way. Tests monkeypatch the symbol; production calls go through the real engine + broker + slack-bolt client. No DI framework, no wrapper classes."
    - "Block Kit defense against prompt-injected mrkdwn. _escape_mrkdwn() backslash-escapes < > * _ ~ | ` in LLM-authored free-form text and collapses whitespace runs. Trusted fields (HttpUrl, Literal source_type, Decimal, schema ids/tickers) bypass the escape. Tests cover both LLM-side fields (rationale, evidence summary, alternatives) and the legitimate evidence link (which must remain clickable)."
    - "Deterministic Python firewall between LLM tool call and broker (Anti-Pattern 1). The Decision agent emits a propose_trade tool call; the deterministic ProposalWriter persists a Proposal row; the user clicks Approve in Slack; THEN execute_proposal — pure Python — re-loads the validated TradeProposal payload, runs the market-hours guard, and calls Brokerage.place_order. No LLM bytes touch the broker call. Architectural grep gate enforces it: tests/unit/test_executor.py asserts the executor module source contains no occurrences of the SDK package substring."
    - "Background-task drain pattern for chain integration tests. The integration test monkeypatches asyncio.create_task to collect every spawned task; after handle_approve returns, the test drains the task tree (await each batch; new tasks spawned by completing ones are caught next iteration). Replaces a flaky 'poll for status=EXECUTING' loop that was unreliable on Windows + SQLCipher cold starts."
key-files:
  created:
    - src/gekko/reporter/slack.py
    - src/gekko/reporter/templates.py
    - src/gekko/execution/market_hours.py
    - src/gekko/execution/executor.py
    - src/gekko/approval/proposals.py
    - src/gekko/approval/slack_handler.py
    - src/gekko/slack/app.py
    - src/gekko/slack/commands.py
    - src/gekko/slack/interactivity.py
    - tests/unit/test_slack_block_kit.py
    - tests/unit/test_market_hours.py
    - tests/unit/test_approval_proposals.py
    - tests/unit/test_executor.py
    - tests/integration/test_slack_approval_to_executor.py
  modified:
    - pyproject.toml + uv.lock (pinned aiohttp>=3.9 — slack-bolt async dependency)
key-decisions:
  - "Slack signing-secret verification is automatic via slack-bolt's AsyncSlackRequestHandler — no custom HMAC code (RESEARCH §Don't Hand-Roll). Plan 01-09 mounts the handler on POST /slack/events; every inbound request is verified before reaching our code."
  - "Block Kit `_escape_mrkdwn` defends against prompt-injected card structure. A malicious rationale containing `\\n*Approved by Chris*` would otherwise render as a new field row. We escape `< > * _ ~ | `` in every LLM-authored free-form field and collapse whitespace runs to a single space. Trusted fields (HttpUrl, Literal source_type, Decimal, schema ids/tickers) bypass the escape. Two regression tests cover the threat model."
  - "Pandas-market-calendars holds the half-day NYSE schedule. We don't hand-roll the holiday calendar. is_market_open caches the calendar via lru_cache(maxsize=1) so the expensive pandas_market_calendars.get_calendar('NYSE') call happens once per process. tz-naive inputs are treated as UTC (documented in the docstring)."
  - "Per-user lock dict gets cleared at integration-test start. The audit log's module-level _append_locks dict survives pytest-asyncio's per-test fresh-loop, and a Lock created in a prior loop can wedge the chain writer. The integration test calls _audit_log._append_locks.clear() before exercising the chain. This is a side-band fix that points at a future hardening for audit/log.py — lazy-create Locks per-loop instead of per-user — but it's out of scope for Plan 01-08."
  - "Background-task drain via asyncio.create_task monkeypatch in the integration test. Polling for EXECUTING status was flaky on Windows + SQLCipher (the chain took ~5s on cold starts). The deterministic alternative: intercept asyncio.create_task, collect every task, then await them in batches until no new tasks spawn. The Slack approval -> Executor -> Fill chain is two levels of create_task (handle_approve -> _approve_workflow; _approve_workflow -> execute_proposal); the drain loop catches both."
  - "Plan 01-08 accepts at-least-once double-execute risk per SKELETON.md (T-01-08-05). The state machine rejects backward transitions (APPROVED -> PENDING is invalid) and the broker dedups by deterministic client_order_id (Pitfall 4) — two layers of safety. Plan 01-03 adds idempotency_key on proposals as the third layer."
  - "_get_session_factory is the test-seam version of the SQLCipher engine indirection. In production it builds a per-user SQLCipher engine via the cached passphrase (Plan 01-09's CLI bootstrap populates the cache). Tests monkeypatch it to return a pre-built session factory bound to the temp_sqlcipher_db fixture's engine + None for the engine (signalling 'don't dispose'). Same pattern as gekko.agent.runtime._get_passphrase from Plan 01-07."
  - "execute_proposal persists broker_order_id on the proposals row AT the same transaction as the status transition (APPROVED -> EXECUTING) + the order_submitted audit event. The row's broker_order_id column is the dashboard's correlation key for the 'trade timeline' view (Plan 01-09). Failing to persist it would force the dashboard to JOIN events -> proposals through client_order_id, which works but is slower."
  - "Executor never imports claude_agent_sdk. Grep gate in tests/unit/test_executor.py reads the module source bytes and asserts the SDK package substring is absent. A future refactor that transitively pulled the SDK in would trip this test. The Decision agent's only side-effect-capable tools are propose_trade / propose_no_action; once those write a Proposal row, the LLM has no further reach into the broker path."
  - "Fixed FK ordering bug in test_approval_proposals.py's _seed_user_and_strategy helper. SQLAlchemy 2.x doesn't auto-order INSERTs by FK dependency unless a relationship() is declared on the parent (it isn't on gekko.db.models — D-21 keeps the model layer flat). The helper now does `await session.flush()` between the User and Strategy adds so SQLCipher's PRAGMA foreign_keys=ON sees the User row before the Strategy INSERT."
patterns-established:
  - "Pattern: ack-first + asyncio.create_task background workflow. EVERY Slack handler in the project must follow this — await ack() FIRST; schedule actual work via create_task. The 3-second Slack timeout (Pitfall 3) is non-negotiable."
  - "Pattern: cross-user defense at the body['user']['id'] vs row.user_id boundary. Any Slack handler that mutates per-user state (approvals, kill switches, dashboard escalations in P3) must do this check. The Pydantic schema's user_id field on the proposal/strategy is the authoritative owner."
  - "Pattern: deterministic Python firewall between LLM and broker. The Executor is the canonical example. Future LLM-tool-call -> external-system writes (broker, email, browser automation in P9) MUST follow this — LLM emits structured tool call; deterministic Python writer validates + persists + calls the external system. NO direct broker/email/browser calls from within the LLM."
  - "Pattern: module-level test seams for engines and external clients (_get_session_factory, _build_broker, _send_slack_dm). Avoids constructor wiring + DI frameworks. Tests monkeypatch the symbol; production uses the real implementation. Reused for any future per-user engine + per-user broker + per-user Slack client wiring."
  - "Pattern: pandas_market_calendars + lru_cache for the holiday schedule. The lru_cache(maxsize=1) on _nyse_calendar() makes the expensive .get_calendar('NYSE') call once per process. Half-day awareness comes for free. Future market hours questions (P7 deferred-retry scheduler, P9 international markets) extend the same pattern with different calendars."
requirements-completed:
  - HITL-01
  - HITL-04
  - EXEC-10
metrics:
  duration_minutes: 110
  completed: "2026-06-11T12:35:00Z"
---

# Phase 01 Plan 08: Slack HITL + Executor + Fill Stream Summary

**The HITL approval surface.** Slack Block Kit proposal card (HITL-01 verbose; all proposal fields incl. company/sector best-effort + paper banner + REG-01 disclosure + four buttons), pandas_market_calendars NYSE schedule (EXEC-10), proposals state machine (PENDING -> APPROVED -> EXECUTING -> FILLED), /gekko run slash command (D-06 third trigger surface), Approve/Reject action handlers with V4 cross-user defense (HITL-04 + T-01-08-01), the deterministic Executor that calls AlpacaBroker.place_order (D-20 / Anti-Pattern 1 firewall), and the on_fill_event TradingStream callback (BROK-A-06 wiring point for Plan 01-09).

## Plan `<output>` block answers

The plan asked the executor to record five things in this SUMMARY:

1. **Is `_GET_PASSPHRASE()` indirection still in use?** YES. Both `gekko.approval.slack_handler._get_session_factory` and `gekko.execution.executor._get_session_factory` import `gekko.agent.runtime._get_passphrase` and call it when building the per-user SQLCipher engine. **Plan 01-09 must close this loop** — its `gekko serve` startup MUST call `runtime.set_passphrase(<verified passphrase>)` BEFORE any FastAPI route can serve a Slack request. Both modules raise `RuntimeError` with a clear "passphrase not set" message if Plan 01-09's bootstrap forgets to populate the cache. (Same indirection Plan 01-07 introduced — Plan 01-09 must satisfy it.)

2. **Executor does not import `claude_agent_sdk`.** Verified two ways:
   - **Grep gate test** (`tests/unit/test_executor.py::test_executor_module_does_not_import_claude_agent_sdk`) reads `gekko.execution.executor`'s source bytes and asserts the SDK package substring is absent.
   - **Direct grep**: `grep -r "claude_agent_sdk" src/gekko/execution/` returns empty.
   The Executor's transitive imports — `gekko.agent.runtime._get_passphrase` — does pull the SDK at the runtime module level (the orchestrator side), but the Executor module itself stays clean of the LLM-side namespace. The architectural firewall per Anti-Pattern 1 holds: once a Proposal row is APPROVED, no LLM bytes can reach `place_order`.

3. **Manual verification of HITL-01 (real Slack DM, Block Kit rendering).** **Deferred to Plan 01-09.** Plan 01-08 does not boot the FastAPI process; the manual verification needs a running `gekko serve` + a real Slack token. VALIDATION.md §Manual-Only Verifications row 1 will be checked off during Plan 01-09's manual smoke (ngrok or staging Slack workspace; click Approve / Reject / Edit Size / Escalate; confirm banner / fields / disclosure render). The unit-level coverage (19 Block Kit tests + the prompt-injection escape regression) is the auto gate.

4. **Manual verification of BROK-A-06 (real fill via TradingStream websocket).** **Deferred to Plan 01-09.** Plan 01-05 covered the AlpacaFillStream's local mock + cassette round-trip; Plan 01-08 wires `on_fill_event` as the callback signature but does not actually attach it to a live stream. Plan 01-09's FastAPI lifespan owns the construction (`AlpacaFillStream(api_key=..., secret_key=..., user_id=..., on_fill=on_fill_event)` + `stream.start()`), and the manual verification is the user clicking Approve on a paper limit order whose fill comes back via the live websocket — VALIDATION.md §Manual-Only row 2.

5. **Reminders for downstream phases:**
   - **P2 (OrderGuard).** `execute_proposal`'s `broker = _build_broker(user_id); result = await broker.place_order(req)` call site becomes `result = await guard.place_order(req)` with no other code changes. The OrderRequest construction, market-hours guard, audit events, and state-machine transitions all happen UPSTREAM of where the OrderGuard wraps. P2 just needs to inject the guard into `_build_broker`.
   - **P3 (HITL UX hardening).** Two things:
     - `edit_size` and `escalate_to_dashboard` stubs become real handlers. Same delegation pattern via `slack.interactivity`; the workflow logic moves out of the stubs.
     - Add `idempotency_key` column to the `proposals` table + rewire the Approve handler to upsert by it instead of relying on at-least-once delivery tolerance. SKELETON.md T-01-08-05 disposition flips from "accept" to "mitigate".
   - **P7 (schedule-aware scheduling).** The Executor's after-hours `error` event with context `executor.market_closed` becomes the **deferral marker** the scheduler watches for. Instead of transitioning the proposal to FAILED, P7's scheduler-aware variant transitions to PENDING_DEFERRED and re-fires `execute_proposal` at the next market open. State machine gets a new (`PENDING_DEFERRED`, `APPROVED`) edge OR a new `(APPROVED, PENDING_DEFERRED)` transition; existing handlers don't need to know about it.

## Performance

- **Duration:** ~110 min (Tasks 1+2 from the prior session were already committed; Task 3 RED+GREEN + Task 4 RED+GREEN + SUMMARY/STATE in this session)
- **Tasks:** 4 of 4 complete (T1 Block Kit card + mrkdwn-escape hardening; T2 market-hours guard; T3 proposals state machine + Slack handlers; T4 Executor + fill callback + integration test)
- **Files created:** 14 (9 src, 5 tests)
- **Files modified:** 2 (pyproject.toml + uv.lock — aiohttp pin)
- **Tests added:** 53 (19 Block Kit incl. 2 mrkdwn-escape + 9 market-hours + 15 approval-proposals + 9 executor + 1 integration chain)
- **Total Plan 01-08 commits:** 9 (`6bb7fb6` Task 1 RED → `8e4dce5` Task 1 GREEN → `4790225` mrkdwn-escape hardening → `8da5605` Task 2 RED → `a5e468a` Task 2 GREEN → `b1e8a76` Task 3 RED → `9a5cf26` Task 3 GREEN → `c608532` Task 4 RED → `913b960` Task 4 GREEN)

## Files Created (14)

### Source layer (9)

- `src/gekko/reporter/slack.py` — `build_proposal_card`, `build_no_action_message`, `build_fill_confirmation`, and the `_escape_mrkdwn` defense.
- `src/gekko/reporter/templates.py` — REG-01 disclosure constant + UNKNOWN_FIELD_PLACEHOLDER.
- `src/gekko/execution/market_hours.py` — `is_market_open`, `next_market_open`, lru-cached `_nyse_calendar` (EXEC-10).
- `src/gekko/execution/executor.py` — `execute_proposal`, `on_fill_event`, plus the three module-level test seams (`_get_session_factory`, `_build_broker`, `_send_slack_dm`).
- `src/gekko/approval/proposals.py` — `STATE_TRANSITIONS` table, `transition_status` primitive, `approve_proposal`, `reject_proposal`.
- `src/gekko/approval/slack_handler.py` — `handle_approve`, `handle_reject`, `handle_edit_size_stub`, `handle_escalate_stub` + the `_get_session_factory` / `execute_proposal` test seams.
- `src/gekko/slack/app.py` — `slack_app` (`AsyncApp`) + `slack_handler` (`AsyncSlackRequestHandler`) module-level singletons.
- `src/gekko/slack/commands.py` — `handle_gekko_command` (/gekko run slash command).
- `src/gekko/slack/interactivity.py` — `@slack_app.action(...)` and `@slack_app.command(...)` registrations.

### Tests (5)

- `tests/unit/test_slack_block_kit.py` — 19 tests covering HITL-01 field completeness (incl. populated + None branches for company_name / sector), all four buttons + action_ids, REG-01 disclosure presence, build_no_action_message D-09 verbose shape, build_fill_confirmation single-line text, AND 2 prompt-injection escape regression tests (rationale / evidence / alternatives metacharacters + NoAction factors).
- `tests/unit/test_market_hours.py` — 9 tests covering RTH inside-window, before-open, after-close, weekend, July 4 holiday, Black Friday half-day, next_market_open, tz-naive-as-UTC, tzdata availability.
- `tests/unit/test_approval_proposals.py` — 15 tests covering state machine (PENDING -> APPROVED, idempotent same-state, invalid backward move, STATE_TRANSITIONS table completeness, approve+event, reject+event), slash command (run with name → trigger, run without name → usage, empty → help, ack-first), action handlers (approve invokes executor, reject does not, edit-size stub DMs Phase 3, escalate stub DMs Phase 3, cross-user refused).
- `tests/unit/test_executor.py` — 9 tests covering happy path, market closed → FAILED + error event, status != APPROVED → ValueError, BrokerOrderError → FAILED + error event + DM, duplicate client_order_id (broker dedup) → success, on_fill_event → FILLED + fill event + DM, Decimal normalization (Pitfall 6: '5.00' collapses to '5'), order_submitted payload conforms to OrderSubmittedEventPayload, and the architectural grep gate.
- `tests/integration/test_slack_approval_to_executor.py` — full HITL chain integration test. Seeds a User + Strategy + PENDING Proposal + the seed `proposal` event; clicks Approve; drains all background create_task tasks; manually fires on_fill_event; asserts the audit chain is exactly `[proposal, approval, order_submitted, fill]` in order; walks the SHA-256 chain via `audit.verify.walk_chain` — expects no broken rows; checks both Slack DMs landed (approved + filled).

## Files Modified (2)

- `pyproject.toml` + `uv.lock` — pinned `aiohttp>=3.9` (slack-bolt's AsyncApp imports it at module-load even when only the FastAPI adapter is in use).

## Verification

- `uv run pytest tests/unit/test_slack_block_kit.py tests/unit/test_market_hours.py tests/unit/test_approval_proposals.py tests/unit/test_executor.py -q --no-header` → 52 passed.
- `uv run pytest tests/integration/test_slack_approval_to_executor.py -q -m integration --no-header` → 1 passed.
- `uv run pytest tests/unit tests/integration -m "integration or not integration" -q --no-header` (whole-suite regression) → **336 passed, 4 skipped** in ~65 seconds. No pre-existing tests regressed.
- Grep gate: `grep -r "claude_agent_sdk" src/gekko/execution/` returns empty.

## Reminders Carried Forward

- **`_GET_PASSPHRASE()` indirection is still open.** Plan 01-09 must populate `gekko.agent.runtime._PASSPHRASE_CACHE` via `runtime.set_passphrase(...)` BEFORE any FastAPI route serves a Slack request (otherwise both `_approve_workflow` and `execute_proposal` raise `RuntimeError`).
- **AlpacaFillStream wiring lives in Plan 01-09's lifespan.** Construction: `AlpacaFillStream(api_key=..., secret_key=..., user_id=settings.gekko_user_id, on_fill=on_fill_event)`; call `stream.start()` during startup; `await stream.stop()` on shutdown.
- **Manual verification of HITL-01 + BROK-A-06 deferred to Plan 01-09.** The Plan 01-09 manual smoke is when the full chain runs against a real Slack workspace + real paper Alpaca endpoint.
- **Audit log's per-user `_append_locks` should be lazy-per-loop, not lazy-per-user.** The integration test currently clears `_append_locks` at the start because stale `asyncio.Lock` instances from a prior pytest-asyncio loop can wedge `append_event`. This is a side-band fix — a future audit-log hardening (P3 or P4) should make the locks lazy-per-loop so the workaround isn't needed.
