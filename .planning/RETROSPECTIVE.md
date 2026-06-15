# Project Gekko — Living Retrospective

## Milestone: v1.0 — Vertical-Slice MVP

**Shipped:** 2026-06-15
**Phases:** 1 (Foundation & Vertical Slice) | **Plans:** 9 | **Tasks:** 26
**Timeline:** 2026-06-08 (roadmap created) → 2026-06-15 (milestone close) = 8 calendar days
**Code commits in milestone range:** 30+ (see `git log 2f0747c..HEAD`)

### What Was Built

A self-hosted, human-in-the-loop, Slack-approved paper-trading agent that runs end-to-end on the operator's machine:

- Encrypted per-user SQLCipher DB + Alembic migration over 6 P1 tables, with a typed `WrongPassphraseError` and `GEKKO_DB_PASSPHRASE` env-var fallback for headless runs
- Append-only SHA-256 hash-chained audit log with `canonical_json` + `walk_chain` (36 unit tests)
- Brokerage ABC + paper-only `AlpacaBroker` with Knight-Capital two-layer constructor guard and HTTP-422 duplicate handling (49 broker tests + cassette integration with `GEKKO_TEST_LIVE_ALPACA=1` live opt-in)
- Locked Pydantic schemas: Strategy, ResearchBrief, TradeProposal, NoActionProposal, EventPayload discriminated union (88 schema tests)
- Claude Agent SDK orchestrator: BudgetTracker + 4 Researcher tools + 2 Decision tools, two `query()` calls (Researcher → Decision), `<RESEARCH_BRIEF>` regex parse at the trust boundary
- Slack Block Kit HITL card + Approve/Reject/Edit-Size/Escalate buttons, cross-user defense, mrkdwn-escape prompt-injection defense, deterministic Executor with market-hours guard
- Real CLI (`init` / `serve` / `run` / `strategy create` / `audit verify|dump`), APScheduler 3.x with `SQLAlchemyJobStore` over a pre-built sync engine, FastAPI dashboard with vendored HTMX 2.0.4 + SHA-384 SRI gate
- Socket Mode adapter so no public tunnel is needed when `SLACK_APP_TOKEN` is set
- Walking-skeleton E2E test that asserts the 5-event chain integrity via `walk_chain`

### Manual demo result (2026-06-12)

`gekko audit verify` → **"Chain intact across 22 events for user chris"**. Three full 5-event happy-path subsequences with real Alpaca paper fills: AVGO BUY 1 @ $381.84, NVDA BUY 2 @ $204.97, AMD BUY 0.97 @ $513.40 (limit unfilled at close).

### What Worked

- **Walking-skeleton vertical-slice mode (MVP).** Each Phase-1 plan delivered an end-to-end thin slice. By plan 9, the operator could actually trigger a trade. This was the right shape — far better than building OrderGuard first, then HITL UI, then proving the loop works.
- **Cassette + live-opt-in integration tests (`GEKKO_TEST_LIVE_ALPACA=1`).** Default test runs are fast and offline; the live mode catches things the cassette can't (like real Alpaca 422-duplicate response shapes). The cassette never has to be in sync with broker reality for routine CI.
- **Trust boundary at the orchestrator (D-10).** Researcher's transcript never crosses to Decision — only the parsed `ResearchBrief` JSON does. The `<RESEARCH_BRIEF>` regex extraction at the orchestrator made the data flow auditable. Prompt-injection defense in v2.0 has a clean place to live.
- **Manual demo as a quality gate.** The walking-skeleton demo on 2026-06-12 surfaced 7 production bugs across two close-out passes — bugs that were architecturally invisible to the cassette tests because the bugs were in the *identity model* (gekko_user_id vs slack_user_id), the *LLM output distribution* (rationale length), and *cross-process state* (passphrase prompting). All cassette tests passed before the demo started.
- **TDD-with-RED-confirmation for the late fixes.** Quick task `260612-nlv`'s RED phase produced the literal error message `AssertionError: assert 'chris' == 'U08LRFFRBS4'` — that single line was worth more than any review session. Pattern locked in for future quick tasks.
- **Quick tasks for demo-discovery fixes.** Two `/gsd-quick` runs (`260612-dix`, `260612-nlv`) closed the demo-discovery bugs with full GSD discipline (atomic commit, tests, summary, STATE.md row) in ~25 minutes each. Lighter than a full phase, heavier than a direct edit. Right shape for "tiny but load-bearing."

### What Was Inefficient

- **Re-triggering the demo after a code fix without restarting `gekko serve`.** Python imports schemas once at module load; the running server still had the old `max_length=1000` in memory. Bit us once during the demo. **Future fix:** add a "serve was started at X; latest schema commit is Y; restart if Y > X" warning at trigger time, OR document the restart-after-fix workflow more prominently.
- **The `audit-open` SDK tool's filename mismatch with the quick-workflow's `{quick_id}-SUMMARY.md` convention.** Wasted ~5 min at milestone close investigating "audit says missing but tasks are done." Filed as a known GSD-SDK upstream issue; we acknowledged-and-proceeded.
- **The `agent.run.complete` log's misleading `outcome=propose_trade` for a *failed* run.** When the rationale-overflow ValidationError fired, `agent.run.complete` STILL logged outcome=propose_trade — because the orchestrator log fired before `write_proposal`'s exception propagated up. **Future fix:** wrap `agent.run.complete` in a try/finally that records the actual run outcome (`failed`, `propose_trade`, `propose_no_action`).
- **Yahooquery + Finnhub timeouts inside the Researcher.** A few Researcher cycles took 3-4 min because yahooquery / Finnhub were slow. No timeout was wired. **Future fix:** explicit `timeout=10.0` on every Researcher-tool HTTP call.

### Patterns Established

- **Identity-split pattern.** Anywhere we DM a user from a server-side path, we now ask: "Am I passing `settings.slack_user_id` or the per-row `user_id` (gekko_user_id)?" If it's the latter, that's a bug. Fixed in 4 places at v1.0 close; the pattern is locked for future Slack DM-emitting code.
- **Audit-first ordering at fragile boundaries.** `on_fill_event` writes the `fill` event INSIDE the transaction and commits BEFORE attempting the Slack DM. Result: even when the DM crashed for hours, the audit chain stayed intact. Apply this pattern broadly in v2.0 Executor + OrderGuard.
- **`canonical_subset` shape locked at the audit boundary.** `{event_type, payload, ts, user_id}` is the canonical shape, stored as literal `payload_json`. Future plans can add keys to the inner `payload` dict without breaking the chain; they cannot add/remove canonical-subset-level keys without a coordinated migration. Single most important schema decision in v1.0.
- **Two `query()` calls (Researcher → Decision), not subagent delegation.** Claude Agent SDK 0.2.93 does not have `client.delegate(subagent_name, prompt)`. The orchestrator drives both subagents explicitly with two `query()` calls and the Researcher's `ResearchBrief` as the *only* thing that crosses. P4 hardening has a clean place to add prompt-injection defenses.
- **`/gsd-quick` for production demo discoveries.** When live-demo discoveries land bugs, route the fix through `/gsd-quick` (atomic commit, tests, STATE.md row). Two such tasks (`260612-dix`, `260612-nlv`) at v1.0 close; this is the right cadence.

### Key Lessons

- **Cassette tests are necessary but not sufficient.** They tell you the WIRING is correct; they cannot tell you that the LLM's real output respects the schema, or that Slack's real identity model matches what you mocked. Always plan a manual demo as a *first-class quality gate* — not as something you do after CI passes.
- **The audit chain is the load-bearing artifact.** When the fill DM failed for hours during the live demo, the audit chain still proved the trades had executed correctly. SHA-256-chained, deterministic-canonical-bytes append-only logs are worth their complexity.
- **Phase-1 walking-skeleton mode (MVP) was the right call.** Building OrderGuard first (v2.0 Phase 2) would have been correct technically but wrong product-wise — we'd have a perfect safety floor with no agent loop to safeguard. Vertical slices first, hardening after.
- **Identity bugs are the most common "the demo passed but it's still broken" failure.** 4 of the 7 demo-discovery bugs at v1.0 close were identity-split errors (gekko_user_id vs slack_user_id). Add identity-correctness assertions to integration tests in v2.0.
- **Python imports the schema once.** Restart serve after a schema change, always. Document it; ideally auto-warn.

### Cost Observations

- Sessions: ~25 GSD sessions across 8 days (discuss + plan + execute + verify per phase)
- Notable: Most of the milestone's *real* discovery work happened in the final 4 sessions (the 2026-06-12 manual demo + 2 quick-task fixes + this archival). The walking-skeleton + cassette tests got us 85% of the way there; the manual demo + quick tasks got the last 15% that was actually load-bearing.
- Model mix: Sonnet 4.6 dominated (planners + executors + agent runtime + LLM-in-the-loop research). Opus 4.7 (1M context) used for this session's orchestration.
- Cost ceiling: not formally measured in v1.0; v2.0 / Phase 4 adds the two-tier ledger.

### Cross-Milestone Trends

*(This is the first milestone — table will populate over subsequent milestones.)*

| Metric | v1.0 |
|---|---|
| Phases shipped | 1 |
| Plans shipped | 9 |
| Tasks shipped | 26 |
| Demo-discovery bugs found | 7 |
| Demo-discovery bugs that were identity-related | 4 (57%) |
| Days from roadmap to milestone close | 8 |
| Audit chain integrity at close | ✓ 22 events |
| Test count at close (unit + integration) | 365+ unit + 11 integration |
