# Milestones

## v1.0 Vertical-Slice MVP (Shipped: 2026-06-15)

**Scope:** Phase 1 — Foundation & Vertical Slice (Alpaca Paper + Slack HITL)
**Phase count:** 1 phase, 9 plans, 26 tasks
**Phases 2-9 deferred to v2.0+.** The archived `milestones/v1.0-ROADMAP.md` captures the original 9-phase scope at the moment of close; the live `ROADMAP.md` re-binds Phases 2-9 to v2.0+.

**One-sentence summary:** A self-hosted, human-in-the-loop, Slack-approved paper-trading agent runs end-to-end on the operator's machine — plain-English strategy → research → proposal → approval → broker execution → fill — with a SHA-256-chained audit log under per-user SQLCipher encryption.

**Key accomplishments:**

- **Encrypted, per-user storage.** SQLCipher-encrypted SQLite engine with PRAGMA-key connect-event handler and typed `WrongPassphraseError`; SQLAlchemy 2.x ORM over 6 Phase 1 tables with `user_id` everywhere; Alembic `0001_initial` migration; passphrase never persists in any config file. Sync + async engine factories.
- **Tamper-evident audit log.** Append-only SHA-256 hash chain over the events table, locked-in canonical-subset shape `{event_type, payload, ts, user_id}` stored as literal `payload_json`; per-user `asyncio.Lock` serializes concurrent appends; `walk_chain` detects payload tampering, prev_hash forging, deleted rows, cross-user contamination. 36 unit tests.
- **Safe paper broker, ready to graduate to live.** Brokerage ABC + paper-only `AlpacaBroker` with two-layer constructor guard (Knight-Capital insurance per Pitfall 7), HTTP-422 duplicate handling that calls `get_order_by_client_id` and never re-POSTs (Pitfall 4), LIMIT/MARKET/STOP routing, TradingStream fill listener via `asyncio.to_thread`. Cassette-replay integration test (default) with live opt-in via `GEKKO_TEST_LIVE_ALPACA=1`. EXEC-01 grep gate live; 49 broker tests.
- **Locked schema contracts.** Strategy + HardCaps + Guidance, ResearchBrief + EvidenceSnippet + TickerSnapshot (Researcher→Decision contract with `extra="allow"` for P4 forward-compat), TradeProposal + NoActionProposal + AlternativeConsidered (structured-rationale differentiator), EventPayload discriminated union, plain-English `generate_strategy_diff`, snapshot-row `next_version()`. 88 new unit tests; ruff + mypy --strict clean across 37 src files.
- **Claude Agent SDK orchestrator.** BudgetTracker + 4 Researcher tools (Alpaca/yahooquery quote, Finnhub news, SEC EDGAR filings, web fetch) + 2 Decision tools (`propose_trade` / `propose_no_action`); Researcher and Decision subagents via two `query()` calls (Option A), module-global tool context for DI under D-18, `<RESEARCH_BRIEF>` regex parse at the trust boundary, `compile_strategy_from_chat` for chat-mode strategy authoring.
- **Slack HITL approval surface.** Block Kit card with structured rationale (3-5 evidence + alternatives + confidence), Approve/Reject/Edit-Size/Escalate buttons, cross-user defense (`slack_user_id` ≠ `gekko_user_id` enforced), mrkdwn metachar escape against prompt-injection, deterministic `Executor` with `pandas_market_calendars` market-hours guard (EXEC-10), `on_fill_event` from TradingStream websocket, idempotency via deterministic `client_order_id`.
- **Production lifecycle wiring.** Real CLI (`init` / `serve` / `run` / `strategy create [flag|chat]` / `audit verify|dump`), APScheduler 3.x `AsyncIOScheduler` + `SQLAlchemyJobStore` over a pre-built sync engine (no passphrase in any URL), FastAPI dashboard with vendored HTMX 2.0.4 + SHA-384 SRI gate + Tailwind subset, Socket Mode adapter (no public tunnel needed when `SLACK_APP_TOKEN` is set), passphrase vault (D-19), env-var fallback (`GEKKO_DB_PASSPHRASE`) for headless runs.
- **Manual demo proved end-to-end correctness.** `gekko audit verify` returned **"Chain intact across 22 events for user chris"** on 2026-06-12. Three full 5-event happy-path chains recorded with real Alpaca paper fills: AVGO BUY 1 @ $381.84, NVDA BUY 2 @ $204.97, AMD BUY 0.97 @ $513.40 (limit unfilled at close).

**Demo-discovery fixes landed at close (7 production bugs the cassette tests couldn't have caught):**

1. Identity-split #1: `gekko_user_id` vs `slack_user_id` — slash command path (commit `297a882`)
2. Identity-split #2: cross-user defense lookup site (commit `297a882`)
3. Identity-split #3: post-run-result DM channel (commit `297a882`)
4. structlog `format_exc_info` missing (tracebacks rendered as literal `"true"`) (commit `297a882`)
5. Socket Mode wiring path + `GEKKO_DB_PASSPHRASE` env fallback for headless runs (commit `297a882`)
6. Rationale-overflow `ValidationError`: `max_length=1000` was too tight; raised to 5000 + Slack-render truncate guard at 2900 chars (quick task `260612-dix` — commits `9bc8c36` + `03a9b8e`)
7. Identity-split #4: `_send_slack_dm` was passing `gekko_user_id` to Slack's `chat.postMessage`, crashing with `channel_not_found` after every successful trade (quick task `260612-nlv` — commit `d7b26c8`)

**Known gap (deferred to Phase 3 / v2.0):** Executor errors (`MarketClosed`, `BrokerOrderError`) write `error` audit events but don't surface to Slack. Operator sees "Approved … Placing order…" then silence when the order fails post-approval. Tracked in `quick/260612-dix-raise-rationale-cap-to-5000-slack-render/deferred-items.md`.

**Deferred items at close:** 0 actually deferred. SDK `audit-open` flagged 2 quick tasks as "missing" due to a file-name convention mismatch — both are objectively complete with commits + tests; documented in STATE.md under `Deferred Items`.

**Test posture at close:** 365+ unit + 11 integration passing (cassette mode); manual demo passed on real Slack + Alpaca Paper + Claude Sonnet 4.6 on 2026-06-12 with 22 audit-chain events committed.

---
