---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
last_updated: "2026-06-09T20:25:00.000Z"
progress:
  total_phases: 9
  completed_phases: 0
  total_plans: 9
  completed_plans: 7
  percent: 78
---

# Project State: Project Gekko

**Last updated:** 2026-06-09 (Plan 01-07 complete — Agent runtime: BudgetTracker (D-13) + four Researcher tools (RES-01..04) + propose_trade/no_action sentinel tools + RESEARCHER/DECISION AgentDefinitions (D-10, D-11) + ProposalWriter (D-15, D-20, hallucinated-ticker mitigation) + trigger_strategy_run orchestrator (D-06) + compile_strategy_from_chat (STRAT-01); STRAT-01 + STRAT-03 + RES-01 + RES-02 + RES-03 + RES-04 + RES-05 closed)

## Project Reference

**Core Value:** A trustworthy autonomous agent that turns a plain-English investment thesis into actual, monitored trades on the user's own brokerage account — starting human-in-the-loop with small dollars and graduating to autonomy as trust is earned.

**Current Focus:** Phase 1 — Foundation & Vertical Slice

## Current Position

Phase: 1 (Foundation & Vertical Slice) — EXECUTING
Plan: 8 of 9 (01-01 + 01-02 + 01-03 + 01-04 + 01-05 + 01-06 + 01-07 complete)

- **Phase:** 1 (Foundation & Vertical Slice)
- **Plan:** 01-08 (Slack + Executor: Block Kit HITL card, /gekko run slash command, Executor wiring AlpacaBroker.place_order, websocket fill stream) — next
- **Status:** Executing Phase 1, Wave 2 (continuing)
- **Progress:** [████████░░] 78%
- **Resume from:** `.planning/phases/01-foundation-vertical-slice-alpaca-paper-slack-hitl/01-08-PLAN.md`

```
[##############....] 78%
```

## Performance Metrics

| Metric | Value |
|--------|-------|
| Phases planned | 9 |
| Phases complete | 0 |
| v1 requirements mapped | 108 / 108 |
| v1 requirements unmapped | 0 |
| Research summaries | 4 (STACK, FEATURES, ARCHITECTURE, PITFALLS) + consolidated SUMMARY |
| Granularity | standard |
| Mode | mvp (Vertical MVP) |

## Accumulated Context

### Decisions Made During Roadmapping

| Decision | Rationale |
|---|---|
| 9 phases (one above standard granularity) | Safety sequencing requires distinct phases for OrderGuard (P2), HITL UX (P3), Agent Architecture (P4), and Trust Ladder (P5); merging any of them would force a load-bearing safety surface into a "polish" bucket |
| Operations (P7) and Deployment (P9) ordering | Operations precedes additional brokers because autonomy + unreliable ops = silent failure. Deployment packaging is merged into P9 alongside browser brokers since both are "shipping the box" |
| Browser-fallback brokers in P9 (last) | All four researchers concur: fragility, TOS risk, lowest confidence — never block a release on a broken browser path |
| Trust Ladder gets its own dedicated phase (P5) | Per PROJECT.md key decision; ARCH and PITFALLS confirm. Real-money autonomy is the highest-stakes design surface |
| Multi-user data model lives in P1, multi-user UI in P6 | Data model cannot be retrofitted (`user_id` plumbing through every layer); UI surface is a deliverable once the data shape is proven |
| All 5 brokers in v1 (ambitious path) | Alpaca P1+P2 (vertical slice + safety floor) → IBKR + Schwab P8 (API path) → Robinhood + Fidelity P9 (browser fallback, last to ship) |
| Cost ceiling is two-tier in P4 | 80% graceful degradation, 100% hard halt. Baked into agent architecture phase, not a polish phase |
| Per-user isolated deployment (selected) | Each user runs their own Gekko instance on their own hardware; multi-user is mainly packaging + onboarding (P9), not runtime multi-tenancy. Data model still carries `user_id` for future-proofing and data export |
| SQLCipher whole-DB encryption + passphrase-on-start | ARCH recommendation chosen over STACK's Fernet+keychain for cross-platform parity (avoids silent failures when service runs without logged-in user session) |
| Decimal for money math, idempotency via `client_order_id` | Non-negotiable per PITFALLS Pitfall 1 (Knight Capital prevention) |
| Robinhood Agentic Trading API status check in P1 | Re-validate the official API before committing to browser adapter in P9 (per BROK-R-01 and PITFALLS Pitfall 8) |

### Open Questions Carried Forward

| Question | Surfaced In | Resolution Phase |
|---|---|---|
| Trust ladder statistical promotion criteria — exact thresholds for "N successful HITL approvals" | PITFALLS Pitfall 13/14; FEATURES discussion point 3 | Phase 5 |
| Default LLM cost ceiling value (USD/day per user) | COST-01 | Phase 4 |
| Wash-sale default behavior — flag only vs. "avoid causing avoidable wash sales" | FEATURES discussion point 2; EXEC-09 | Phase 2 or 5 (decision needed from Chris before live trading) |
| Robinhood Agentic Trading API viability vs. browser adapter | BROK-R-01; STACK + ARCH + PITFALLS | Phase 9 (validate before commit) |
| Capital scaling thresholds (when does $1K-validated strategy need re-confirmation at higher size?) | TRUST-05 | Phase 5 |
| Per-strategy fresh session vs. persistent session | ARCH open question 2 | Phase 4 |

### TODOs

- [x] User to approve roadmap — approved 2026-06-08
- [x] Phase 1 context gathered (`/gsd-discuss-phase 1`) — committed 2026-06-08 (`4a6d4b1`)
- [x] Run `/gsd-plan-phase 1` to decompose Phase 1 into executable plans
- [x] Plan 01-01 executed — uv scaffold + Typer CLI + `gekko doctor` (2026-06-08)
- [x] Plan 01-02 executed — Pydantic Settings + structlog credential redaction (AUTH-04) + conftest fixtures (2026-06-08)
- [x] Plan 01-03 executed — SQLCipher engine (AUTH-03) + 6-table data model + alembic 0001_initial (2026-06-08)
- [x] Plan 01-04 executed — Audit chain: canonical_json + append_event + walk_chain (AUDT-01, AUDT-02) (2026-06-08)
- [x] Plan 01-05 executed — Brokerage ABC + AlpacaBroker paper-only + TradingStream + cassette round-trip (EXEC-01, EXEC-02, EXEC-07, BROK-A-01/03/04/05/06) (2026-06-08)
- [x] Plan 01-06 executed — Pydantic schema contracts: Strategy + HardCaps + Guidance + ResearchBrief + EvidenceSnippet + TradeProposal + NoActionProposal + EventPayload + plain-English diff + next_version (STRAT-04, STRAT-05, STRAT-06, REPT-04, RES-08) (2026-06-09)
- [x] Plan 01-07 executed — Agent runtime: BudgetTracker + 4 Researcher tools + propose_trade/no_action + RESEARCHER/DECISION AgentDefinitions + ProposalWriter + trigger_strategy_run + compile_strategy_from_chat (STRAT-01, STRAT-03, RES-01, RES-02, RES-03, RES-04, RES-05) (2026-06-09)
- [ ] Plan 01-08 — Slack + Executor: Block Kit HITL card, /gekko run slash command, Executor wiring AlpacaBroker.place_order, websocket fill stream (Wave 2 — next)
- [x] Resolve "wash-sale default" decision before Phase 2 plan-phase — flag-only chosen 2026-06-08
- [ ] Resolve "default LLM cost ceiling" value before Phase 4 plan-phase
- [ ] Resolve "trust ladder promotion criteria" placeholder before Phase 5 plan-phase
- [ ] Re-evaluate per-cycle research budget (soft + 2x grace) during Phase 4 — tighten to hard caps if daily ceilings routinely hit

### Phase 1 Context Highlights (locked decisions for downstream agents)

- **Strategy:** minimal v1 fields (name, thesis, watchlist, hard caps); plain-English diff; explicit save; chat supports both new & refine
- **Trigger UX:** Slack + CLI + Dashboard (all three from day one); name-based selection; daily fixed-time schedule supported alongside manual; verbose `no_action`
- **Agent architecture:** Researcher + Decision split from day one via Claude SDK subagents; structured tool calls with `propose_trade(...)` / `propose_no_action(...)`; full evidence + confidence + alternatives per proposal
- **Audit log:** single `events` table + JSON payload; full structured rationale in payload; SHA-256 hash chain enforced in app code; brokerage-standard tax-export CSV columns

Full detail: `.planning/phases/01-foundation-vertical-slice-alpaca-paper-slack-hitl/01-CONTEXT.md`

### Blockers

None.

## Session Continuity

**Next action:** Execute Plan 01-08 — Slack + Executor (Block Kit HITL approval card, `/gekko run` slash command, Executor wiring `AlpacaBroker.place_order(OrderRequest)` from approved TradeProposal, websocket fill stream → fill audit events). Will import `gekko.agent.runtime.trigger_strategy_run` (Plan 01-07) as the single orchestrator entry point. Plan 01-08 builds the Block Kit card from the `TradeProposal` returned by `trigger_strategy_run`; handles approval/rejection interactivity; constructs `OrderRequest(client_order_id=proposal.client_order_id, ...)` for the broker; writes `approval`/`rejection`/`order_submitted`/`fill` events via Plan 01-04's `append_event` with Plan 01-06's typed `EventPayload` shapes.

**Resumable from:** `.planning/phases/01-foundation-vertical-slice-alpaca-paper-slack-hitl/01-08-PLAN.md`. STATE.md + ROADMAP.md + REQUIREMENTS.md + the Plan 01-01..01-07 SUMMARYs + docs/sdk-shape.md provide full context for any agent to pick up the work.

### Decisions from Plan 01-02 (added 2026-06-08)

- _Anthropic + Slack user OAuth shapes added to the redaction regex set._ `_ANTHROPIC` (sk-ant-*) matched before generic `_SK` for clearer audit labels; `_XOXA` added for Slack user OAuth.
- `_REDACT_KEYS` _extends the RESEARCH baseline with Phase 1 env-var names._ Defence in depth against `log.info(**settings.model_dump())` patterns.
- _Recursive value scrub one level deep into dict/list/tuple._ Broker/Slack response payloads are nested dicts; flat-only scrub would miss embedded credentials.
- `get_settings()` _uses `@lru_cache(maxsize=1)` (NOT a module-level singleton)._ Avoids import-time crashes on missing env (gekko doctor handles that with a friendly message) and lets tests swap env via `clean_settings_env.cache_clear()`.
- `Settings.db_url_for()` _is a scaffold URL with literal PLACEHOLDER passphrase._ Plan 01-03 will build the real engine via PRAGMA key in a connect-event hook (passphrase never embedded in URL).

### Decisions from Plan 01-03 (added 2026-06-08)

- _SQLCipher engine uses `creator=` / `async_creator_fn=` callbacks instead of the stock `sqlite+pysqlcipher://` URL form._ The stock dialect reads `url.password` and emits `PRAGMA key=<quoted>`, leaking the passphrase via `str(engine.url)`. Our callback path returns a freshly opened `sqlcipher3.dbapi2` connection (sync) or an `aiosqlite.Connection` wrapping a sqlcipher3 connector (async). URL is `:memory:` placeholder; passphrase lives in handler closure only (T-01-03-05).
- _Added `aiosqlite>=0.22.1` as a direct dependency (Rule 3 — auto-fix blocker)._ The plan's chosen `sqlite+aiosqlite:///` URL form requires it; it was missing from the 01-01 pyproject scaffold. omnilib's standard async-sqlite library; established, not a slopsquat risk.
- _DBAPI exception classes patched on the dialect post-construction._ Without it, raw `sqlcipher3.dbapi2.IntegrityError` escapes SQLAlchemy's wrapping machinery (sqlcipher3 does NOT inherit from sqlite3). Patch overrides `dialect.loaded_dbapi.IntegrityError` etc. to point at sqlcipher3's classes; keeps the standard `sqlalchemy.exc.*` contract intact for downstream code.
- _Sync engine factory `get_sync_engine` mirrors `get_async_engine`._ Plan 01-09's APScheduler `SQLAlchemyJobStore` accepts a pre-built `Engine` — passing `get_sync_engine(...)` keeps the passphrase out of any URL string that APScheduler might serialize. Sync-engine tests cover passphrase-not-in-repr/url specifically (VALIDATION row 01-09-T2).
- _Alembic `sqlalchemy.url` is LEFT BLANK in alembic.ini._ env.py reads `GEKKO_DB_PASSPHRASE` from env at runtime and constructs the engine via `gekko.db.engine.get_async_engine`. Passphrase NEVER persisted in any config file (T-01-03-04).
- _Removed `version_locations = %(here)s/migrations/versions` from alembic.ini._ Alembic splits the setting on whitespace; `%(here)s` expanded to a path with a space ("Project Gekko"), making alembic discover ZERO revisions. Defaulting to `{script_location}/versions` handles spaces correctly.
- _CheckConstraint vocabularies (guidance.scope, proposals.status, events.event_type) are declared once at the top of models.py and copied into 0001_initial.py._ Alembic migrations are frozen historical artifacts; the migration MUST work against any future models.py state, so it cannot import the live vocabulary. The local duplication is intentional and obvious.
- _`BrokerCredential.paper` has `server_default='1'` (TRUE)._ Belt-and-braces over AlpacaBroker's constructor guard (Plan 01-05) — Phase 1 paper-only invariant enforced at both the DB and Python layers.
- _Defense-in-depth `__repr__` excludes payload_json + key_blob + secret_blob._ Defends against accidental `log.info(model_instance)` (AUTH-04 belt-and-braces).

### Decisions from Plan 01-04 (added 2026-06-08)

- _`payload_json` stores the FULL canonical-subset string, not just the inner payload dict._ Per RESEARCH §Pattern 3 the choice was a planner decision; locking in the full string means `walk_chain` is a one-liner `sha256(prev_hash + row.payload_json)` and verification cannot drift out of sync with the writer's canonical-subset schema. Cost: ~30-50 bytes of duplicate data per row.
- _Canonical-subset shape is locked at `{event_type, payload, ts, user_id}`._ Future plans can add keys to the inner `payload` dict without breaking the chain (canonical_json sort_keys handles it). They CANNOT add/remove canonical-subset-level keys without a coordinated migration that invalidates every existing event row's hash. Treat as a one-shot architectural decision.
- _Per-user `asyncio.Lock` dict (registry guarded by a separate `_registry_lock`) over a single module-level Lock._ Plan 01-09 will run APScheduler + Slack + dashboard in the same event loop; independent users must be able to append audit events in parallel. Same-user concurrent appends still serialize (the test gates this with 50-task asyncio.gather).
- _Decimal normalization is the CALLER's responsibility, not `canonical_json`'s._ Per RESEARCH §Pitfall 6, `normalize_decimals(payload)` is the explicit pre-step every money-handling plan must call before `append_event`. The serializer itself does NOT auto-normalize — silently mutating caller-visible payload shape would be a worse failure mode than caller forgetfulness. Plans 01-07 (TradeProposal qty) and 01-08 (fill price) MUST surface this in their SUMMARYs.
- _`normalize_decimals` uses `+payload.normalize()` (unary plus on the normalized value) so `Decimal('0').normalize()` collapses `0E+0` back to `0` in the canonical JSON output (rather than the visually surprising scientific-notation form Python's `Decimal.normalize()` produces for zero).
- _`GENESIS_PREV_HASH = '0' * 64` (lowercase)._ Matches `hashlib.sha256(...).hexdigest()` output so equality comparisons against future row_hashes don't need `.lower()` coercion. Locked per CONTEXT.md Claude's Discretion A11.
- _`walk_chain` advances `expected_prev = row.row_hash` even on a break, surfacing ALL inconsistent rows for forensic analysis (not just the first)._ Useful for detecting forge-then-reseal attacks where a tampered middle row is rehashed by an attacker — the seal would hide that row's break but downstream rows' prev_hash checks would still surface.
- _`append_event` never raises `AuditChainBroken`._ Write/verify separation: `AuditChainBroken` (from `gekko.core.errors`) is for `walk_chain` callers and the `gekko audit verify` CLI to raise. The write path is intentionally tolerant so a single corrupted row doesn't block all future audit writes.

### Decisions from Plan 01-06 (added 2026-06-09)

- _`Strategy.mode` is `Literal["paper", "live"]` with default `"paper"` per D-24 / STRAT-06._ The schema accepts both modes; UI confirmation for the paper→live flip is enforced by Plan 01-09's dashboard, NOT the schema. Schema-layer rejection of `mode="live"` would have forced a coupling between Pydantic and UI logic.
- _`HardCaps.max_position_pct` carries a defensive ceiling of `le=Decimal("0.20")` (20%)._ Per RESEARCH §"Code Examples", concentrating more than 20% in a single position is an architectural smell — schema rejection catches it at validation time before it reaches OrderGuard (P2). The other caps use Pydantic's `gt`/`ge` bounds without defensive ceilings (callers know their own risk profile).
- _`ResearchBrief` uses `model_config = ConfigDict(extra="allow")` for forward-compat to P4._ This is the load-bearing forward-compatibility mechanism per RESEARCH §"Pattern 2": P4 hardening will add `injected_content_flags` / `source_allowlist_violations` / `sanitization_applied` as additional optional fields. The `extra="allow"` keeps `model_extra` populated rather than rejecting unknown keys. `research_budget_used: dict[str, Any]` (not a sub-model) lets P4 extend its keys without re-versioning the brief schema.
- _`TradeProposal` uses `extra="ignore"` (NOT "allow")._ Different from ResearchBrief: TradeProposal sits ATTHE audit-log write boundary (D-15 says payload_json IS this model_dump). Allowing unknown extras into the persisted JSON would make the audit-log payload shape less predictable; ignoring keeps the schema clean while still tolerating older deserialized rows.
- _`generate_strategy_diff` is deterministic Python, NOT LLM-generated for P1._ Per RESEARCH §"Don't Hand-Roll", the LLM-generated diff path is acceptable but the deterministic implementation is simpler for P1. P6 may replace with LLM-generated prose if Chris wants richer narratives.
- _`EvidenceSnippet.quote_text` is the UNTRUSTED-content channel; `summary` and `source_type` are trusted._ P4 prompt-injection defense will wrap `quote_text` in `<UNTRUSTED>...</UNTRUSTED>` markers at the Decision-agent prompt boundary. The schema preserves the bytes verbatim — sanitization is a prompt-layer concern, not a schema concern.
- _`EventPayload` is a Pydantic v2 discriminated union via `Discriminator(callable)` + `Annotated[..., Tag(value)]`._ NOT enforced at `append_event`'s write site in Plan 01-04 (that handler still accepts plain dict per its contract). EventPayload is the CALLER-side type validator: Plans 01-07 and 01-08 construct typed payloads, call `model_dump()`, and pass the dict to `append_event`. Recommendation captured in 01-06-SUMMARY's plan-output block: enforce typed validation at the write site even though `append_event` is dict-tolerant.
- _`TradeProposal.client_order_id` is `Field(min_length=32, max_length=32)`._ Exactly the 32-char hex output of `compute_client_order_id` (Plan 01-05). The schema strictness is the load-bearing match: Plan 01-07's ProposalWriter computes `compute_client_order_id(...)`, stores it on the row, AND embeds it in the proposal model; any drift between the two would be caught at TradeProposal validation time.
- _`TradeProposal.evidence` is `Field(min_length=3, max_length=5)` — the D-12 differentiator._ This is the one-shot architectural decision per CONTEXT.md §"specifics" — cannot be retrofitted from free-form prose. Once Plan 01-07's Decision agent emits a TradeProposal, the schema rejection is the LAST gate before persistence; if the agent supplied fewer than 3 or more than 5 evidence snippets, it's a re-prompt loop, not a silent acceptance.

### Decisions from Plan 01-07 (added 2026-06-09)

- _docs/sdk-shape.md is the authoritative claude-agent-sdk 0.2.93 reference; RESEARCH §Code Examples is SUPERSEDED for SDK-shape concerns._ Task 1 was the blocking-human re-verification checkpoint that produced 8 deltas: positional `@tool(name, desc, schema)` decorator; `async def fn(args: dict) -> dict` returning MCP content shape; module-global tool context for DI (no kwargs injection); `create_sdk_mcp_server(name='gekko', ...)` with `mcp__gekko__*` fully-qualified names; two `query()` calls (Option A) instead of nonexistent `client.delegate(...)`; `<RESEARCH_BRIEF>` regex extraction (no `result.structured_output`); model alias `"sonnet"`; SDK mocked entirely in tests (no `claude` CLI binary).
- _BudgetTracker uses flat per-tool token estimates (100/200/300/500) in P1._ Real `ResultMessage.usage` from the SDK is available but plumbing requires hooking the message stream — P4 scope per docs/sdk-shape.md delta #6. The per-cycle 2x hard halt at the call-count and elapsed-seconds dimensions is the safety net P1 needs.
- _Module-global tool context (gekko.agent.tools.context) is the DI pattern for SDK tools._ The Claude Agent SDK's `@tool` decorator requires `async def fn(args: dict) -> dict` — no kwargs injection. Module-globals are safe under D-18's single-event-loop / single-process modular monolith assumption. `trigger_strategy_run` calls `set_tool_context(budget=..., broker=...)` BEFORE the first `query()` call; the four Researcher tools read via `get_tool_context()`.
- _Two query() calls instead of subagent delegation (Option A)._ The SDK has no `client.delegate(subagent_name, prompt)` method. Orchestrator drives both subagents explicitly: Phase A `query()` with Researcher system_prompt + `allowed_tools=RESEARCHER_TOOLS`; Phase B `query()` with Decision system_prompt + `allowed_tools=DECISION_TOOLS`. The Researcher's transcript NEVER crosses to Decision — only the parsed `ResearchBrief` JSON does (D-10 trust boundary held at the orchestrator layer).
- _Researcher emits `<RESEARCH_BRIEF>{json}</RESEARCH_BRIEF>` in TextBlock; orchestrator regex-parses it._ Alternative was `ClaudeAgentOptions.output_format`, but it's session-level so both subagents would get the same schema (we have two different shapes). P1's text-block parsing is brittle by design — predictable for a constrained system_prompt; P4 can swap to per-call output_format if needed.
- _Decision tool input_schemas are derived from TradeProposal/NoActionProposal JSON Schema with runtime fields stripped._ The LLM does NOT supply `user_id`, `strategy_name`, `decision_id`, `client_order_id` — ProposalWriter fills them per D-20. The schema-strip keeps the model's tool-use prompt focused on the fields it actually picks.
- _ProposalWriter uses `model_dump(mode='python')` then `normalize_decimals(...)` BEFORE append_event._ `mode='json'` converts Decimals to strings before normalize_decimals can collapse trailing-zero variants — defeating Pitfall 6. mode='python' preserves Decimals; canonical_json downstream renders via str(). Decimal('100.0') and Decimal('100') now produce the same audit-chain canonical bytes.
- _ProposalWriter handles concurrent-insert race via IntegrityError handler._ Catch IntegrityError on `session.flush()`, rollback, open fresh transaction, SELECT the winning row, return its TradeProposal. The combination of (a) SELECT-before-INSERT short-circuit, (b) IntegrityError race handler, and (c) deterministic `compute_client_order_id` satisfies the EXEC-02 / Knight-Capital prevention contract end-to-end at the writer layer.
- _Watchlist-violation error event is re-emitted from the orchestrator after rollback._ ProposalWriter queues the error event inside the rejection branch then raises ProposalRejected — which rolls back the queued event. `trigger_strategy_run` catches the exception, opens a fresh transaction, and re-emits the event with a `trigger_strategy_run.proposal_rejected` context marker. Audit-event persistence failure is swallowed (logged but not re-raised) so the original ProposalRejected remains the surface error.
- _`runtime.set_passphrase` / `_get_passphrase` is the SQLCipher passphrase indirection — Plan 01-09 owns the bootstrap._ Production: `gekko serve` / `gekko run` CLI prompts the operator, verifies via `gekko.db.engine.verify_passphrase`, then calls `runtime.set_passphrase(...)` BEFORE any APScheduler / FastAPI route fires. Tests bypass entirely by passing `session_factory=` to `trigger_strategy_run`.
- _`compile_strategy_from_chat` follows the same `<STRATEGY>{json}</STRATEGY>` regex pattern as the Researcher._ Single `query()` call with the Strategy Compiler system_prompt; parses the block; runtime fills `strategy_id` (uuid), `user_id`, `version=1`, `created_at`, `created_by_chat=True`. LLM only authors the user-visible fields (name, thesis, watchlist, hard_caps, mode, schedule_time).

---
*State initialized: 2026-06-08 after roadmap creation*
*Updated: 2026-06-08 after Phase 1 context gathered*
*Updated: 2026-06-08 after Plan 01-02 complete*
*Updated: 2026-06-08 after Plan 01-03 complete*
*Updated: 2026-06-08 after Plan 01-04 complete*
*Updated: 2026-06-08 after Plan 01-05 complete*
*Updated: 2026-06-09 after Plan 01-06 complete*
*Updated: 2026-06-09 after Plan 01-07 complete*
