# Phase 3: Production HITL UX (Slack Block Kit + Dashboard Fallback) — Pattern Map

**Mapped:** 2026-06-17
**Phase directory:** `.planning/phases/03-production-hitl-ux-slack-block-kit-dashboard-fallback/`
**Files analyzed:** 38 (12 NEW modules, 13 EXTENDED modules, 6 NEW templates, 7 EXTENDED templates/CSS, ~30 new test files referenced in VALIDATION.md)
**Analogs found:** 36 / 38 (95%) — 2 files have NO Phase-1/Phase-2 analog (slack-bolt async modal `views_open` + FastAPI `SessionMiddleware` cookie auth)

---

## 1. File Classification Table

One row per NEW or EXTENDED file Phase 3 will touch. `read_first` names the single Phase-1/Phase-2 file the executor MUST read before authoring the new module so its idioms (imports, type hints, `_get_session_factory` shim shape, structlog redaction, identity-split routing, audit-event vocabulary, state-machine extension pattern, Alembic batch_alter_table conventions) match.

### 1a. NEW source modules

| New file | Role | Data flow | Closest analog | Match quality | Why this analog | `read_first` |
|---|---|---|---|---|---|---|
| `src/gekko/approval/dedup.py` | service (claim-action / UNIQUE INSERT) | request-response | `src/gekko/audit/log.py:88-167` (`append_event` — per-user lock + flush + IntegrityError surface) PLUS `src/gekko/agent/proposal_writer.py:290-329` (idempotent INSERT with `IntegrityError` catch + rebuild-from-existing) | role-match | Two-analog stack: `append_event`'s per-user `asyncio.Lock` + flush shape gives the concurrency idiom; `proposal_writer`'s `try: await session.flush() except IntegrityError:` block at lines 326-329 is the literal load-bearing pattern Phase 3's `claim_action` mirrors. The UNIQUE-clash → return `"duplicate"` is the inversion of "rebuild from existing row" — same exception, different branch. | `src/gekko/agent/proposal_writer.py` + `src/gekko/audit/log.py` |
| `src/gekko/approval/quiet_hours.py` | service (`_resolve_quiet_hours(user_id, now) → bool`) | request-response | `src/gekko/scheduler/jobs.py:65-104` (`_parse_schedule_time` — IANA tz + `zoneinfo.ZoneInfo` + clear `ValueError` on unknown tz) + `src/gekko/schemas/strategy.py:63-96` (`_validate_schedule_time` field-validator) | EXACT | Both files already use the exact `zoneinfo.ZoneInfo` + `ZoneInfoNotFoundError` shape Phase 3 needs (D-49 IANA validation). DST handling is implicit in `zoneinfo`. The strategy-wins / fall-to-user precedence (D-47) is a new policy layer, but the underlying `ZoneInfo`-resolution + tz-conversion arithmetic is verbatim from these two files. | `src/gekko/scheduler/jobs.py` + `src/gekko/schemas/strategy.py` |
| `src/gekko/approval/expiry.py` | service (sweep + state transition + audit event + chat.update + DM) | event-driven | `src/gekko/execution/executor.py:188-326` (the `executor.market_closed` branch is the literal TEMPLATE for the sweep's per-row work) + `src/gekko/execution/kill_switch.py:145-300` (DB-first + parallel-cleanup + Slack-DM-outside-transaction ordering invariant) | EXACT | The market-closed branch in executor.py is identical in shape to the sweep's per-row work: load row → log warning → open transaction → `append_event(event_type="expiration", payload=normalize_decimals({...}))` → `transition_status(PENDING → EXPIRED)` → commit → DM outside transaction. The kill_switch ordering invariant (DB COMMIT first, side-effects after) maps directly to the sweep's "transition first, chat.update + DM after" pattern. | `src/gekko/execution/executor.py` + `src/gekko/execution/kill_switch.py` |
| `src/gekko/reporter/daily_pnl.py` | service (audit-log query + Block Kit digest + DM) | batch / request-response | `src/gekko/reporter/slack.py:198-323` (`build_proposal_card` — Block Kit list-of-dicts builder with `_escape_mrkdwn` discipline) + `src/gekko/execution/executor.py:334-432` (`on_fill_event` — audit-log SELECT + Slack DM, payload-parse via `json.loads(row.payload_json)`) | role-match | The Block Kit builder pattern (header → section → context → actions) is verbatim from `build_proposal_card`. The audit-log query pattern (SELECT `Event` rows for today, parse `payload_json`, aggregate) is verbatim from `on_fill_event`'s walk in `executor.py:334-432`. The market-closed-day gate via `pandas_market_calendars.get_calendar("NYSE").schedule(...)` is NEW (D-59) — no Phase 1/2 analog uses pandas_market_calendars for an entry gate, but the library is already in tree from P1's `is_market_open`. | `src/gekko/reporter/slack.py` + `src/gekko/execution/executor.py` |
| `src/gekko/scheduler/jobs.py` (EXTENDED — adds two new `register_*` registrars) | service (APScheduler job registration) | event-driven | SAME FILE `src/gekko/scheduler/jobs.py:107-154` (`schedule_strategy_daily`) | EXACT | Pure extension — adds `register_expire_stale_sweep(scheduler, user_id)` and `register_daily_pnl_cron(scheduler, user_id)`. The `IntervalTrigger(seconds=60)` and `CronTrigger(hour=16, minute=30, timezone=ZoneInfo("America/New_York"))` body shapes mirror `schedule_strategy_daily`'s `add_job(...)` exactly. Use the same `"module:fn"` string-ref (CRITICAL per Plan 01-09 SUMMARY — `SQLAlchemyJobStore` pickles jobs across restart, bound function refs are fragile). Add `coalesce=True`, `max_instances=1`, `misfire_grace_time=300` per RESEARCH §HITL-03. | `src/gekko/scheduler/jobs.py` (itself) |

### 1b. EXTENDED source modules

| File | Location to modify | Pattern to follow (existing code) | Phase-1/Phase-2 line range |
|---|---|---|---|
| `src/gekko/approval/proposals.py` | Extend `STATE_TRANSITIONS` frozenset at lines 80-101 with ONE new edge: `("PENDING", "EXPIRED")`. The frozenset already carries `("AWAITING_2ND_CHANNEL", "EXPIRED")` (line 98) reserved by P2 — P3 adds the sibling PENDING-side edge. Optionally add an `expire_proposal(session, proposal_id, *, reason)` convenience helper mirroring `approve_proposal` (lines 168-206) / `reject_proposal` (lines 209-237) so the sweep + dashboard manual-expire paths share one entry point. | (a) The frozenset literal at lines 80-101 — append the new edge inside the braces; keep the comment style ("Phase 3 — Plan 03-XX Task X: ..."). (b) `transition_status` body at lines 109-160 is unchanged — data-driven. (c) The convenience-wrapper shape at lines 168-206 (`approve_proposal`) is the template: transition + append `expiration` event with payload `{proposal_id, reason, configured_timeout_minutes, expired_at}` per D-50. | 80-101 (frozenset); 168-237 (wrapper pattern) |
| `src/gekko/approval/slack_handler.py` | (1) Replace `handle_edit_size_stub` at lines 414-431 with full modal flow per D-54: rename to `handle_edit_size`, call `client.views_open(trigger_id=body["trigger_id"], view={...})` with the modal payload per RESEARCH §3 Pattern 3 + UI-SPEC §"Surface 1". (2) At the TOP of `_approve_workflow` (line 122, RIGHT after the cross-user check at line 141), call `await claim_action(session, proposal_id=decision_id, action_id="approve_proposal", actor_slack_user_id=slack_user_id, actor_gekko_user_id=gekko_user_id, source="slack", slack_trigger_id=body.get("trigger_id"))` — if `"duplicate"` returned, fire the ephemeral via `respond_url` (D-43) and return. Mirror in `_reject_workflow` (line 349) with `action_id="reject_proposal"`. (3) Replace `handle_escalate_stub` at lines 434-452 — D-60 converts this to a URL button in `build_proposal_card`, so the Bolt action handler is no longer needed. Either DELETE or convert to a no-op + deprecation log warning. | (a) The cross-user-check + identity-split DM pattern at lines 141-161 (`_send_slack_dm(gekko_user_id, ...)`) is universal — every new dup-click ephemeral must route through it. (b) The `is_live_first` branch pattern at lines 216-244 (`transition_status(... AWAITING_2ND_CHANNEL)` + `append_event(... awaiting_2nd_channel=True)`) is the model for the edit-size success path: `transition_status(PENDING → APPROVED)` + `append_event(event_type="edit_size", payload=normalize_decimals({old_qty, new_qty, old_notional, new_notional, drift_pct}))`. (c) The `payload_json_snapshot` pattern at lines 177-201 (snapshot inside transaction, parse OUTSIDE) is the template for the `private_metadata` payload — pre-load `ref_price` + `target_notional_usd` + `original_qty` + `ticker` before `views_open`. | 122-326 (approve workflow); 333-406 (reject workflow); 409-452 (edit-size + escalate stubs) |
| `src/gekko/slack/interactivity.py` | (1) Remove (or repurpose) the `escalate_to_dashboard` action registration at lines 53-56 (D-60 — URL button now). (2) Add a NEW view-submission listener: `@slack_app.view("edit_size_modal")` → delegate to `handle_edit_size_submit` (the new view_submission handler in `slack_handler.py`). The shape mirrors the existing `@slack_app.action(...)` wrappers at lines 35-56. | The four existing `@slack_app.action("...")` wrappers at lines 35-56 are the model — `async def _name(ack, body, client): await handler(ack=ack, body=body, client=client)`. The view-submission variant uses `async def _view(ack, body, client, view): await handler(ack=ack, body=body, client=client, view=view)` — same shape, extra `view` kwarg per slack-bolt's view_submission contract. | 29-56 |
| `src/gekko/slack/commands.py` | NO CHANGE for the kill/unkill commands (already wired by P2). If P3 adds `gekko serve` lifespan helpers for the new APScheduler jobs (`expire_stale_sweep` registration is by P3-NEW `register_expire_stale_sweep`), they live in `gekko.dashboard.app.lifespan`, NOT here. Only this file's job is the slash-command parser. | n/a — no extension required for P3. | n/a |
| `src/gekko/dashboard/app.py` | (1) Add `SessionMiddleware` registration in `create_app()` BEFORE `app.include_router(router)` at line 214 — `app.add_middleware(SessionMiddleware, secret_key=os.urandom(32).hex(), max_age=8*3600, https_only=False, same_site="strict", session_cookie="gekko_session")`. D-58: ephemeral per-restart secret. (2) In `lifespan` AFTER the existing kill-active boot check (line 132) and AFTER `app.state.scheduler.start()` (line 136), add two new job registrations alongside the existing P1 jobs: `register_expire_stale_sweep(app.state.scheduler, user_id=user_id)` (D-50) and `register_daily_pnl_cron(app.state.scheduler, user_id=user_id)` (D-48 / E). Both use the SAME P1 scheduler instance — do NOT create a new scheduler. | (a) The boot-time kill_active SELECT pattern at lines 92-132 is the model for any new boot-time check (P3 has none — the dedup table is queried per-handler, not at boot). (b) The scheduler-build-and-start sequence at lines 134-136 is where new `register_*` calls slot in — AFTER `scheduler.start()` returns (the scheduler must be running before jobs are added). (c) The `app.middleware("http")` decorator pattern at lines 233-245 — Phase 3's SessionMiddleware is NOT this shape (`add_middleware` registers Starlette-style middleware classes); the inline `@app.middleware("http")` decorator stays for the banner_mode + kill_active state injection. | 55-202 (lifespan); 205-247 (create_app + middleware) |
| `src/gekko/dashboard/routes.py` | (1) Add 8 new routes: `GET /login` (passphrase prompt), `POST /login` (passphrase verify + mint session), `GET /approvals` (HTMX index of PENDING proposals), `POST /approvals/{id}/approve`, `POST /approvals/{id}/reject`, `GET /approvals/{id}/edit-size` (HTMX modal fragment), `POST /approvals/{id}/edit-submit` (server-validates drift; on pass: dedup + state-transition + dispatch; on fail: re-render modal with error block), `GET /settings`, `POST /settings`. (2) Each handler uses the `claim_action` dedup gate with `source="dashboard"` per D-56. (3) The approve/reject handlers reuse the SAME server logic as Slack — extract into a private `_approve_logic(...)` / `_reject_logic(...)` helper called by both surfaces. | (a) The form-based POST handler shape at lines 359-399 (`kill_endpoint`) is the universal template: `confirm: str = Form(...)`, server-side validation, `asyncio.create_task(_background(...))`, return `templates.TemplateResponse(...)`. (b) The HTMX partial-return pattern at lines 539-541 (`return HTMLResponse('<span class="chip-live">LIVE — eligible</span>')`) is the model for inline-swap responses. (c) The `live_confirm_post` route at lines 622-744 is the closest existing analog for the new `/approvals/{id}/edit-submit` route — same shape: open transaction → re-read row inside transaction → check status (state-machine idempotency layer) → transition → append_event → commit → background dispatch via `asyncio.create_task`. (d) REG-04 scoping via `settings.gekko_user_id` at lines 64, 156, 224, 380 is universal. (e) The session-cookie write on successful `/login` POST: `request.session["gekko_user_id"] = settings.gekko_user_id; request.session["authenticated"] = True; return RedirectResponse(url=next_url, status_code=303)`. (f) The auth dependency: a new `async def require_session(request: Request) -> str:` that raises `HTTPException(status_code=302, ...)` redirecting to `/login?next={current_path}` when `request.session.get("authenticated")` is falsey. | 50-292 (P1 + P2 routes — all shape models); 539-744 (live-confirm — closest analog for `/approvals/{id}/edit-submit`) |
| `src/gekko/execution/executor.py` | (1) `on_fill_event` at lines 334-432: wrap the routine paper-fill `_send_slack_dm` call (the one that fires AFTER the FILL transition at line 394) in `_send_slack_dm_respecting_quiet_hours(user_id, text, category="routine_fill")` — the wrapper consults `_resolve_quiet_hours` and either fires or defers per D-48. (2) Add `_send_slack_dm_respecting_quiet_hours(user_id, text, *, category)` next to the existing `_send_slack_dm` at line 188. Bypass categories per D-48 (kill state changes, executor errors, first-live fills) fire DIRECTLY through `_send_slack_dm`; non-bypass categories check `_resolve_quiet_hours` first. (3) NO CHANGE to `_build_broker` (lines 105-185), `place_order` call (line 237), `cap_rejection` branch (lines 188-220), or `_send_slack_dm` (line 188) — these are all P2-locked. | (a) `_send_slack_dm` at lines 188-216 is the identity-split-safe seam (quick-260612-nlv fix) — the new quiet-hours-aware wrapper MUST route through `_send_slack_dm` for the actual send; it must NEVER call `slack_app.client.chat_postMessage` directly. (b) The fill DM call at line ~410-420 (inside `on_fill_event`) is the call site that needs wrapping. (c) The pattern for the error / cap_rejection DMs at lines 454 (MarketClosed) and 654 (BrokerOrderError) — these are bypass-category DMs; they continue to call `_send_slack_dm` directly (P3 adds severity-tier emoji prefix per RESEARCH §REPT-01 — `⚠️` informational / `❌` error / `🚫` kill-state). | 188-244 (DM seam); 334-432 (on_fill_event — DM call site to wrap) |
| `src/gekko/reporter/slack.py` | (1) Modify `build_proposal_card` at lines 198-323: when `expired=True` kwarg, swap the actions block at lines ~286-315 for a context block with the EXPIRED status text per UI-SPEC §Surface 4. (2) Add `[EXPIRED]` chip rendering via a new section-block prepended when expired. (3) Add `[AWAITING 2ND CHANNEL]` chip rendering (P2-inherited state — P3 just adds the visual). (4) Replace the existing escalate button at lines ~286-315 with a URL button: `{"type": "button", "text": {"type": "plain_text", "text": "Open in dashboard"}, "url": f"{settings.dashboard_url}/approvals/{proposal_id}", "style": "primary"}` per D-60 (no `action_id` because URL buttons don't round-trip). | (a) The `_banner(account_mode)` helper at lines 121-129 is the model — Phase 3 adds NO new banner styles (UI-SPEC inherits P2 banners verbatim). (b) The `_escape_mrkdwn` universal route at lines 101-118 — every LLM-authored string goes through it; deterministic constants ("Expired at HH:MM — not executed", "EXPIRED", "AWAITING 2ND CHANNEL") pass through unwrapped (RESEARCH §5b). (c) The block-list-of-dicts return shape at lines 236-323 — Phase 3's expired branch returns a DIFFERENT list of blocks (the same first 5 blocks + a context block instead of actions). | 99-323 |
| `src/gekko/db/models.py` | (1) Add NEW `SlackActionDedup` class per D-45 — columns: `id` (autoincrement PK), `proposal_id` (FK to `proposals.proposal_id`), `action_id` (string), `actor_slack_user_id` (string, nullable), `actor_gekko_user_id` (string, FK to `users.user_id`), `source` (string, `"slack"`/`"dashboard"`/`"cli"`), `slack_trigger_id` (string, nullable), `inserted_at` (string ISO), `result` (string, `"first_write"`/`"duplicate"`). UNIQUE constraint on `(proposal_id, action_id, actor_slack_user_id)` AND `(proposal_id, action_id, actor_gekko_user_id, source)` per D-42 / D-56. (2) Extend `User` (lines 121-168): add `quiet_hours_start: Mapped[str | None]` (HH:MM:SS), `quiet_hours_end: Mapped[str | None]`, `timezone: Mapped[str | None]` (IANA, e.g. `"America/New_York"`). (3) Extend `Strategy` payload via `schemas/strategy.py` (NOT the DB row — strategy fields live in `payload_json`). Add the three new fields to the `Strategy` Pydantic schema. (4) Extend `Proposal` (lines 314-373): add `expires_at: Mapped[str | None]` (ISO UTC, grandfathered NULL per D-61), `slack_message_ts: Mapped[str | None]`, `slack_message_channel: Mapped[str | None]` (for chat.update of expired card per D-53). (5) Extend `_PROPOSAL_STATUSES` tuple (lines 53-62) with `"EXPIRED"` (Assumption A6). (6) Extend `_EVENT_TYPES` (lines 81-95) with `"expiration"`, `"dedup_click"`, `"edit_size"`, `"daily_pnl"`. | (a) The `StrategyMetadata` class at lines 220-261 is the closest analog for `SlackActionDedup` — composite-PK-or-UNIQUE-keyed per-(user, ...) row with audit-friendly columns. (b) The `_in_check` + `CheckConstraint` pattern at lines 98-100, 354-362 — extending `_PROPOSAL_STATUSES` + `_EVENT_TYPES` automatically threads through the CheckConstraint declarations. (c) The `__repr__` exclusion pattern at lines 164-168, 256-261, 365-373 — `SlackActionDedup.__repr__` excludes `slack_trigger_id` (mildly sensitive, retry-debugging only) but includes `proposal_id` + `action_id` + `source` + `result` (the audit-grep-able fields). (d) The `server_default=text("0")` pattern at lines 154-156 for boolean defaults; for nullable text columns just `nullable=True`. | 47-95 (CheckConstraint vocabularies); 121-168 (User); 220-261 (StrategyMetadata — closest analog for SlackActionDedup); 314-373 (Proposal) |
| `src/gekko/schemas/strategy.py` | Add 3 new optional fields to `Strategy` (lines 99-150): `quiet_hours_start: str | None = None` (HH:MM format — validated against `^\d{2}:\d{2}$`), `quiet_hours_end: str | None = None` (same shape), `proposal_timeout_minutes: int | None = None` (positive int via `Field(None, gt=0)`). NOTE: `Strategy.timezone` is NOT added per D-47 — strategy inherits user TZ. | (a) The `Field(...)` + `field_validator` pattern at lines 130-150 — Phase 3 may add a `_validate_quiet_hours_format` field_validator mirroring `_validate_schedule_time` (lines 63-96) for HH:MM regex. (b) The `Literal["paper", "live"]` mode field at line 125 — `quiet_hours_start` / `quiet_hours_end` use `str \| None` (no Literal). (c) `model_config = ConfigDict(frozen=False, extra="forbid")` at line 113 — Strategy is `extra="forbid"`, so the new fields MUST be declared on the model. | 99-150 |
| `src/gekko/agent/proposal_writer.py` | Stamp `expires_at` on the proposal row at the point where the TradeProposal is built and persisted (around line 220 where `tp = TradeProposal.model_validate(merged)` lands, and before the INSERT at line 304). Compute: `expires_at = (datetime.now(UTC) + timedelta(minutes=strategy.proposal_timeout_minutes or 30)).isoformat()`. Persist on `Proposal` row column (NEW per D-61). | (a) The `account_mode = "LIVE" if ... else "PAPER"` ternary at lines 207-211 is the model for `expires_at` computation. (b) The `now_iso = datetime.now(UTC).isoformat()` pattern at line 303 — same UTC ISO format for `expires_at`. (c) The proposal row INSERT at lines 304-325 is where the new `expires_at=expires_at` kwarg lands alongside the existing `account_mode=account_mode` (line 323). | 190-329 |

### 1c. NEW Jinja2 templates

| New file | Role | Data flow | Closest analog | Match quality | Why | `read_first` |
|---|---|---|---|---|---|---|
| `src/gekko/dashboard/templates/login.html` (or `.html.j2`) | Full-page form | request-response | `src/gekko/dashboard/templates/user_agreement.html.j2` (full-page extend-base + body content) + `src/gekko/dashboard/templates/first_live_confirm.html.j2` (form-with-error-state + `<input type="hidden" name="next">` pattern) | EXACT | `user_agreement.html.j2` is the closest existing "full-page text + minimal interactivity" template. `first_live_confirm.html.j2` (lines 60-93) shows the form pattern with hidden field + required inputs + cancel link. P3 login adds an error block (`<div class="login-error" role="alert" aria-live="assertive">`) and a single password input. CSP-clean (no inline `<script>`); UI-SPEC §Surface 3 declares the exact copy + classes. | `src/gekko/dashboard/templates/user_agreement.html.j2` + `src/gekko/dashboard/templates/first_live_confirm.html.j2` |
| `src/gekko/dashboard/templates/approvals_index.html` | Full-page HTMX index | request-response | `src/gekko/dashboard/templates/strategies_list.html.j2` (table iteration with `{% for s in strategies %}` + HTMX `hx-post` actions per row + empty-state branch) | EXACT | Both render a list of items with per-row HTMX actions. P3's `approvals_index.html` iterates `{% for p in proposals %}` and renders the shared `_proposal_card.html.j2` partial via `{% include "proposal_card.html.j2" %}`. The `live_mode_eligible` chip rendering at lines 26-31 of `strategies_list.html.j2` is the model for the `[LIVE]` / `[EXPIRED]` / `[AWAITING 2ND CHANNEL]` chips per UI-SPEC §Surface 2. The empty-state branch at lines 59-64 is the model for the P3 empty state (`🤖 No proposals pending — agent is waiting...`). | `src/gekko/dashboard/templates/strategies_list.html.j2` |
| `src/gekko/dashboard/templates/proposal_card.html` (SHARED partial — dashboard + Slack-render reference) | Reusable card partial | request-response | NO direct analog (`build_proposal_card` in `reporter/slack.py:198-323` is the Slack-side parallel — same schema, different transport). The closest Jinja2 partial is `src/gekko/dashboard/templates/kill_active_banner.html.j2` (small reusable block). | role-match | The HTML structure declared in UI-SPEC §Surface 2 is the contract (article + header + chips + rationale + evidence + actions). The `{% if status == "PENDING" %} ... {% elif status == "EXPIRED" %} ... {% elif status == "AWAITING_2ND_CHANNEL" %} ...` conditional branch shape mirrors `strategies_list.html.j2:46-53` (`{% if not s.live_mode_eligible %} ... {% endif %}`). HTMX `hx-post="/approvals/{{ proposal_id }}/approve" hx-target="closest article" hx-swap="outerHTML" hx-disable-elt="this"` is the load-bearing partial-self-replace pattern (UI-SPEC §Surface 2). The `aria-label` per UI-SPEC §Surface 2 ARIA section. | `src/gekko/dashboard/templates/strategies_list.html.j2` + `src/gekko/reporter/slack.py` |
| `src/gekko/dashboard/templates/edit_size_modal.html` | HTMX-loaded modal partial | request-response | `src/gekko/dashboard/templates/promote_to_live_modal.html.j2` (typed-confirm modal with `hx-post` form + Cancel link + modal-backdrop wrapper) + `src/gekko/dashboard/templates/kill_modal.html.j2` (modal with form + required input + Cancel → `/modal/close`) | EXACT | `promote_to_live_modal.html.j2` is the closest existing typed-input modal partial. P3's edit-size modal: same `.modal-backdrop > .modal > .modal-headline + .modal-body + form` shape; replaces the typed-name input with a `<input type="number" name="qty" step="0.01" min="0.01">` per UI-SPEC §Surface 1 (Slack parallel uses `number_input` block); adds a read-only drift-preview line (server-side computed and re-rendered on POST); error-state branch when `drift_pct > 0.02` re-renders the modal with a red `.login-error`-style block at the top per UI-SPEC §Surface 2 error states. | `src/gekko/dashboard/templates/promote_to_live_modal.html.j2` + `src/gekko/dashboard/templates/kill_modal.html.j2` |
| `src/gekko/dashboard/templates/expired_card.html` (or rendered via `proposal_card.html` with `status == "EXPIRED"`) | Inline status partial | request-response | `src/gekko/dashboard/templates/trigger_button.html.j2` (small partial returned by POST and swapped in by HTMX) | role-match | If the expired card is rendered as a sub-branch of `proposal_card.html.j2` (preferred per UI-SPEC §Surface 4 — single partial, conditional class), no separate file is needed. If rendered as a separate file, follow the 8-line shape of `trigger_button.html.j2`: minimal HTML, conditional content, no inline scripts. | `src/gekko/dashboard/templates/trigger_button.html.j2` |
| `src/gekko/dashboard/templates/settings.html` | Full-page form | request-response | `src/gekko/dashboard/templates/strategy_edit.html.j2` (full-page form with multiple input groups + Save button + Cancel link) | EXACT | `strategy_edit.html.j2` is the model — same `{% extends "base.html.j2" %}` + `<form method="post" action="/settings">` shape. Replaces the strategy fields with: `<select name="timezone">` populated from `iana_timezones` (passed in context — `list(zoneinfo.available_timezones())`); two `<input type="time">` inputs for quiet_hours_start / quiet_hours_end. Success state renders a `<div role="status">Settings saved.</div>` at top per UI-SPEC §Surface 5. Validation-error state renders a `.login-error`-style block with the specific failure message. | `src/gekko/dashboard/templates/strategy_edit.html.j2` |

### 1d. NEW Alembic migration

| New file | Role | Data flow | Closest analog | Match quality | Why | `read_first` |
|---|---|---|---|---|---|---|
| `migrations/versions/0004_p3_hitl_ux.py` | Alembic schema migration | CRUD | `migrations/versions/0002_orderguard.py` (Phase 2 — adds tables + columns + CHECK constraint replacement via `batch_alter_table`) + `migrations/versions/0003_event_types_phase2.py` (Phase 2 — extends `ck_event_type` CHECK via `drop_constraint` + `create_check_constraint` inside `batch_alter_table`) | EXACT | Both Phase 2 migrations are the literal templates. `0002_orderguard.py` shape for new tables + columns + composite-PK changes (the `_FROZEN_*` local vocabularies + `_in_check` + the `batch_alter_table` walk-throughs at lines 76+). `0003_event_types_phase2.py` shape for CHECK-constraint extension (`_FROZEN_EVENT_TYPES` + `_FROZEN_EVENT_TYPES_PRE` + `bop.drop_constraint("ck_event_type", type_="check") + bop.create_check_constraint("ck_event_type", _in_check(...))`). P3 needs BOTH patterns — new `slack_action_dedup` table (0002 shape) + extended `ck_proposal_status` (adds "EXPIRED") + extended `ck_event_type` (adds 4 new types) (0003 shape) + new columns on `User` + `Proposal` (0002 shape `add_column`). | `migrations/versions/0002_orderguard.py` + `migrations/versions/0003_event_types_phase2.py` |

### 1e. NEW test files (from VALIDATION.md — ~30 tests)

| New file (representative) | Role | Data flow | Closest analog | Match quality | Why | `read_first` |
|---|---|---|---|---|---|---|
| `tests/unit/test_dedup.py` | Unit test for `claim_action` first-write/duplicate semantics | request-response | `tests/unit/test_proposal_writer.py` (Plan 01-07 — 11 ProposalWriter behaviors including `IntegrityError`-on-second-write idempotency) | EXACT | Same shape: open in-memory engine, seed proposal row, call `claim_action(session, proposal_id=..., action_id="approve_proposal", ...)` twice with the same kwargs, assert first returns `"first_write"` and second returns `"duplicate"` without raising. Assert a `dedup_click` event lands in the audit log on the duplicate. | `tests/unit/test_proposal_writer.py` |
| `tests/unit/test_quiet_hours.py` | Unit test for `_resolve_quiet_hours` precedence + DST | request-response | `tests/unit/test_market_hours.py` (Plan 01-08 — 9 tests of `is_market_open` covering DST + tz arithmetic) | EXACT | Same "binary block decision read from a structured external source" shape. Tests cover: (a) no quiet hours configured → False; (b) user-level only → in-window True; (c) strategy override → narrower-than-user warns but evaluates per-strategy; (d) DST spring-forward 23h-day boundary; (e) DST fall-back 25h-day boundary; (f) overnight window wrap (start > end). | `tests/unit/test_market_hours.py` |
| `tests/unit/test_expire_stale_proposals.py` | Unit test for sweep | event-driven | `tests/unit/test_executor.py` (Plan 01-08 — `test_market_closed_path` is the closest sibling because it tests the EXACT same "branch into FAILED + audit event + DM" shape the sweep mirrors) | EXACT | Seed PENDING proposal with `expires_at <= now()`; call `expire_stale_proposals(user_id=...)`; assert: status flipped to EXPIRED, `expiration` event in audit log with payload `{reason="timeout", expired_at, configured_timeout_minutes}`, `chat.update` called (mocked) with expired-card blocks, separate `_send_slack_dm` called (mocked) with the D-53 DM copy. Test sweep IDEMPOTENCY by running twice — second call should be a no-op. Test grandfathering: PENDING proposal with `expires_at IS NULL` is NOT swept per D-61. | `tests/unit/test_executor.py` |
| `tests/unit/test_edit_size_drift.py` | Unit test for the 2% drift gate inside `handle_edit_size_submit` | request-response | `tests/unit/test_orderguard.py` (Plan 02-02 — covers `check_qty_price_drift` 2% bound) + `tests/unit/test_proposal_schema.py` (Plan 01-06 — TradeProposal field validation) | EXACT | The drift math (`abs(new_qty × ref_price - target_notional_usd) / target_notional_usd`) is the EXACT same arithmetic OrderGuard's `_qty_price.py` runs. Test cases: (a) drift = 0% → pass; (b) drift = 1.99% → pass; (c) drift = 2.01% → fail (modal re-rendered with error, NO state change, NO audit event); (d) submitting same modal twice with the same qty → dedup table catches second submit. | `tests/unit/test_orderguard.py` |
| `tests/unit/test_dashboard_session_auth.py` | Unit test for `SessionMiddleware` + `require_session` dep | request-response | `tests/integration/test_dashboard_strategy_edit.py` (Plan 01-09 — uses `httpx.ASGITransport` against `create_app()`) | role-match | Same `httpx.ASGITransport(app=create_app())` pattern. Test cases: (a) GET `/approvals` without cookie → 302 redirect to `/login?next=/approvals`; (b) POST `/login` with wrong passphrase → form re-renders with `.login-error`; (c) POST `/login` with correct passphrase → 303 redirect + Set-Cookie header containing signed session; (d) GET `/approvals` with valid cookie → 200 + rendered index; (e) restart app → cookie no longer valid (D-58 ephemeral secret). | `tests/integration/test_dashboard_strategy_edit.py` |
| `tests/unit/test_daily_pnl_digest.py` | Unit test for digest builder + market-closed-day gate | batch | `tests/unit/test_reporter_slack.py` (Plan 01-08 — `build_proposal_card` block-list assertions) + `tests/unit/test_market_hours.py` (NYSE calendar) | role-match | Test cases: (a) digest renders header + per-strategy + counts + footer button blocks; (b) zero fills today → renders "_no fills today_" branch; (c) `pandas_market_calendars.get_calendar("NYSE").schedule(...)` empty (weekend/holiday) → handler returns early, no DM sent; (d) gross P&L sign drives `📈` vs `📉` glyph; (e) USD format string `${value:+,.2f}` produces the expected output. | `tests/unit/test_reporter_slack.py` |
| `tests/unit/test_proposal_state_machine_expired.py` | Unit test for new `PENDING → EXPIRED` edge | request-response | `tests/unit/test_approval_proposals.py` (Plan 01-08 — `STATE_TRANSITIONS` completeness; 15 tests) | EXACT | Extends the existing frozenset-completeness test with the new edge. Also tests idempotency: `transition_status(PENDING → EXPIRED)` is a no-op when row already EXPIRED. Tests the convenience wrapper `expire_proposal(...)` if added. | `tests/unit/test_approval_proposals.py` |
| `tests/unit/test_slack_modal_views_open.py` | Unit test for `views_open` payload shape | request-response | NO direct analog — first modal in tree | NO ANALOG | Lean on RESEARCH §3 Pattern 3 verbatim. Mock `client.views_open` via `unittest.mock.AsyncMock`; call `handle_edit_size(ack, body, client)`; assert `client.views_open` was called once with `trigger_id=body["trigger_id"]` and a `view` dict whose `callback_id == "edit_size_modal"` and whose `private_metadata` is a JSON string carrying `decision_id`, `ref_price`, `target_notional_usd`, `original_qty`, `ticker`, `response_url`. Use the source-bytes idiom from `tests/unit/test_executor.py::test_executor_module_does_not_import_claude_agent_sdk` for the no-SDK grep gate. | RESEARCH §3 Pattern 3 + `tests/unit/test_executor.py` |
| `tests/integration/test_p3_walking_skeleton.py` (or similar end-to-end) | Full chain integration | event-driven | `tests/integration/test_slack_approval_to_executor.py` (Plan 01-08 — full HITL chain with monkeypatched `asyncio.create_task` drain) | EXACT | Same shape but expanded chain: `[proposal (with expires_at), slack_action_dedup INSERT, approval, order_submitted, fill, daily_pnl audit event]`. Also test the EXPIRY chain end-to-end: PENDING proposal → 60s sweep → EXPIRED + chat.update mocked + DM mocked. | `tests/integration/test_slack_approval_to_executor.py` |
| `tests/unit/test_no_claude_sdk_in_p3_modules.py` | AST grep gate | n/a (static) | `tests/unit/test_executor.py::test_executor_module_does_not_import_claude_agent_sdk` (Plan 01-08 — reads source bytes; asserts SDK package name absent) | EXACT | Extend the existing gate's file list to include: `gekko/approval/dedup.py`, `gekko/approval/quiet_hours.py`, `gekko/approval/expiry.py`, `gekko/reporter/daily_pnl.py`, `gekko/scheduler/jobs.py` (additions). The dedup + quiet-hours + expiry modules are deterministic Python; the LLM bytes never reach them. | `tests/unit/test_executor.py` |

---

## 2. Idiom Inventory — Concrete Code Excerpts

### 2a. State-machine extension — frozenset literal + idempotent transition

**Source:** `src/gekko/approval/proposals.py:80-160`

```python
STATE_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        # Phase 1 — Plan 01-08
        ("PENDING", "APPROVED"),
        ("PENDING", "REJECTED"),
        ("APPROVED", "EXECUTING"),
        ("APPROVED", "FAILED"),
        ("EXECUTING", "FILLED"),
        ("EXECUTING", "FAILED"),
        # Phase 2 — dual-channel gate edges
        ("PENDING", "AWAITING_2ND_CHANNEL"),
        ("AWAITING_2ND_CHANNEL", "APPROVED_LIVE"),
        ("AWAITING_2ND_CHANNEL", "REJECTED"),
        ("AWAITING_2ND_CHANNEL", "EXPIRED"),  # forward-prep for P3
        ("APPROVED_LIVE", "EXECUTING"),
    }
)
```

**Phase 3 application:** Append `("PENDING", "EXPIRED")` to the frozenset literal. The `AWAITING_2ND_CHANNEL → EXPIRED` edge was already added in P2 as forward-prep; P3 adds the sibling. The `transition_status` body (lines 109-160) is DATA-DRIVEN on the frozenset — DO NOT modify. The idempotent same-state return at lines 139-141 is the load-bearing invariant that makes the sweep + click-race safe (D-53 edge case).

### 2b. UNIQUE-INSERT idempotent persistence with `IntegrityError`

**Source:** `src/gekko/agent/proposal_writer.py:290-329` (the load-bearing template for the new `claim_action`)

```python
# 4. Idempotent persistence — return existing row if it exists.
existing_row = (
    await session.execute(
        select(ProposalRow).where(ProposalRow.proposal_id == decision_id)
    )
).scalar_one_or_none()

if existing_row is not None:
    # Rebuild the TradeProposal from the persisted JSON to honor
    # idempotency: the second caller returns the SAME proposal the
    # first caller wrote, not a freshly constructed twin.
    return TradeProposal.model_validate_json(existing_row.payload_json)

now_iso = datetime.now(UTC).isoformat()
session.add(
    ProposalRow(
        proposal_id=decision_id,
        # ... other columns ...
        account_mode=account_mode,
    )
)
try:
    await session.flush()
except IntegrityError:
    # Concurrent insert race: another writer beat us to the same
    # primary key. Re-query and return the winner's row.
    ...
```

**Phase 3 application:** The new `claim_action(session, proposal_id, action_id, actor_slack_user_id, actor_gekko_user_id, source, slack_trigger_id)` helper mirrors this exactly — but INVERTS the branch: on `IntegrityError`, return `"duplicate"` (the caller takes the ephemeral-response path); on success, return `"first_write"`. Critical: the `try: await session.flush() except IntegrityError: await session.rollback()` shape is required because IntegrityError invalidates the transaction — the rollback is non-negotiable. Then write the `dedup_click` audit event in a FRESH transaction (D-45).

### 2c. Per-user `asyncio.Lock` registry (concurrency primitive)

**Source:** `src/gekko/audit/log.py:69-81`

```python
_append_locks: dict[str, asyncio.Lock] = {}
_registry_lock: asyncio.Lock = asyncio.Lock()


async def _lock_for(user_id: str) -> asyncio.Lock:
    """Return the per-user lock, creating it on first use."""
    # Fast path: lock already exists.
    lock = _append_locks.get(user_id)
    if lock is not None:
        return lock
    # Slow path: registry mutation under guard.
    async with _registry_lock:
        return _append_locks.setdefault(user_id, asyncio.Lock())
```

**Phase 3 application:** The dedup table INSERT does NOT need an asyncio.Lock layer — the UNIQUE constraint is the atomic primitive. But the daily P&L digest builder, if it walks the audit log inside an `async with lock`-style block, MUST follow the per-user lock pattern (so concurrent writers don't deadlock with the daily-pnl reader). The quiet-hours predicate is pure-function + DB-read; no lock needed.

### 2d. `_get_session_factory` test seam + finally-dispose

**Source:** `src/gekko/execution/executor.py:90-102` + `src/gekko/approval/slack_handler.py:70-88` + `src/gekko/execution/kill_switch.py:101-114`

```python
def _get_session_factory(
    user_id: str,
) -> tuple[AsyncSessionLocal, AsyncEngine | None]:
    """Build a session factory + owning engine for ``user_id``.

    Mirrors the same indirection used by :mod:`gekko.approval.slack_handler`
    so tests have a single seam to monkeypatch.
    """
    settings = get_settings()
    engine = get_async_engine(
        settings.db_path_for(user_id), _get_passphrase()
    )
    return make_session_factory(engine), engine
```

```python
# Caller-side usage:
sf, engine = _get_session_factory(user_id)
try:
    async with sf() as session, session.begin():
        # ... DB work ...
        ...
    # Side-effects OUTSIDE the transaction (Slack DM, chat.update)
finally:
    if engine is not None:
        await engine.dispose()
```

**Phase 3 application:** Every NEW DB-touching module (`approval/dedup.py`, `approval/quiet_hours.py`, `approval/expiry.py`, `reporter/daily_pnl.py`) MUST declare its own module-local `_get_session_factory` shim verbatim — patching one module's seam does NOT patch another's. The `if engine is not None: await engine.dispose()` finally block is the disposal contract — `None` means "test owns disposal".

### 2e. Identity-split-safe Slack DM seam

**Source:** `src/gekko/execution/executor.py:188-216`

```python
async def _send_slack_dm(user_id: str, text: str) -> None:
    """Send a Slack DM addressed to the configured operator.

    Identity-split: the ``user_id`` argument is the INTERNAL
    ``gekko_user_id`` (e.g. ``"chris"``) carried for caller-API
    stability + audit/log metadata. Slack's ``chat.postMessage``
    requires a Slack channel/user id (e.g. ``"U08LRFFRBS4"``), so this
    function reads :attr:`gekko.config.Settings.slack_user_id` and
    binds it to the ``channel=`` kwarg. Passing ``user_id`` to Slack
    directly produces ``SlackApiError(channel_not_found)``.
    """
    from gekko.slack.app import slack_app

    settings = get_settings()
    await slack_app.client.chat_postMessage(
        channel=settings.slack_user_id, text=text
    )
```

**Phase 3 application:** Every new DM path (sweep DM in `expiry.py`, daily P&L DM in `daily_pnl.py`, dedup ephemeral, edit-size success DM) MUST route through `_send_slack_dm` (text) or `_send_slack_dm_blocks` (Block Kit). The new `_send_slack_dm_respecting_quiet_hours(user_id, text, *, category)` wrapper consults `_resolve_quiet_hours(user_id, datetime.now(UTC))` and either calls `_send_slack_dm` directly (bypass categories per D-48) or defers/drops per D-48 routine categories. CRITICAL: PATTERNS §10 explicitly forbids a parallel DM path — single chokepoint, single audit point.

### 2f. APScheduler job registration with string-ref + restart-safe knobs

**Source:** `src/gekko/scheduler/jobs.py:107-154`

```python
def schedule_strategy_daily(
    scheduler: AsyncIOScheduler,
    *,
    user_id: str,
    strategy_name: str,
    schedule_time: str,
) -> str:
    hh, mm, tz = _parse_schedule_time(schedule_time)
    job_id = f"run-{user_id}-{strategy_name}"

    # trigger_strategy_run is imported by string id ("module:fn") because
    # APScheduler's SQLAlchemyJobStore pickles the job and re-loads it on
    # the new process during a restart — pickling a bound function ref
    # works in P1 but is fragile across refactors. The string ref is the
    # forward-compatible form.
    scheduler.add_job(
        "gekko.agent.runtime:trigger_strategy_run",
        CronTrigger(hour=hh, minute=mm, timezone=tz),
        kwargs={
            "user_id": user_id,
            "strategy_name": strategy_name,
            "source": "schedule",
        },
        id=job_id,
        replace_existing=True,
    )
    ...
```

**Phase 3 application:** Two new registrars in this file:

```python
def register_expire_stale_sweep(
    scheduler: AsyncIOScheduler, *, user_id: str
) -> str:
    job_id = f"expire-stale-{user_id}"
    scheduler.add_job(
        "gekko.approval.expiry:expire_stale_proposals",
        IntervalTrigger(seconds=60),
        kwargs={"user_id": user_id},
        id=job_id,
        replace_existing=True,
        coalesce=True,           # piled-up runs collapse to one
        max_instances=1,         # never overlap with self
        misfire_grace_time=300,  # 5min grace on missed fires
    )
    return job_id


def register_daily_pnl_cron(
    scheduler: AsyncIOScheduler, *, user_id: str
) -> str:
    job_id = f"daily-pnl-{user_id}"
    scheduler.add_job(
        "gekko.reporter.daily_pnl:send_daily_pnl_digest",
        CronTrigger(hour=16, minute=30, timezone=ZoneInfo("America/New_York")),
        kwargs={"user_id": user_id},
        id=job_id,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return job_id
```

Both use the SAME P1 scheduler instance (built in `dashboard.app.lifespan` line 135). DO NOT create a new scheduler — multiple AsyncIOScheduler instances against the same SQLite job-store will fight.

### 2g. Audit-event append with `normalize_decimals`

**Source:** `src/gekko/execution/executor.py:188-220` (the `executor.market_closed` branch — the TEMPLATE for `expiration`)

```python
if not is_market_open():
    log.warning(
        "executor.market_closed",
        proposal_id=proposal_id,
        ticker=tp.ticker,
    )
    async with sf() as session, session.begin():
        await append_event(
            session,
            user_id=user_id,
            strategy_id=strategy_id,
            event_type="error",
            payload=normalize_decimals(
                {
                    "context": "executor.market_closed",
                    "error_class": "MarketClosed",
                    "error_message": "...",
                    "proposal_id": proposal_id,
                    "ticker": tp.ticker,
                }
            ),
        )
        await transition_status(
            session,
            proposal_id,
            from_status="APPROVED",
            to_status="FAILED",
        )
    return
```

**Phase 3 application:** The sweep's per-row work is a NEAR-VERBATIM SIBLING:

```python
async with sf() as session, session.begin():
    await transition_status(
        session, row.proposal_id,
        from_status="PENDING", to_status="EXPIRED",
    )
    await append_event(
        session,
        user_id=user_id,
        strategy_id=row.strategy_id,
        event_type="expiration",
        payload=normalize_decimals({
            "proposal_id": row.proposal_id,
            "reason": "timeout",
            "expired_at": now_utc.isoformat(),
            "configured_timeout_minutes": _resolve_strategy_timeout(
                session, row.strategy_id
            ),
        }),
    )
```

`normalize_decimals(payload)` wraps the ENTIRE dict (Pitfall 6 — pre-existing invariant). Phase 3's edit-size event payload (`{old_qty, new_qty, old_notional, new_notional, drift_pct}`) carries Decimals — `normalize_decimals` is non-negotiable.

### 2h. zoneinfo-based timezone resolution with DST + Windows tzdata gate

**Source:** `src/gekko/scheduler/jobs.py:65-104` + `src/gekko/schemas/strategy.py:63-96`

```python
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    tz = ZoneInfo(tz_part)
except ZoneInfoNotFoundError as exc:
    msg = (
        f"Invalid IANA timezone {tz_part!r} in schedule_time. "
        "On Windows install the `tzdata` package."
    )
    raise ValueError(msg) from exc
```

**Phase 3 application:** `_resolve_quiet_hours(user_id, now)` uses the same shape:

```python
async def _resolve_quiet_hours(user_id: str, now: datetime, strategy_name: str | None = None) -> bool:
    """True iff `now` falls in the user's quiet-hours window (strategy override wins)."""
    # ... load user + (optionally) strategy from DB ...
    tz_name = user.timezone or "America/New_York"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        msg = f"Invalid IANA timezone {tz_name!r} for user {user_id}"
        raise ValueError(msg) from exc

    local_now = now.astimezone(tz)
    # ... compute strategy_window vs user_window precedence per D-47 ...
    # ... compare local_now.time() to window start/end, handling wrap (start > end) ...
```

DST is handled implicitly by `zoneinfo`. The timer (D-52 — 30min proposal expiry) lives in UTC; only the quiet-hours window comparison converts to user TZ.

### 2i. HTMX modal partial + `/modal/close` swap

**Source:** `src/gekko/dashboard/templates/promote_to_live_modal.html.j2` (full file, 47 lines)

```html
<div class="modal-backdrop">
  <div class="modal" role="dialog" aria-labelledby="promote-headline" aria-modal="true">
    <h2 id="promote-headline" class="modal-headline">Promote strategy to LIVE</h2>
    <div class="modal-body">
      <p>...</p>
      <form hx-post="/strategies/{{ name }}/promote-to-live"
            hx-target="#promote-result"
            hx-swap="outerHTML"
            class="mt-2">
        <input type="text" name="strategy_name_confirm" required autocomplete="off">
        <div class="modal-actions">
          <a href="#"
             hx-get="/modal/close"
             hx-target="#modal-mount"
             hx-swap="innerHTML">
            Cancel
          </a>
          <button type="submit" class="danger">Promote to LIVE</button>
        </div>
      </form>
      <div id="promote-result"></div>
    </div>
  </div>
</div>
```

**Phase 3 application:** `edit_size_modal.html.j2`:

```html
<div class="modal-backdrop">
  <div class="modal" role="dialog" aria-labelledby="edit-size-headline" aria-modal="true">
    <h2 id="edit-size-headline" class="modal-headline">Edit size — {{ ticker }} {{ side }}</h2>
    <div class="modal-body">
      {% if error %}
      <div class="login-error" role="alert" aria-live="assertive">
        ❌ Drift {{ drift_pct }}% exceeds the 2% safety bound. Target ${{ target_notional_usd }}; this qty = ${{ new_notional }}.
      </div>
      {% endif %}
      <form hx-post="/approvals/{{ proposal_id }}/edit-submit"
            hx-target="#modal-mount"
            hx-swap="innerHTML"
            class="mt-2">
        <label for="qty">New quantity</label>
        <input type="number" id="qty" name="qty" step="0.01" min="0.01" required value="{{ original_qty }}">
        <p class="text-sm">Ref price: ${{ ref_price }} · Target notional: ${{ target_notional_usd }}</p>
        <div class="modal-actions">
          <a hx-get="/modal/close" hx-target="#modal-mount" hx-swap="innerHTML">Cancel</a>
          <button type="submit" class="btn-primary">Approve at this size</button>
        </div>
      </form>
    </div>
  </div>
</div>
```

The `hx-target="#modal-mount" hx-swap="innerHTML"` against the Cancel link is the existing CSP-safe modal-close mechanism (route `GET /modal/close` returns empty HTML — see `dashboard/routes.py:347-356`). The drift-error re-render returns this SAME template with `error=True` + the computed values — HTMX swaps the modal in place, preserving the operator's typed qty.

### 2j. FastAPI form POST with server-side validation + HTMX partial return

**Source:** `src/gekko/dashboard/routes.py:359-399` (the kill_endpoint — universal handler template)

```python
@router.post("/kill", response_class=HTMLResponse)
async def kill_endpoint(
    request: Request, confirm: str = Form(...)
) -> HTMLResponse:
    # Case-sensitive gate
    if confirm.strip() != "KILL":
        raise HTTPException(
            status_code=400,
            detail="Type KILL exactly (uppercase) to confirm.",
        )

    settings = get_settings()
    asyncio.create_task(
        _execute_kill_background(
            user_id=settings.gekko_user_id, source="dashboard"
        )
    )
    # Flag the app-state cache as dirty so subsequent renders pick up
    # the new state without waiting for the 60s TTL to expire.
    try:
        request.app.state.kill_active = True
        request.state.kill_active = True
    except Exception:  # noqa: BLE001
        pass
    return templates.TemplateResponse(
        "kill_active_banner.html.j2",
        {"request": request, "n_cancelled": 0, "n_total": 0, "boot_restored": False},
    )
```

**Phase 3 application:** `POST /approvals/{id}/approve` follows this verbatim — except the cross-surface dedup INSERT is FIRST, BEFORE the state-machine transition:

```python
@router.post("/approvals/{proposal_id}/approve", response_class=HTMLResponse)
async def approve_proposal_endpoint(
    request: Request, proposal_id: str, user_id: str = Depends(require_session)
) -> HTMLResponse:
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session, session.begin():
            outcome = await claim_action(
                session,
                proposal_id=proposal_id,
                action_id="approve_proposal",
                actor_slack_user_id=None,
                actor_gekko_user_id=user_id,
                source="dashboard",
            )
            if outcome == "duplicate":
                # Re-render card with current truth (D-56 — visual state IS the feedback)
                row = await session.get(Proposal, proposal_id)
                return templates.TemplateResponse(
                    "proposal_card.html.j2",
                    {"request": request, "status": row.status, ...},
                )
            # ... transition + append_event + dispatch (mirrors live_confirm_post:712-735) ...
    finally:
        if engine is not None:
            await engine.dispose()
    # Return updated card partial — HTMX swaps via hx-target="closest article" hx-swap="outerHTML"
    return templates.TemplateResponse("proposal_card.html.j2", {"request": request, ...})
```

### 2k. Cross-user defense + cookie-bound user_id

**Source:** `src/gekko/approval/slack_handler.py:141-161` (Slack-side defense — the model for the dashboard auth gate)

```python
if slack_user_id != settings.slack_user_id:
    log.warning(
        "slack.approval.cross_user_refused",
        decision_id=decision_id,
        slack_user_id=slack_user_id,
        configured_user_id=settings.slack_user_id,
    )
    from gekko.execution.executor import _send_slack_dm
    await _send_slack_dm(
        gekko_user_id, "You are not the owner of this proposal."
    )
    return
```

**Phase 3 application:** The dashboard `require_session` dependency mirrors the cross-user check via the cookie:

```python
async def require_session(request: Request) -> str:
    """Return the gekko_user_id bound to the current session, or redirect to /login."""
    settings = get_settings()
    sess_user_id = request.session.get("gekko_user_id")
    if not sess_user_id or sess_user_id != settings.gekko_user_id:
        # Either no cookie, or cookie bound to a different user (P3 single-operator)
        # Per D-57: localhost-only single-operator means there's ONE valid user_id.
        raise HTTPException(
            status_code=302,
            headers={"Location": f"/login?next={request.url.path}"},
        )
    return sess_user_id
```

CRITICAL: never log the session cookie value or the passphrase — extend the `_redact` processor's regex set (per CONTEXT.md `<code_context>` D-25 note).

### 2l. Pre-existing `executor.market_closed` DM site + extension for severity emoji

**Source:** `src/gekko/execution/executor.py:454` (the MarketClosed DM — already wired per P2 carry-forward)

```python
# (executor.py around line 454 — the MarketClosed branch's DM)
await _send_slack_dm(
    user_id,
    f"Market closed; deferred order for {tp.ticker}. P7 will add retry-on-open.",
)
```

**Phase 3 application:** Prepend severity-tier glyph per RESEARCH §REPT-01:

```python
await _send_slack_dm(
    user_id,
    f"⚠️ Market closed; deferred order for {tp.ticker}. P7 will add retry-on-open.",
)
```

Glyph map (locked in UI-SPEC §"Copywriting Contract"):
- `⚠️` informational warnings (MarketClosed, deferred)
- `❌` errors (BrokerOrderError, cap_rejection)
- `🚫` kill-state changes
- `⏰` expiry events
- `📊` daily P&L summary header
- `✅` approval / fill confirmations

### 2m. Alembic batch_alter_table for CHECK + add_column

**Source:** `migrations/versions/0002_orderguard.py:76-110` + `migrations/versions/0003_event_types_phase2.py:86-110`

```python
def upgrade() -> None:
    # 1. users — add kill_active columns
    with op.batch_alter_table("users") as bop:
        bop.add_column(
            sa.Column("kill_active", sa.Boolean(), nullable=False,
                      server_default=sa.text("0"))
        )
        bop.add_column(sa.Column("kill_active_since", sa.String(), nullable=True))
        bop.add_column(sa.Column("kill_active_reason", sa.String(), nullable=True))

    # 2. strategy_metadata — new table
    op.create_table(
        "strategy_metadata",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("strategy_name", sa.String(), nullable=False),
        sa.Column("live_mode_eligible", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        ...
    )
```

```python
# 0003 — CHECK constraint replacement pattern
def upgrade() -> None:
    with op.batch_alter_table("events") as bop:
        bop.drop_constraint("ck_event_type", type_="check")
        bop.create_check_constraint(
            "ck_event_type",
            _in_check("event_type", _FROZEN_EVENT_TYPES),
        )
```

**Phase 3 application:** `migrations/versions/0004_p3_hitl_ux.py`:

```python
revision: str = "0004_p3_hitl_ux"
down_revision: str | None = "0003_event_types_phase2"

_FROZEN_PROPOSAL_STATUSES = (
    "PENDING", "APPROVED", "REJECTED", "EXECUTING", "FILLED", "FAILED",
    "AWAITING_2ND_CHANNEL", "APPROVED_LIVE",
    "EXPIRED",  # P3 addition
)
_FROZEN_PROPOSAL_STATUSES_PRE = (
    "PENDING", "APPROVED", "REJECTED", "EXECUTING", "FILLED", "FAILED",
    "AWAITING_2ND_CHANNEL", "APPROVED_LIVE",
)
_FROZEN_EVENT_TYPES = (
    "decision", "proposal", "approval", "rejection", "order_submitted",
    "fill", "kill_switch", "cap_rejection", "credentials_added",
    "live_mode_promoted", "live_mode_demoted", "first_live_trade_confirmed",
    "expiration", "dedup_click", "edit_size", "daily_pnl",  # P3 additions
    "error",
)
_FROZEN_EVENT_TYPES_PRE = (... 0003's vocab ...)


def upgrade() -> None:
    # 1. users — add quiet hours + timezone columns
    with op.batch_alter_table("users") as bop:
        bop.add_column(sa.Column("quiet_hours_start", sa.String(), nullable=True))
        bop.add_column(sa.Column("quiet_hours_end", sa.String(), nullable=True))
        bop.add_column(sa.Column("timezone", sa.String(), nullable=True))

    # 2. proposals — add expires_at + slack_message_ts + slack_message_channel
    #    + extend status CHECK with EXPIRED
    with op.batch_alter_table("proposals") as bop:
        bop.add_column(sa.Column("expires_at", sa.String(), nullable=True))  # D-61: NULL grandfathered
        bop.add_column(sa.Column("slack_message_ts", sa.String(), nullable=True))
        bop.add_column(sa.Column("slack_message_channel", sa.String(), nullable=True))
        bop.drop_constraint("ck_proposal_status", type_="check")
        bop.create_check_constraint(
            "ck_proposal_status",
            _in_check("status", _FROZEN_PROPOSAL_STATUSES),
        )

    # 3. events — extend event_type CHECK with 4 P3 types
    with op.batch_alter_table("events") as bop:
        bop.drop_constraint("ck_event_type", type_="check")
        bop.create_check_constraint(
            "ck_event_type",
            _in_check("event_type", _FROZEN_EVENT_TYPES),
        )

    # 4. slack_action_dedup — new table
    op.create_table(
        "slack_action_dedup",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("proposal_id", sa.String(), sa.ForeignKey("proposals.proposal_id"), nullable=False),
        sa.Column("action_id", sa.String(), nullable=False),
        sa.Column("actor_slack_user_id", sa.String(), nullable=True),
        sa.Column("actor_gekko_user_id", sa.String(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("slack_trigger_id", sa.String(), nullable=True),
        sa.Column("inserted_at", sa.String(), nullable=False),
        sa.Column("result", sa.String(), nullable=False),
        sa.UniqueConstraint(
            "proposal_id", "action_id", "actor_slack_user_id",
            name="uq_slack_action_dedup_slack",
        ),
        sa.UniqueConstraint(
            "proposal_id", "action_id", "actor_gekko_user_id", "source",
            name="uq_slack_action_dedup_dashboard",
        ),
        sa.CheckConstraint(
            "source IN ('slack', 'dashboard', 'cli')",
            name="ck_slack_action_dedup_source",
        ),
        sa.CheckConstraint(
            "result IN ('first_write', 'duplicate')",
            name="ck_slack_action_dedup_result",
        ),
    )

    # NOTE: D-61 — pre-migration PENDING rows get NULL expires_at (default
    # from the add_column above). The sweep's WHERE clause must filter
    # `expires_at IS NOT NULL AND expires_at <= now()`.


def downgrade() -> None:
    op.drop_table("slack_action_dedup")
    with op.batch_alter_table("events") as bop:
        bop.drop_constraint("ck_event_type", type_="check")
        bop.create_check_constraint("ck_event_type", _in_check("event_type", _FROZEN_EVENT_TYPES_PRE))
    with op.batch_alter_table("proposals") as bop:
        bop.drop_constraint("ck_proposal_status", type_="check")
        bop.create_check_constraint("ck_proposal_status", _in_check("status", _FROZEN_PROPOSAL_STATUSES_PRE))
        bop.drop_column("slack_message_channel")
        bop.drop_column("slack_message_ts")
        bop.drop_column("expires_at")
    with op.batch_alter_table("users") as bop:
        bop.drop_column("timezone")
        bop.drop_column("quiet_hours_end")
        bop.drop_column("quiet_hours_start")
```

### 2n. Slack-bolt async modal `views_open` payload (NEW — no Phase 1/2 analog)

**Source:** None in tree. RESEARCH §3 Pattern 3 documents the shape verbatim. UI-SPEC §Surface 1 is the contract.

```python
# Inside handle_edit_size — replaces handle_edit_size_stub at slack_handler.py:414
async def handle_edit_size(*, ack, body, client):
    await ack()  # FIRST per Pitfall 3
    decision_id = body["actions"][0]["value"]
    slack_user_id = body["user"]["id"]
    trigger_id = body["trigger_id"]  # required for views.open

    # Snapshot ref_price + target_notional_usd from the proposal row.
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

    await client.views_open(
        trigger_id=trigger_id,
        view={
            "type": "modal",
            "callback_id": "edit_size_modal",  # matches @slack_app.view("edit_size_modal")
            "private_metadata": json.dumps({
                "decision_id": decision_id,
                "ref_price": str(ref_price),
                "target_notional_usd": str(tp.target_notional_usd),
                "original_qty": str(tp.qty),
                "ticker": tp.ticker,
                "response_url": body.get("response_url"),
            }),
            "title": {"type": "plain_text", "text": f"Edit size — {tp.ticker} {tp.side}"},
            "submit": {"type": "plain_text", "text": "Approve at this size"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "qty_block",
                    "label": {"type": "plain_text", "text": "New quantity"},
                    "element": {
                        "type": "number_input",
                        "action_id": "qty",
                        "is_decimal_allowed": True,
                        "initial_value": str(tp.qty),
                        "min_value": "0.01",
                    },
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Ref price:* ${ref_price}\n*Target notional:* ${tp.target_notional_usd}"},
                },
            ],
        },
    )
```

The `@slack_app.view("edit_size_modal")` listener handles `view_submission`:

```python
@slack_app.view("edit_size_modal")
async def _edit_size_submit(ack, body, client, view):
    meta = json.loads(view["private_metadata"])
    ref_price = Decimal(meta["ref_price"])
    target = Decimal(meta["target_notional_usd"])
    raw_qty = view["state"]["values"]["qty_block"]["qty"]["value"]
    try:
        new_qty = Decimal(raw_qty)
    except (InvalidOperation, TypeError):
        await ack({
            "response_action": "errors",
            "errors": {"qty_block": "Please enter a numeric quantity."},
        })
        return
    new_notional = new_qty * ref_price
    drift_pct = abs(new_notional - target) / target
    if drift_pct > Decimal("0.02"):
        await ack({
            "response_action": "errors",
            "errors": {
                "qty_block": (
                    f"Drift {drift_pct:.2%} exceeds the 2% safety bound. "
                    f"Target ${target}; this qty = ${new_notional}. "
                    "Adjust qty or re-run the strategy."
                ),
            },
        })
        return
    await ack()  # closes the modal
    asyncio.create_task(_edit_size_submit_workflow(
        decision_id=meta["decision_id"], new_qty=new_qty,
        slack_user_id=body["user"]["id"], meta=meta,
    ))
```

`response_action="errors"` re-renders the modal with the error under the `qty_block` input — Slack preserves the operator's typed qty. NO STATE CHANGE, NO AUDIT EVENT on drift fail.

### 2o. AST grep gate for no-Claude-SDK in deterministic firewall

**Source:** `tests/unit/test_alpaca_live_construction_locked.py:42-80` (the AST walk template) + `tests/unit/test_executor.py::test_executor_module_does_not_import_claude_agent_sdk` (source-bytes check)

```python
import ast
from pathlib import Path

_SRC_ROOT: Path = Path(__file__).resolve().parents[2] / "src" / "gekko"

def _all_python_files() -> list[Path]:
    """Every .py file under src/gekko/."""
    return [p for p in _SRC_ROOT.rglob("*.py") if p.is_file()]
```

**Phase 3 application:** Extend the existing executor SDK-import gate to cover the new P3 deterministic-firewall modules:

```python
def test_p3_modules_do_not_import_claude_agent_sdk():
    p3_modules = [
        "gekko/approval/dedup.py",
        "gekko/approval/quiet_hours.py",
        "gekko/approval/expiry.py",
        "gekko/reporter/daily_pnl.py",
        "gekko/scheduler/jobs.py",
    ]
    for rel in p3_modules:
        src = (Path("src") / rel).read_text(encoding="utf-8")
        assert "claude_agent_sdk" not in src, (
            f"{rel} must not import claude_agent_sdk — deterministic firewall."
        )
        assert "claude-agent-sdk" not in src
```

The dedup table is a DB primitive; the sweep is a deterministic cron; the daily P&L digest is a deterministic Block Kit builder. LLM bytes never reach any of these.

---

## 3. Shared Patterns (Cross-Cutting Concerns)

These six patterns apply to MULTIPLE Phase 3 plans / files; the planner should treat each as a reusable building block referenced from each affected plan's action list rather than reproduced verbatim.

### 3a. `_get_session_factory(user_id) → (sf, engine_or_None)` test seam
**Source:** `src/gekko/execution/executor.py:90-102` + `src/gekko/approval/slack_handler.py:70-88` + `src/gekko/execution/kill_switch.py:101-114` + `src/gekko/strategy/promotion.py:53-61`
**Apply to:** EVERY new function in `src/gekko/approval/dedup.py`, `src/gekko/approval/quiet_hours.py`, `src/gekko/approval/expiry.py`, `src/gekko/reporter/daily_pnl.py`, AND every new route handler in `src/gekko/dashboard/routes.py` that touches per-user DB state.
**Excerpt:** see §2d above.

### 3b. `_escape_mrkdwn` universal route at Slack render boundary
**Source:** `src/gekko/reporter/slack.py:101-118, 220-231`
**Apply to:** EVERY new Block Kit text string in `src/gekko/reporter/daily_pnl.py`, EVERY new card variant in `src/gekko/reporter/slack.py` (expired-card, awaiting-2nd-channel-chip, dedup ephemeral), EVERY new DM in `src/gekko/approval/expiry.py` (the sweep's per-row DM body).
**Excerpt:** see RESEARCH §3g; carry-forward from Phase 2 PATTERNS §3g. Deterministic constants (e.g., "Expired at HH:MM — not executed", "Already approved by @user") pass through unwrapped — only LLM-authored fields (`ticker` when sourced via tool args, `rationale`) route through `_escape_mrkdwn`.

### 3c. `normalize_decimals(payload)` before `append_event`
**Source:** `src/gekko/execution/executor.py:188-220, 283-301, 367-385` + `src/gekko/audit/log.py` interface
**Apply to:** EVERY new `append_event` call site — `expiration` event payload (sweep), `dedup_click` event payload (duplicate gate), `edit_size` event payload (Decimals for qty / notional / drift_pct), `daily_pnl` event payload (Decimals for gross_pnl / per-strategy P&L).
**Excerpt:** see §2g above. The `normalize_decimals(payload)` wrapping is non-negotiable — Pitfall 6 / Phase 1 lock.

### 3d. `asyncio.create_task` + try/except Exception background-task wrapper
**Source:** `src/gekko/dashboard/routes.py:447-460 (_execute_kill_background)` + `src/gekko/slack/commands.py:341-352` + `src/gekko/dashboard/routes.py:294-323 (_run_and_post_dashboard)`
**Apply to:** EVERY new background-task dispatch in dashboard routes (approve / reject / edit-submit workflows, settings save, /login passphrase verify if it does any post-mint work).
**Excerpt:** see Phase 2 PATTERNS §5d. Catches errors so `create_task` doesn't drop them silently.

### 3e. Identity-split routing — single `_send_slack_dm` chokepoint
**Source:** `src/gekko/execution/executor.py:188-216` + every other DM site that already imports it
**Apply to:** EVERY new DM path. The new `_send_slack_dm_respecting_quiet_hours(user_id, text, *, category)` wrapper consults `_resolve_quiet_hours` and routes to `_send_slack_dm` (bypass) or defers/drops (non-bypass during quiet hours).
**Excerpt:** see §2e above. PATTERNS §10 (carry-forward) explicitly forbids a parallel DM path.

### 3f. CSP-clean HTMX templates — no inline `<script>`, no `onclick=`
**Source:** `src/gekko/dashboard/templates/base.html.j2:25-26` (CSP meta tag) + every existing template
**Apply to:** EVERY new template — `login.html`, `approvals_index.html`, `proposal_card.html`, `edit_size_modal.html`, `settings.html`. Interactivity ONLY via `hx-post` / `hx-get` / `hx-target` / `hx-swap` / `hx-trigger` / `hx-confirm` / `hx-disable-elt` attributes interpreted by vendored `htmx.min.js`.
**Excerpt:** see UI-SPEC §"Design System" CSP posture. The test gate `tests/unit/test_dashboard_templates_sri.py` walks the template tree — extend it to cover P3's new templates.

---

## 4. Anti-Patterns Phase 3 Must NOT Do

Phase 1 + Phase 2 already locked these out; Phase 3 must inherit them. Each row is a tripwire — violating it breaks an existing test (named) or an architectural invariant.

| Anti-pattern | What NOT to do | Why (P1/P2 lockout) | Tripwire test / mechanism |
|---|---|---|---|
| **Claude Agent SDK import in dedup / expiry / quiet_hours / daily_pnl modules** | `from claude_agent_sdk import ...` anywhere in `src/gekko/approval/dedup.py`, `src/gekko/approval/quiet_hours.py`, `src/gekko/approval/expiry.py`, `src/gekko/reporter/daily_pnl.py`. | Phase 1 / Phase 2 Anti-Pattern #1 (deterministic firewall). The dedup table is a DB primitive; the sweep is a deterministic cron; the daily P&L digest is a deterministic Block Kit builder. LLM bytes never reach any of these. | Extend `tests/unit/test_executor.py::test_executor_module_does_not_import_claude_agent_sdk` to walk the P3 module list. New `tests/unit/test_no_claude_sdk_in_p3_modules.py` is the explicit gate. |
| **Parallel `_send_slack_dm` path** | Constructing a fresh `slack_app.client.chat_postMessage(channel=..., text=...)` call inside `expiry.py` / `daily_pnl.py` / dedup ephemeral logic instead of routing through the existing seam. | Phase 1 quick-260612-nlv identity-split fix (`gekko_user_id` ≠ `slack_user_id`). A parallel DM path will conflate the two ids and produce `channel_not_found` errors OR worse: silently DM the wrong user in a multi-user future. | New `tests/unit/test_single_dm_seam.py` walks `src/gekko/` and asserts `slack_app.client.chat_postMessage` is called from EXACTLY 2 sites: `executor._send_slack_dm` and `executor._send_slack_dm_blocks`. |
| **In-memory dedup state** | Caching the dedup map in a module-global `dict[str, set[tuple]]` instead of persisting via the `slack_action_dedup` table. | D-41 explicit. Slack's at-least-once delivery survives process restarts; the dedup defense must too. An in-memory dedup auto-clears on the very crashes that often correlate with replay storms. | New `tests/integration/test_dedup_persists_across_restart.py` — spins down + spins up the engine over the same SQLCipher file and asserts the dedup row still bars the replay. |
| **Inline `<script>` in P3 templates** | Add `<script>...</script>` or `onclick="..."` anywhere in `login.html`, `approvals_index.html`, `proposal_card.html`, `edit_size_modal.html`, `settings.html`. | CSP `script-src 'self'` at `base.html.j2:25-26`. All interactivity is HTMX `hx-*` attributes on vendored htmx.min.js. | `tests/unit/test_dashboard_templates_sri.py` (Plan 01-09 + Phase 2 extension) — extend to walk P3's new templates. Assert no inline `<script>`, no inline `onclick=`. |
| **External CDN script without SRI** | Add `<script src="https://cdn.example.com/foo.js">` for a date-picker or other widget. | Phase 1 lockout — vendored HTMX only. UI-SPEC §Design System reinforces "no external CDN". | Same SRI gate test. |
| **Plaintext passphrase / session-cookie value in logs** | Include `passphrase`, `session_cookie`, or the raw value in `log.info(...)` / `log.warning(...)`. | AUTH-04 / D-25 invariant. The `_redact` processor catches Slack tokens + Alpaca keys + Anthropic keys — extend it to cover `passphrase` and `session_cookie`. | Extend `tests/unit/test_logging_redaction.py` (per CONTEXT.md `<code_context>` D-25 note). |
| **Mutating `STATE_TRANSITIONS` body in `proposals.py`** | Refactor `transition_status` (lines 109-160) to add per-status custom branches. | The function is data-driven on the frozenset. Phase 1 / Phase 2 / Phase 3 / future phases extend the frozenset — the body never changes. | `tests/unit/test_approval_proposals.py::test_state_transitions_table_completeness` (15 tests per Plan 01-08 — Phase 2 + Phase 3 extend the expected frozenset literal but the test shape is the same). |
| **Editing qty on a non-PENDING proposal** | Allow `handle_edit_size_submit` to update `tp.qty` when `proposal.status != "PENDING"`. | D-54 invariant. The edit-size flow is the only path that mutates `qty`; mutating qty on APPROVED/EXECUTING/FILLED proposals would defeat the audit chain. | New `tests/unit/test_edit_size_rejects_non_pending.py` — feeds an APPROVED proposal into the submit workflow; asserts state transition fails and qty is unchanged. |
| **Edit-size bypassing OrderGuard's 2% drift check** | Computing `new_qty` and dispatching to executor without re-running the drift gate. | D-54 + D-27 (P2 lock). The Knight Capital defense — operator can edit qty, but cannot bypass the off-by-magnitude check. The Slack `views_open` validation + dashboard POST handler MUST both re-check. | New `tests/unit/test_edit_size_drift_gate.py` covers both transports (Slack `view_submission` and dashboard `POST /approvals/{id}/edit-submit`). |
| **Quiet hours suppressing kill / error / first-live DMs** | Routing kill state changes, `BrokerOrderError`, `OrderGuardRejected`, `MarketClosed`, or first-live-trade fills through the `_send_slack_dm_respecting_quiet_hours` wrapper's "defer" branch. | D-48 bypass categories. These DMs MUST fire regardless of window — operator safety. | New `tests/unit/test_quiet_hours_bypass_categories.py` — configures a quiet window covering "now", invokes each bypass category, asserts each DM fires immediately. |
| **`Strategy.timezone` per-strategy override** | Adding a `timezone` field to the `Strategy` schema. | D-47 explicit — strategy inherits user TZ. Per-strategy TZ would break the operator mental model. | The Strategy Pydantic schema's `extra="forbid"` (line 113 of `schemas/strategy.py`) catches the addition at validation time. New `tests/unit/test_strategy_schema_no_timezone.py` makes it explicit. |
| **Sweep race resolved by application lock instead of state-machine + dedup** | Adding an `asyncio.Lock` to gate the sweep against incoming click handlers. | D-44 / D-53. First-write-wins is enforced at the state-machine layer (idempotent same-state return) + the dedup table (UNIQUE constraint). Application locks would serialize unnecessarily and could deadlock with the per-user audit lock. | New `tests/unit/test_sweep_vs_click_race.py` — exercises both orderings (sweep wins; click wins); both must produce a safe outcome (the late party sees "already handled"). |
| **Per-proposal APScheduler job for expiry** | Creating one `add_job` per PENDING proposal with `DateTrigger(run_date=expires_at)`. | D-50 explicit — single 60s `IntervalTrigger` sweep. Per-proposal jobs scale to N proposals × persistent rows in `apscheduler_jobs`; the 60s sweep is O(1) jobs and survives restart cleanly. | Manual code review at planning time; no runtime test (would need to inspect APScheduler internals). |

---

## 5. No Analog Found

| File | Role | Data flow | Reason (no Phase 1/2 analog) | Planner instruction |
|---|---|---|---|---|
| `src/gekko/dashboard/templates/edit_size_modal.html` (the qty `number_input` with drift preview) | HTMX modal with numeric input + server-side validation re-render | request-response | Phase 1/2 modals are typed-confirm (text input matching a known string). The numeric drift-preview re-render is new. | Author de novo following UI-SPEC §Surface 2c verbatim. The CSP-clean form pattern is borrowed from `promote_to_live_modal.html.j2`; the error-re-render branch is borrowed from `login.html`'s `.login-error` block (UI-SPEC §Surface 3). |
| `src/gekko/dashboard/app.py` Starlette `SessionMiddleware` registration | FastAPI middleware (cookie-bound session) | request-response | Phase 1/2 dashboard has NO session-cookie authentication — `request.app.state.engine` was the only auth boundary (single-operator implicit). | Follow RESEARCH §D-57 verbatim. `app.add_middleware(SessionMiddleware, secret_key=os.urandom(32).hex(), max_age=8*3600, https_only=False, same_site="strict", session_cookie="gekko_session")`. D-58: ephemeral per-restart secret (forced re-login on restart is acceptable). |
| `src/gekko/slack/interactivity.py` `@slack_app.view("edit_size_modal")` listener | Slack-bolt view_submission handler | request-response | No Phase 1/2 modal in tree — `views_open` + `view_submission` is brand new. | Follow RESEARCH §3 Pattern 3 verbatim. The `response_action="errors"` re-render pattern for validation failures is the load-bearing primitive. Test via `tests/unit/test_slack_modal_views_open.py` (no analog; first modal in tree). |

---

## Metadata

**Analog search scope:**
- `src/gekko/approval/` (3 files — proposals.py, slack_handler.py, executor.py reference)
- `src/gekko/audit/` (3 files — log.py, canonical.py, lineage tools)
- `src/gekko/brokers/` (3 files — sampled alpaca.py + base.py for `_send_slack_dm` reference)
- `src/gekko/dashboard/` (templates + routes.py + tailwind.css + base.html.j2 + app.py + 12 templates)
- `src/gekko/db/models.py` (8 tables verified incl. P2 additions)
- `src/gekko/execution/` (executor.py, kill_switch.py, market_hours.py)
- `src/gekko/reporter/slack.py` + `templates.py` (existing Block Kit builders)
- `src/gekko/schemas/strategy.py` + `proposal.py` (Pydantic v2 patterns)
- `src/gekko/slack/` (commands.py + interactivity.py + app.py)
- `src/gekko/scheduler/jobs.py` (APScheduler shape)
- `src/gekko/strategy/promotion.py` (composite-key CRUD analog)
- `src/gekko/agent/proposal_writer.py` (idempotent INSERT pattern)
- `src/gekko/vault/passphrase.py` (module-global singleton — referenced for SQLCipher cache reuse in D-57)
- `migrations/versions/0001_initial.py` + `0002_orderguard.py` + `0003_event_types_phase2.py`
- Phase 1 + Phase 2 plan SUMMARY files (referenced via PATTERNS chain)
- Phase 2 PATTERNS.md (`02-PATTERNS.md` — used as the format reference)

**Files actively read this session (non-overlapping ranges only):**
- `03-CONTEXT.md` (full, 249 lines)
- `03-RESEARCH.md` (1-677 + 678-692 — two non-overlapping ranges)
- `03-UI-SPEC.md` (full, 1006 lines)
- `02-PATTERNS.md` (1-484 + 485-692 — two non-overlapping ranges)
- `src/gekko/approval/proposals.py` (full, 246 lines)
- `src/gekko/approval/slack_handler.py` (full, 461 lines)
- `src/gekko/dashboard/app.py` (full, 302 lines)
- `src/gekko/dashboard/routes.py` (full, 748 lines)
- `src/gekko/scheduler/jobs.py` (full, 189 lines)
- `src/gekko/db/models.py` (full, 503 lines)
- `src/gekko/execution/executor.py` (1-250 — relevant section)
- `src/gekko/reporter/slack.py` (1-250 — relevant section)
- `src/gekko/audit/log.py` (full, 170 lines)
- `src/gekko/slack/commands.py` (full, 368 lines)
- `src/gekko/slack/interactivity.py` (full, 60 lines)
- `src/gekko/schemas/strategy.py` (full, 219 lines)
- `src/gekko/schemas/proposal.py` (1-170 — relevant section)
- `src/gekko/execution/kill_switch.py` (1-200 — relevant section)
- `src/gekko/strategy/promotion.py` (1-120 — relevant section)
- `src/gekko/agent/proposal_writer.py` (190-329 — relevant section)
- `src/gekko/dashboard/templates/base.html.j2` (full, 92 lines)
- `src/gekko/dashboard/templates/kill_modal.html.j2` (full, 50 lines)
- `src/gekko/dashboard/templates/unkill_modal.html.j2` (full, 42 lines)
- `src/gekko/dashboard/templates/kill_active_banner.html.j2` (full, 38 lines)
- `src/gekko/dashboard/templates/first_live_confirm.html.j2` (full, 95 lines)
- `src/gekko/dashboard/templates/promote_to_live_modal.html.j2` (full, 47 lines)
- `src/gekko/dashboard/templates/strategy_edit.html.j2` (full, 75 lines)
- `src/gekko/dashboard/templates/strategies_list.html.j2` (full, 65 lines)
- `src/gekko/dashboard/templates/user_agreement.html.j2` (full, 30 lines)
- `src/gekko/dashboard/templates/trigger_button.html.j2` (full, 8 lines)
- `migrations/versions/0002_orderguard.py` (1-100 — header + upgrade preview)
- `migrations/versions/0003_event_types_phase2.py` (full, 110 lines)
- `tests/unit/test_alpaca_live_construction_locked.py` (1-80 — AST-walk template)

**Pattern extraction date:** 2026-06-17
