# Project Gekko

> An autonomous stock trading agent powered by Claude — turns a plain-English investment thesis into researched, monitored trades on your own brokerage account.

**Status:** Phase 1 (Foundation & Vertical Slice) — implementation complete. The walking skeleton runs end-to-end against Alpaca paper + Slack + Claude on the operator's own hardware. See [Phase 1 walking-skeleton demo](#phase-1--walking-skeleton-demo) below.

---

## What is Project Gekko?

Project Gekko is a self-hosted autonomous trading agent that lets you author an investment strategy in plain English, then researches the market, proposes trades with rationale, and executes them on your own brokerage — starting human-in-the-loop with paper money and graduating to autonomy as trust is earned.

It's designed for individuals who want an LLM-driven trading assistant they actually control, running on hardware they own, talking to brokers they already use. No SaaS, no shared multi-tenant runtime, no "your data is the product."

### Why "Gekko"?

Named (with a wink) after Gordon Gekko of *Wall Street*. The goal is the inverse: a trading agent whose first instinct is to ask permission, log its reasoning, and refuse to talk itself past a hard cap.

---

## Core ideas

| | |
|---|---|
| **Brain** | Claude (via the [Claude Agent SDK](https://docs.anthropic.com/en/api/agent-sdk)) — stays inside the Anthropic ecosystem to minimize technical debt. |
| **Strategy authoring** | Natural-language chat ("I'm bullish on AI infra, max 5% per position, prefer dividend payers"), tunable via a structured form. Ad-hoc steering ("focus on energy this week") is persisted and injected into future cycles. |
| **Research** | Price/quote data, news & sentiment, SEC EDGAR fundamentals, and sandboxed web research — bounded per cycle to prevent autoregressive drift. |
| **Execution** | API-first (Alpaca, Interactive Brokers, Schwab); browser-driven fallback (Robinhood, Fidelity) via Playwright when no public API exists. |
| **Safety floor** | A non-LLM **OrderGuard** layer that every order passes through: idempotent `client_order_id`, universe whitelist, hard caps (size / daily-loss / trades-per-day / sector), paper-vs-live credential pairing, kill switch. |
| **Human-in-the-loop** | Slack Block Kit approval cards with idempotent buttons, configurable quiet hours (no 2am pings), `timeout = REJECT` default, web dashboard fallback. |
| **Trust ladder** | Every strategy starts `propose-only`. User explicitly promotes to `auto-execute-within-caps` per strategy. Portfolio-level caps stack on per-strategy caps. Capital scaling is its own separate trust rung. Anomaly detection auto-demotes on drawdown. |
| **Reporting** | Slack DM (proposals, executions, P&L, alerts), web dashboard (portfolio, history, strategy editor, audit browser), email digests (daily, weekly). |
| **Deployment** | Per-user isolated — each user installs Gekko on their own Mac Mini or Windows machine via a one-command installer. No shared instance, no SaaS. |

---

## What it is *not*

| Anti-feature | Why |
|---|---|
| Day-trading / sub-second loops | LLM inference latency makes this unrealistic and explodes cost. |
| Options spreads, futures, forex | v1 focuses on US equities. Different risk profile. |
| Public SaaS sign-ups | v1 is "me + a few people I share with." Each user runs their own instance. |
| "Investment advice" framing | Hard regulatory line. Gekko is execution tooling acting on the user's own authored strategy. |
| Real-money autonomous from day one | All strategies start human-in-the-loop. Explicit promotion required. |
| Auto-strategy generation (LLM proposes its own strategies) | Strategies are user-authored. Keeps Gekko inside "personal tool" framing. |
| Tax form generation | Punt to your tax software. Gekko exports a CSV. |
| Copy-trading marketplace | Regulatory tripwire. Explicit non-goal. |

---

## Tech stack

- **Runtime:** Python 3.12, single-process modular monolith
- **Orchestration:** Claude Agent SDK
- **Web:** FastAPI + HTMX + Tailwind + Jinja2 (lightweight, single-process)
- **Storage:** SQLite (WAL) + SQLCipher whole-database encryption; DuckDB for analytical reads
- **Scheduler:** APScheduler with SQLite job store
- **Brokers:** `alpaca-py`, `ib_async`, `schwab-py`, `browser-use` (Playwright-based fallback)
- **Market data:** Alpaca IEX feed (primary), `yahooquery` (fallback), Finnhub + Alpha Vantage (news), SEC EDGAR (fundamentals)
- **Notifications:** `slack-bolt` for Slack, Resend for email
- **Process supervision:** `launchd` on macOS, NSSM on Windows
- **Logging:** `structlog` (JSON, rotated)

---

## Roadmap

9 phases, vertical-MVP style — each phase ships an end-to-end capability you can touch.

| # | Phase | What it delivers |
|---|---|---|
| 1 | **Foundation & Vertical Slice** | Working end-to-end loop: Alpaca paper, plain-English strategy, Slack HITL approval, full audit trail. Multi-user-ready data model. |
| 2 | **OrderGuard & Real-Money Alpaca Live** | Non-LLM cap-enforcement layer + first real-money trades (still HITL). Knight-Capital prevention. |
| 3 | **Production HITL UX** | Idempotent Slack Block Kit, quiet hours, timeout=REJECT, web dashboard fallback. |
| 4 | **Agent Architecture & Cost Bounds** | Research/decision agent separation, prompt-injection defense, two-tier LLM cost ceiling (80% degrade, 100% halt). |
| 5 | **Trust Ladder** | Per-strategy promotion (propose-only → auto-within-caps), portfolio-level caps, capital-scaling rung, anomaly demotion. |
| 6 | **Web Dashboard & Multi-User Auth** | Magic-link auth, strategy editor, portfolio view, audit browser, web approval fallback. |
| 7 | **Operations & Observability** | launchd/NSSM supervision, heartbeat, NTP drift check, daily reconciliation, market-hours scheduling. |
| 8 | **Additional API Brokers** | IBKR + Schwab via the same `Broker` abstraction. Schwab's 7-day refresh-token coordinator. |
| 9 | **Browser-Fallback Brokers & Packaging** | Robinhood + Fidelity via `browser-use`. One-command install + first-run wizard. |

Detailed planning lives in [`.planning/`](./.planning/):

- [`PROJECT.md`](./.planning/PROJECT.md) — project intent, requirements, key decisions
- [`REQUIREMENTS.md`](./.planning/REQUIREMENTS.md) — 108 v1 requirements with REQ-IDs across 19 categories
- [`ROADMAP.md`](./.planning/ROADMAP.md) — phase breakdown with success criteria
- [`research/`](./.planning/research/) — stack, features, architecture, pitfalls, summary

---

## Safety posture

This is a real-money tool. The architecture is structured around a handful of catastrophic failure modes that have actually destroyed real trading systems:

- **Knight Capital ($440M in 45 minutes, 2012)** — duplicate-order loops. Mitigated by deterministic `client_order_id` + idempotency + "never auto-retry a POST, query instead."
- **Hallucinated tickers** — LLM types NVAX when it means NVDA. Mitigated by universe whitelist enforced in OrderGuard + ticker-resolver tool the LLM must call instead of typing tickers.
- **Off-by-magnitude position sizing** — 500 shares when meant 500 dollars. Mitigated by `Decimal` everywhere + OrderGuard sanity-checks `qty × price` against declared notional within 2%.
- **LLM "talks itself into" a bad trade** (autoregressive reasoning drift) — Mitigated by bounded research turns + research/decision agent separation + `no_action` as a first-class output.
- **Prompt injection** via news articles or SEC filings — Mitigated by source allowlist + privilege separation (Researcher subagent has zero credential/order access) + delimited untrusted-content blocks.
- **Multi-user credential leakage** — Mitigated by per-user encryption keys, credentials never entering LLM context, explicit `user_id` plumbing through every layer.
- **Runaway LLM cost** — real reported incidents of $4K-$47K agent runaway. Mitigated by a hard daily cost ceiling that the agent cannot talk past (graceful at 80%, hard halt at 100%).

---

## Regulatory framing

Project Gekko is **personal trade-execution tooling acting on the user's own authored strategy**, not investment advice. Each user runs their own isolated instance on their own hardware, the agent never makes investment recommendations the user didn't author, and there is no shared performance dashboard or copy-trading mechanic.

In most US states the SEC investment-adviser de-minimis threshold is "fewer than 6 clients," and the "friends and family" exemption is narrow. If usage grows beyond a handful of personal users, a one-time legal review is the responsible next step.

---

## Disclaimer

**This software is provided as a personal-use tool, not investment advice.** Trading securities involves substantial risk of loss. You are responsible for every trade your instance of Gekko proposes or executes, the tax consequences of those trades, and the operational reliability of the machine you run it on. The authors make no representation that any strategy authored or executed by Gekko will be profitable. Use paper trading until you trust both the agent and your own strategy. Read your brokerage's terms of service — automated trading is prohibited by some brokerages (notably Robinhood), and your account may be subject to closure if you violate those terms.

---

## Phase 1 — Walking-skeleton demo

This is the SKELETON Demo Script: a 5-minute end-to-end run that proves the Phase 1 capability is alive on the operator's own machine.

### Prerequisites

- Python **3.12** (pinned per D-18 — `gekko doctor` confirms)
- [`uv`](https://docs.astral.sh/uv/) for Python tooling
- An [Alpaca paper-trading](https://alpaca.markets/) account (API key + secret)
- A Slack app with bot token + signing secret + the operator's user ID
- An Anthropic API key (Claude Agent SDK)
- Optional: [Finnhub](https://finnhub.io/) free-tier key for news evidence
- On Windows: ensure `tzdata` is installed (already pinned in `pyproject.toml`)

### Environment variables

```bash
export ANTHROPIC_API_KEY=...
export ALPACA_PAPER_API_KEY=...
export ALPACA_PAPER_SECRET_KEY=...
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_SIGNING_SECRET=...
export SLACK_USER_ID=U...          # your Slack member id
export GEKKO_USER_ID=alice         # your local Gekko user (per-user isolated DB)
export FINNHUB_API_KEY=...         # optional; degrades gracefully when absent
```

### One-time setup

```bash
uv sync                            # installs all dependencies
uv run gekko doctor                # env audit — confirms all required vars present
uv run gekko init                  # first-run wizard: passphrase + REG-02 agreement
                                   # → creates encrypted ~/.gekko/<user_id>.db
```

### Author a strategy

Flag mode:

```bash
uv run gekko strategy create \
  --name ai-infra-bull \
  --thesis "I'm bullish on AI infrastructure providers" \
  --watchlist NVDA,AMD,AVGO \
  --max-position-pct 0.05 \
  --max-daily-loss-usd 200 \
  --max-trades-per-day 3 \
  --max-sector-exposure-pct 0.25
```

Or chat mode (STRAT-01):

```bash
echo "I'm bullish on AI infra leaders — NVDA, AMD, AVGO. Conservative caps." \
  | uv run gekko strategy create --from-chat
```

### Run the agent

Terminal 1:

```bash
uv run gekko serve                 # FastAPI dashboard + Slack adapter + APScheduler
                                   # binds to 127.0.0.1:8000 by default
```

Terminal 2 — expose the dashboard to Slack:

```bash
cloudflared tunnel run gekko-dev   # or ngrok / etc — any HTTPS tunnel works
                                   # update your Slack app's Interactivity Request URL
                                   # to <tunnel-url>/slack/events
```

Terminal 3 — trigger a run:

```bash
uv run gekko run ai-infra-bull
```

Within ~60 seconds you should receive a Slack DM with the HITL-01 Block Kit card:
PAPER banner, ticker, action, qty, rationale, 3-5 evidence snippets with links, alternatives considered, confidence, Approve/Reject/Edit-Size/Escalate buttons, and the "Not investment advice" footer.

Click **Approve**. Within seconds (assuming market is open) you'll see:

> *Approved {decision_id}. Placing order…*
> *Paper order filled: BUY 5 NVDA @ $1,234.56 — strategy=ai-infra-bull*

### Inspect the audit log

```bash
uv run gekko audit verify          # walk_chain over the SHA-256 hash chain
# → Chain intact across 5 events for user alice

uv run gekko audit dump --limit 5  # line-delimited JSON of the 5 most recent events
```

The 5 events: `decision` -> `proposal` -> `approval` -> `order_submitted` -> `fill`. Every row links to its predecessor via `prev_hash`/`row_hash` — tampering with any payload breaks the chain.

### Run the automated wave-gate test

```bash
uv run pytest tests/integration/test_trigger_run_end_to_end.py -m integration
```

This runs the same flow in cassette mode (no Slack, no Alpaca, no Claude) — the chain-integrity proof without leaving the dev machine.

---

## Phase 2 — Walking-skeleton demo (OrderGuard + Real-Money Alpaca Live)

This is the Phase-2 demo: a 5-to-10 minute end-to-end run that places a **real $1 limit order** on the operator's Alpaca live account, exercises the HITL-06 dual-channel gate (Slack approve + dashboard `/live-confirm` with a 5-second read timer), and validates that the OrderGuard firewall + audit chain stay intact end-to-end.

**Before running the manual demo, ensure** `uv run pytest tests/integration/test_promote_paper_to_live_end_to_end.py -x` **passes — that's the cassette version of this recipe (no real money), and any failure there is a blocker.**

### Prerequisites

- Phase 1 walking-skeleton demo above has been completed at least once (22-event chain on disk).
- An **Alpaca live-trading account** with at least a few dollars of buying power. Generate live API key + secret at the Alpaca dashboard → **Live Trading → API Keys**.
- The Slack + Anthropic + dashboard tunnel from the Phase 1 demo are still configured. The dashboard tunnel URL must be reachable from the same browser as Slack (so the Slack DM's "Open Dashboard to Confirm" URL-button works in one click).
- Set `DASHBOARD_URL` in your `.env` (or environment) to the **public** dashboard URL (ngrok / cloudflared / Tailscale) — the HITL-06 Slack DM bakes this URL into the second-channel link. The default `http://localhost:8000` only works when Slack and your browser run on the same machine.

### Add live credentials to the SQLCipher vault

```bash
uv run gekko credentials add-alpaca-live
# (prompts for API key + secret with hide_input — values never echo, never land in .env)
# → "Live credentials stored for user chris."
```

Live keys live **only** inside the encrypted per-user SQLCipher DB (`~/.gekko/<user_id>.db`). They never touch `.env`, never appear in logs (the structlog redactor catches them), and never enter LLM context.

### Promote a strategy to live-eligible

```bash
uv run gekko strategy promote-live ai-infra-bull
# (typed-name confirm: re-type "ai-infra-bull" when prompted)
# → "Strategy `ai-infra-bull` is now live-mode-eligible. The next approved
#    trade will require dual-channel confirmation (Slack DM + dashboard)."
```

Open the dashboard at `/strategies` — the `ai-infra-bull` row now shows a **`[LIVE]` red chip**, and a persistent `[LIVE — REAL MONEY] Alpaca live trading is armed.` banner is stuck to the top of every dashboard page.

In the dashboard's strategy edit page, the `mode` `<select>` is now enabled — switch it from `paper` to `live` and save. (Paper-only strategies stay paper; only live-eligible ones can flip.)

### First live trade — dual-channel gate

Trigger a run (Slack or CLI):

```bash
uv run gekko run ai-infra-bull
# or, in Slack DM with the bot: /gekko run ai-infra-bull
```

Within ~60 seconds the operator receives a **dedicated first-live Slack DM** — NOT the regular HITL card:

- **Header:** `🔴 FIRST LIVE TRADE — DUAL CONFIRM REQUIRED`
- **Body:** strategy, ticker, action, notional, rationale (all `_escape_mrkdwn`-wrapped)
- **One button only:** `Open Dashboard to Confirm` (URL-button — no inline Approve/Reject; the dual-channel gate is structurally bypass-proof)

Click `Open Dashboard to Confirm`. The browser opens `/live-confirm/{proposal_id}` — a **full-page** template (not a modal):

- **Hero:** `Confirm First Live Trade — ai-infra-bull`
- Full trade-detail panel (ticker, qty, limit_price, target_notional_usd, rationale)
- Two checkboxes:
  - ☐ `I understand this is real money on my Alpaca live account.`
  - ☐ `I have read the rationale and trade details above.`
- Countdown: `Read time: 5… 4… 3… 2… 1… (you may now confirm)`
- Final button: `Confirm First Live Trade`

Tick both checkboxes. **Wait for the 5-second countdown to elapse** (the server-side timer is the gate — bypassing the visible countdown via DevTools gains nothing). Then click `Confirm First Live Trade`.

The page transitions to:

> *First live trade confirmed. The executor is placing your order with Alpaca. You'll receive a Slack DM when the fill confirms.*

### Verify the fill

Within seconds (assuming market hours and a liquid stock like AAPL with a near-market limit), a Slack DM lands:

> *🔴 LIVE: Filled BUY 1 AAPL @ $X.XX — strategy=ai-infra-bull (client_order_id=…, broker_order_id=…)*

Confirm the fill on the **Alpaca live dashboard** (https://app.alpaca.markets/dashboard/overview → Orders) — the $1 order should show as `filled`, and the position should appear in your account.

### Inspect the 6-event chain

```bash
uv run gekko audit verify
# → Chain intact across 28 events for user chris
#   (22 from Phase 1 + 6 new from this live trade — first_live_trade_stamped + credential events
#    add to the chain length but do not break it)

uv run gekko audit dump --limit 10
```

The 6 trade-related events for this run, in order: `decision` → `proposal` → `approval (awaiting_2nd_channel=True)` → `approval (second_channel=True)` → `order_submitted` → `fill`. Every row links to its predecessor via `prev_hash`/`row_hash` — tampering with any payload breaks the chain.

### Subsequent live trade — single-channel path

Trigger another run on the same strategy:

```bash
uv run gekko run ai-infra-bull
```

This time the Slack DM is the **regular HITL card** (with the red `🔴 LIVE — REAL MONEY` banner + the `⚠️ THIS PLACES A REAL-MONEY ORDER…` caution line). The inline `Approve` button is back — a single click dispatches the executor immediately. No dashboard step. This is because `strategy_metadata.first_live_trade_confirmed_at` was stamped on the first live fill, and the per-strategy dual-channel gate stays closed thereafter (D-32).

### Kill switch sanity check

While a live order is open (or place a new far-from-market limit order on purpose), exercise the kill:

```bash
# In Slack DM: /gekko kill   (then in the follow-up: type "KILL")
# Or CLI:
uv run gekko kill
```

Within 5 seconds:

- All open orders are cancelled (best-effort parallel cancel with 4s gather timeout).
- Slack DM: `🚫 Kill ACTIVE. Cancelled X/Y. Z pending. W failed (see logs).`
- The dashboard shows a persistent `🚫 KILL ACTIVE — no orders will fire.` banner stacked above the live banner.
- Running `gekko run ai-infra-bull` while killed produces a `cap_rejection` audit event with `reject_code=kill_active`.

Resume:

```bash
# Slack: /gekko unkill   (type "UNKILL" to confirm)
# Or CLI:
uv run gekko unkill
```

The kill banner clears; trading resumes.

### Run the automated wave-gate test (cassette mode)

```bash
uv run pytest tests/integration/test_promote_paper_to_live_end_to_end.py -m integration
```

This runs the same flow against a recorded cassette at `tests/fixtures/cassettes/alpaca_live_promote_smoke.json` — no real money, no real Slack, no Claude. Use it as a regression gate before re-running the manual recipe.

### Demo closeout

Record for the audit trail:

- Wall-clock elapsed from `/gekko run` to the fill Slack DM
- The chain ID + the broker_order_id of the actual $1 trade
- Any UI / copy / timing surprises (file as Phase-3 backlog if applicable)

---

### Phase 3 — Production HITL UX demo

This is the Phase-3 demo: a 10-15 minute end-to-end run that exercises every
Phase-3 capability — idempotent Slack Block Kit buttons, quiet hours, proposal
timeout (REJECT on expiry), edit-size modal, web dashboard fallback, and the
16:30 ET daily P&L digest.

**Before running the manual demo, ensure**
`uv run pytest tests/integration/test_p3_walking_skeleton.py -x` **passes —
that is the automated cassette that gates this recipe.**

#### Prerequisites

- Phase 1 + Phase 2 walking-skeleton demos above have been completed at least
  once (the audit chain must have ≥22 events on disk).
- `uv run alembic upgrade head` has been run to apply the Phase-3 migration
  (``0004_p3_hitl_ux``) if not already applied. Verify with:
  ```bash
  uv run alembic current
  # → should show head as 0004_...
  ```
- The same Slack + Anthropic + Alpaca paper + dashboard tunnel from the
  Phase 1 demo are still configured.
- Optional: a second browser window or device for the dashboard-fallback step.

#### One-time setup

```bash
uv run alembic upgrade head   # apply 0004_p3_hitl_ux if not already applied
uv run gekko serve             # starts FastAPI + Slack Socket Mode + APScheduler
                               # boot log should show:
                               #   scheduler.sweep_registered  (expire_stale sweep)
                               #   scheduler.daily_pnl_registered (16:30 ET cron)
```

#### Verify idempotency (HITL-02)

Trigger a strategy run and wait for the Slack proposal DM:

```bash
uv run gekko run <strategy_name>
```

When the HITL Block Kit card arrives in Slack, **click Approve twice rapidly**
(within ~2 seconds of each other). Observe:

- ONE execution fires (one "Approved … Placing order…" DM, one fill DM).
- ONE ephemeral toast: "Already approved by @you at HH:MM. Status: APPROVED."
  (only visible to you, disappears when you dismiss).
- `uv run gekko audit verify` shows chain intact with no duplicate events.

#### Verify quiet hours (HITL-05)

From the dashboard (`http://localhost:8000/settings`) or via CLI, configure
quiet hours on your user profile:

```
quiet_hours_start = 22:00
quiet_hours_end   = 07:00
timezone          = America/New_York
```

Trigger a strategy run at 23:00 ET. Observe that **no Slack proposal DM
arrives** — the proposal is created in the DB but the DM is suppressed until
the quiet window ends. To confirm suppression immediately without waiting:

```bash
uv run gekko audit dump --limit 5
# → "proposal" event exists in the chain but no "approval" or "fill" follow it
```

At 07:01 ET the next morning (or by resetting quiet hours to None), run the
strategy again and observe the DM lands normally.

#### Verify timeout=REJECT (HITL-03)

Create a strategy (or update an existing one) with a short proposal timeout:

```bash
uv run gekko strategy create \
  --name quick-expire-test \
  --thesis "Test expiry — short timeout" \
  --watchlist NVDA \
  --proposal-timeout-minutes 1
```

Trigger a run:

```bash
uv run gekko run quick-expire-test
```

Do **not** click Approve. After ~60 seconds the APScheduler sweep fires and:

- The Slack card swaps to a greyed-out `[EXPIRED]` state (chat.update).
- A separate DM arrives: "Your NVDA BUY proposal expired without action.
  Reason: timeout=REJECT (configured at 1 min on strategy quick-expire-test)."
- Attempting to click Approve on the expired card produces an ephemeral:
  "Proposal already expired at HH:MM."

#### Verify edit-size (HITL-04 / UI-SPEC Surface 1)

Trigger a strategy run and wait for the Slack HITL card. Click **Edit-size**:

- A modal opens with the current qty, ref price, and target notional.
- Submit a new qty **within 2% drift** (e.g., same qty ± 1 share) → modal
  closes, proposal transitions to APPROVED, executor fires normally.
- On a second run, click Edit-size again and submit a qty **with > 2% drift**
  (e.g., 10× the original qty). The modal re-renders with a red error message
  and the state does NOT change — the proposal remains PENDING.

#### Verify dashboard fallback (DASH-04)

1. Stop the Slack Socket Mode adapter (Ctrl-C on the `gekko serve` process, or
   configure the bot with an invalid token temporarily).
2. Navigate to `http://localhost:8000/login` in your browser.
3. Enter the SQLCipher passphrase you used in `gekko init`.
4. The `/approvals` page lists all PENDING proposals.
5. Click **Approve** on a proposal. The card updates inline (HTMX swap).
6. The same execution chain fires (executor → Alpaca paper → fill DM).
7. `uv run gekko audit verify` confirms chain intact with a dedup row
   showing `source="dashboard"`.

#### Verify daily P&L (REPT-01)

Wait until **16:30 America/New_York** on a NYSE trading day (weekday, non-holiday). Observe:

- A Slack DM arrives with the daily P&L digest:
  - Header: "Daily P&L — YYYY-MM-DD"
  - Gross P&L with 📈 or 📉 sign glyph
  - Per-strategy breakdown with fills count
  - Open positions count, fills today count, errors today count
  - "Open dashboard" button linking to `/approvals`
- `uv run gekko audit dump --event-type daily_pnl` shows the digest event
  with `fills_count`, `errors_count`, `gross_pnl` in the payload.

If it is outside market hours or a holiday, the DM is suppressed (NYSE calendar
gate per D-59). Verify by checking `gekko audit dump` — no `daily_pnl` event
on a non-trading day.

#### Run the automated wave-gate test (cassette mode)

```bash
uv run pytest tests/integration/test_p3_walking_skeleton.py -x
```

This runs all four cassette scenarios in under 5 seconds — no real Slack, no
real Alpaca, no Claude. All four must pass before attempting the manual demo:

1. `test_p3_happy_path_approve` — Slack approve + dup-click dedup + fill + P&L
2. `test_p3_happy_path_with_edit_size` — Edit-size modal flow + fill
3. `test_dashboard_fallback` — /login → /approvals → approve → fill
4. `test_expiry_chain` — sweep → EXPIRED + chat.update + expiry DM

#### Demo closeout

Record for the audit trail:

- Which of the 5 manual-only verifications from
  `.planning/phases/03-production-hitl-ux-slack-block-kit-dashboard-fallback/deferred-items.md`
  were completed.
- Any UI / copy / timing surprises (file as Phase-4 backlog if applicable).
- The Phase-3 audit chain length: `uv run gekko audit verify` should show
  "Chain intact across N events for user \<user\>".

---

## License

TBD.

---

*Project Gekko is a [Get Shit Done](https://github.com/opengsd) workflow project. Planning artifacts in [`.planning/`](./.planning/) capture the full decision trail from idea to roadmap.*
