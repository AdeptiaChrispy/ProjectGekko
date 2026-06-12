# Phase 2: OrderGuard & Real-Money Alpaca Live (Safety Floor) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-11
**Phase:** 02-OrderGuard & Real-Money Alpaca Live (Safety Floor)
**Areas discussed:** A. OrderGuard placement + block/flag/backoff matrix; B. Live mode unlock + HITL-06 first-live gate; C. Kill switch scope, persistence, cancel semantics; D. RES-06/07 prompt-injection minimum

---

## A. OrderGuard placement + block/flag/backoff matrix

### A1. Where should OrderGuard live in the code?

| Option | Description | Selected |
|--------|-------------|----------|
| Decorator wrapping Brokerage.place_order | OrderGuard is itself a Brokerage subclass that wraps a real broker and delegates. Same place_order(req) signature. brokers/base.py docstring pre-declares this pattern. Composes cleanly with P8/P9 brokers. | ✓ |
| Inline checks in executor.py | Add a _run_orderguard_checks() block in execute_proposal between APPROVED-load and broker.place_order. | |
| Separate orderguard module (pure functions) | gekko.guard.orderguard.check(req, strategy, account) returns GuardVerdict. Pure functions, no broker. Called from executor. | |

**User's choice:** Decorator wrapping Brokerage.place_order
**Notes:** Pattern is already pre-declared in Phase 1 brokers/base.py docstring; composes with all future broker subclasses.

### A2. qty×price 2% sanity reference

| Option | Description | Selected |
|--------|-------------|----------|
| Fetch broker last quote at place_order time | Guard fetches AlpacaBroker.get_quote(ticker) right before place_order. No schema change. MARKET orders need a separate path. | |
| Schema-add: TradeProposal.target_notional_usd | LLM declares dollar intent as a separate field. Guard rejects if qty × ref_price diverges from target_notional_usd by >2%. Strongest defense against off-by-magnitude. | ✓ |
| Both — quote drift AND declared notional | Belt-and-suspenders. LLM declares target_notional; guard checks qty×limit_price within 2% of declared AND limit_price within 2% of last quote. Could mis-fire at market open on spread widening. | |

**User's choice:** Schema-add: TradeProposal.target_notional_usd
**Notes:** Requires schema migration + propose_trade tool definition update. The 10x-error coincidence in BOTH qty and limit_price simultaneously is near-impossible.

### A3. HITL card pre-warn vs. OrderGuard block

| Option | Description | Selected |
|--------|-------------|----------|
| Standard — BLOCK in OrderGuard, only wash-sale flagged in card | Minimum that satisfies success criteria. PDT/T+1 only visible as rejection events post-approval. | |
| Pre-warn — surface PDT/T+1 risk in HITL card too | OrderGuard still BLOCKS at place_order; HITL card additionally pre-warns PDT/T+1/wash-sale before approval click. Defense in depth. | ✓ |
| Minimal — punt PDT/T+1 to a later phase | Roadmap edit; defers EXEC-11. Breaks success criterion 5. | |

**User's choice:** Pre-warn — surface PDT/T+1 risk in HITL card too
**Notes:** Two-layer defense — user sees risks before clicking; OrderGuard re-checks state at place_order time.

---

## B. Live mode unlock + HITL-06 first-live gate

### B1. Live mode toggle representation

| Option | Description | Selected |
|--------|-------------|----------|
| Strategy.live_mode_eligible (bool) | Toggle requires typed-name confirmation via CLI or dashboard; Slack does NOT have promotion command. | ✓ |
| Strategy.mode = Literal['paper','live'] | Explicit field; allows per-run override (run a live strategy in paper for testing). | |
| Account-level live flag + per-strategy approve list | Two-layer gate. Allows whole-account paper override. | |

**User's choice:** Strategy.live_mode_eligible (bool)
**Notes:** Promotion via CLI or dashboard, NOT Slack (deliberate friction for high-stakes action). Both require typed strategy name to confirm.

### B2. HITL-06 dual-channel gate location

| Option | Description | Selected |
|--------|-------------|----------|
| State-machine extension — new AWAITING_2ND_CHANNEL state | PENDING→APPROVED→AWAITING_2ND_CHANNEL→APPROVED_LIVE→EXECUTING→FILLED. Strategy.first_live_trade_confirmed_at tracks already-confirmed strategies. | ✓ |
| Separate live_confirmations table | Two parallel acks; executor polls. Less state-machine churn; more bookkeeping. | |
| Atomic dual-channel ack | Must click both before either counts. Most user-friction. | |

**User's choice:** State-machine extension — new AWAITING_2ND_CHANNEL state
**Notes:** Per-strategy gate (not per-user) — subsequent live trades on a confirmed strategy skip the gate.

### B3. Live-mode visual treatment

| Option | Description | Selected |
|--------|-------------|----------|
| Banner only — red LIVE on every surface | Slack header + dashboard top-bar + CLI ANSI. Minimum. | |
| Banner + in-card warning line + 'live' chip on rationale | Banner everywhere PLUS a "THIS PLACES A REAL-MONEY ORDER" line above Slack buttons + dashboard 'LIVE' chip on rationale. Belt + suspenders against banner blindness. | ✓ |
| Heavy — banner + warning + scroll-to-confirm gate in dashboard | Dashboard requires scrolling to bottom. Probably overkill given HITL-06 already requires two channels. | |

**User's choice:** Banner + in-card warning line + 'live' chip on rationale
**Notes:** Build on Phase 1's existing build_proposal_card(account_mode=...) parameter.

### B4. Live API key storage

| Option | Description | Selected |
|--------|-------------|----------|
| SQLCipher vault — entered via `gekko credentials add alpaca-live` | Live keys encrypted-at-rest in per-user SQLCipher DB. Existing passphrase unlocks them at runtime. Stronger than .env. | ✓ |
| .env file (matches paper pattern, plaintext on disk) | Simplest. Weakest — plaintext on disk even if gitignored. | |
| Both — env var override + vault fallback | Vault primary; env var for test/CI key rotation drills. | |

**User's choice:** SQLCipher vault — entered via `gekko credentials add alpaca-live`
**Notes:** .env stays paper-only. Live keys NEVER on plaintext disk.

---

## C. Kill switch scope, persistence, cancel semantics

### C1. Kill switch scope

| Option | Description | Selected |
|--------|-------------|----------|
| Global only — one big red button | One DB flag at user-level. Per-strategy halt achievable by un-promoting or rejecting proposals. | ✓ |
| Global + per-strategy | Two levels of kill flag; OrderGuard checks both. Duplicates capability already available via un-promote. | |
| Global + emergency-only — no unkill same-day | Adds 4-hour cooling-off after kill. Cuts user agency in real emergency. | |

**User's choice:** Global only
**Notes:** Per-strategy halt achievable via live_mode_eligible=False or rejecting proposals.

### C2. Kill persistence across restart

| Option | Description | Selected |
|--------|-------------|----------|
| Persists across restart — explicit unkill required | DB column; survives crash/reboot. Safe-by-default. | ✓ |
| Auto-clears on restart — kill is process-local | In-memory only. Faster recovery from accidental kill, but defeats kill if runaway also crashes the process. | |
| Persists with 24h TTL | Auto-clears after a day. Drift risk — operator forgets, system silently resumes. | |

**User's choice:** Persists across restart — explicit unkill required
**Notes:** Boot sequence Slack-DMs the operator if kill_active=true on startup.

### C3. Cancel-open-orders semantic

| Option | Description | Selected |
|--------|-------------|----------|
| Best-effort + parallel cancel + report | set kill_active=true first; asyncio.gather cancel all; 4s timeout; DM tally. Meets 5s SLA; surfaces partial-failure. | ✓ |
| Required-success-before-ack | Wait for every cancel confirmed. Strongest semantic; may exceed 5s SLA. | |
| Ack first, cancel in background | Fastest response (<100ms) but loses visibility. | |

**User's choice:** Best-effort + parallel cancel + report
**Notes:** Cancelled/pending/failed counts surfaced to operator in the Slack DM.

### C4. Kill trigger surfaces

| Option | Description | Selected |
|--------|-------------|----------|
| Slack + dashboard + CLI — three surfaces, all typed-confirm | Operator can always reach at least one surface. | ✓ |
| Slack + dashboard only — literal success criterion | Operator on the machine with wedged Slack and dashboard is stuck. | |
| Slack + dashboard + CLI + hardware fallback file | Three normal surfaces + filesystem-trigger watcher. Overkill at v1. | |

**User's choice:** Slack + dashboard + CLI — three surfaces, all typed-confirm
**Notes:** Unkill is symmetric across all three surfaces.

---

## D. RES-06/07 prompt-injection minimum

### D1. Source allowlist scope

| Option | Description | Selected |
|--------|-------------|----------|
| Per-tool trust tiers + host allowlist for web only | Three tiers: structured-API trusted; news APIs delimited; web host-allowlisted then delimited. Curated frozenset of allowed hosts. | ✓ |
| Wrap everything in `<untrusted_content>`, no host allowlist | Even structured-API data wrapped. No host filter. Simpler; weaker. | |
| Host allowlist for ALL external sources | Strictest. Even Finnhub article content host-filtered. Maximum defense; max operational overhead. | |

**User's choice:** Per-tool trust tiers + host allowlist for web only
**Notes:** Decision agent system prompt includes the standard untrusted-content warning. Full red-teaming + suspicious-content detection deferred to Phase 4.

---

## Claude's Discretion

Items left to research / planning that don't need user input now:

- Exact backoff parameters for EXEC-08 (base seconds, max retries, jitter percentage) — researcher pulls current Alpaca rate-limit docs.
- Library choice for the retry loop (tenacity is the default).
- Exact PDT detection depth (query Alpaca's account flag vs. roll our own 5-day count) — researcher validates.
- Exact T+1 settlement-cash calculation source (Alpaca exposes several relevant fields) — researcher confirms.
- Strategy schema migration / Alembic revision sequencing for new columns.
- cap_rejection event payload field names + reject_code enum values.
- Slack /gekko kill confirmation modal flow (slash-command, two-step pattern).
- Full Web allowlist initial seed.
- Where exactly the live-keys vault row lives in the SQLCipher schema (new credentials table vs. column on users).

## Deferred Ideas

Ideas mentioned during discussion that were noted for future phases:

- **Hardware fallback kill file** (e.g., `/etc/gekko/KILL`) — discussed in C4; rejected for P2; reconsider in P7 (Operations & Observability).
- **Per-strategy kill switch** — discussed in C1; achievable today via un-promote or reject. Promote to first-class in P5 (Trust Ladder).
- **Slack `/gekko promote-live <strategy>` command** — explicitly rejected in B1 as deliberate friction. Revisit if needed in P3.
- **Suspicious-content audit event + detection patterns** — beyond D-39/D-40 minimums; Phase 4 (success criterion #2 explicitly).
- **Full prompt-injection red-team battery** — Phase 4 (Agent Architecture & Cost Bounds).
- **Daily kill-state TTL** — discussed in C2 and rejected (drift risk).
- **Required-confirm-cancel-everything semantic on kill** — discussed in C3 and rejected (partial-failure visibility is more useful).
- **Hardware MFA / TOTP for live-mode promotion** — out of scope for v1 self-hosted single-user-per-instance.
