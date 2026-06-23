---
phase: 3
slug: production-hitl-ux-slack-block-kit-dashboard-fallback
status: verified
threats_total: 98
threats_closed: 98
threats_open: 0
asvs_level: 1
created: 2026-06-23
---

# Phase 3 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.
> Register authored at plan time (`register_authored_at_plan_time: true`) across all 15 Plan files;
> this audit (State B) consolidates those `<threat_model>` blocks and verifies each
> declared mitigation is present in the implemented code.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| Slack HTTP/WebSocket → Bolt async handler | slack-bolt signature-verify gate (P1 lock, `SLACK_SIGNING_SECRET`) runs BEFORE any handler / `claim_action`. Socket Mode = no HTTP headers. | Interactivity payload (proposal_id, actor slack_user_id, trigger_id) |
| `body["user"]["id"]` → handler-side authoritative actor | Cross-user check (`slack_user_id != settings.slack_user_id`) is the access-control gate after ack, before any state mutation. | Slack user id |
| Browser → FastAPI dashboard | SessionMiddleware-signed cookie carries `gekko_user_id`; ephemeral per-restart secret (D-58); HttpOnly + SameSite=Strict + https_only=False (HTTP-on-localhost per D-57). | Session cookie, passphrase (POST /login only) |
| Auth-gated router (`Depends(require_session)`) vs `public_router` | Two-router fail-closed pattern: only `/login` + `/healthz` are public; every action endpoint inherits `require_session` → 302 /login for unauthenticated callers. | gekko_user_id (session-derived) |
| Operator-supplied qty (edit-size) → cap gate | Edit-size qty is untrusted; `_check_edit_size_caps` (absolute dollar cap = max_position_pct × equity) is the server gate; slider max is display-only. OrderGuard re-checks at execute time. | qty (form / view_submission) |
| Concurrent INSERT → `slack_action_dedup` UNIQUE | SQLite WAL serializes writers; UNIQUE on `(proposal_id, action_id, actor)` is the exactly-once primitive under at-least-once delivery. | Dedup row |
| ProposalWriter → DB row (`expires_at`) | `expires_at` is a SERVER clock value computed `datetime.now(UTC)` AFTER TradeProposal build; LLM has no write path. | Proposal expiry timestamp |
| User IANA tz string → ZoneInfo | `zoneinfo` whitelist; `ZoneInfoNotFoundError` raised before any tz arithmetic / fs access. | timezone string |
| Dual-channel first-live gate | First live trade requires BOTH Slack approve (→ AWAITING_2ND_CHANNEL) AND dashboard /live-confirm (server-side 5s timer + 2 ack checkboxes). Slack first-live card is URL-button-only. | Proposal state transition |
| APScheduler → SQLCipher jobstore | Jobs referenced via `module:fn` string ref; jobstore encrypted at rest (passphrase-on-start). | Pickled job metadata |

---

## Threat Register

Status legend: closed = mitigation verified in code (file:line) / accepted-risk logged / supply-chain n/a.
All `*-SC` supply-chain rows: Phase 3 introduced **no** new pip/npm/cargo dependencies (per 03-RESEARCH.md); each is closed as not-applicable.

### Plan 03-01 — schema/migration + expires_at
| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-03-01-01 | Tampering | 0004 downgrade orphan rows | mitigate | closed | migrations/versions/0004_p3_hitl_ux.py:223 drops `slack_action_dedup` BEFORE :238 drops `expires_at` |
| T-03-01-02 | Tampering | proposal_timeout_minutes (LLM strategy) | mitigate | closed | schemas/strategy.py:161 `Field(default=None, gt=0)`; proposal_writer.py:94,316 falls back to `PROPOSAL_TIMEOUT_DEFAULT_MIN=30` |
| T-03-01-03 | Info disclosure | SlackActionDedup.__repr__ leaks trigger_id | mitigate | closed | db/models.py:364-372 `__repr__` excludes `slack_trigger_id` (comment :365) |
| T-03-01-04 | Info disclosure | new User columns in repr | accept | closed | Accepted-risks log AR-01 (lifestyle metadata, not credentials) |
| T-03-01-05 | Repudiation | LLM cannot influence expires_at | mitigate | closed | proposal_writer.py:310-317 `datetime.now(UTC)` computed after model build; no LLM write path |
| T-03-01-06 | DoS | extreme timeout never expires | accept | closed | Accepted-risks log AR-02 (single-operator self-harm; P5 ceiling) |
| T-03-01-SC | Tampering | dependency installs | n/a | closed | No new deps |

### Plan 03-02 — claim_action dedup gate (HITL-02)
| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-03-02-01 | Spoofing | synthetic interactivity payload | mitigate | closed | slack-bolt signature gate (P1); cross-user check slack_handler.py:219-239 |
| T-03-02-02 | Tampering | approve/reject race corrupts state | mitigate | closed | UNIQUE indexes uq_dedup_slack/uq_dedup_dashboard (0004:171-183); dedup.py:141-196 flush+IntegrityError |
| T-03-02-03 | Repudiation | operator denies clicking | mitigate | closed | dedup.py:129-138 records actor_slack/gekko_user_id+trigger_id+inserted_at; dedup_click event :170-185 |
| T-03-02-04 | Info disclosure | ephemeral leaks first-writer id | accept | closed | Accepted-risks log AR-03 (intended UX, D-43; ephemeral to duplicate-clicker only) |
| T-03-02-05 | DoS | hammer endpoint to fill dedup table | accept | closed | Accepted-risks log AR-04 (signing secret + cross-user + UNIQUE gate; bounded growth) |
| T-03-02-06 | EoP | cross-user click on another's proposal | mitigate | closed | slack_handler.py:219-239 (approve), :506-520 (reject) refuse non-operator |
| T-03-02-SC | Tampering | dependency installs | n/a | closed | No new deps |

### Plan 03-03 — quiet hours + tz
| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-03-03-01 | Spoofing | malicious tz string (e.g. ../etc/passwd) | mitigate | closed | quiet_hours.py:145-148 `ZoneInfo(tz_name)` → ZoneInfoNotFoundError before fs access; routes.py:1130 settings validates against `available_timezones()` |
| T-03-03-02 | Tampering | DST off-by-one missed exec | mitigate | closed | quiet_hours.py uses stdlib `zoneinfo` (DST-native); DST tests in test_quiet_hours_predicate.py |
| T-03-03-03 | Info disclosure | log leaks strategy_name | accept | closed | Accepted-risks log AR-05 (non-sensitive label) |
| T-03-03-04 | DoS / EoP | LLM injects tz to disable quiet hours | n/a in P3 | closed | tz set only via operator /settings form (routes.py:1109); LLM has no User write (D-11/D-18) |
| T-03-03-05 | Repudiation | operator denies 2am window | mitigate | closed | /settings POST gated by SessionMiddleware (routes.py:1109 on auth router); structlog logs update |
| T-03-03-06 | Tampering | LLM 24/7-silence override | accept | closed | Accepted-risks log AR-06 (narrowing awake-time is safer side; P6 scope) |
| T-03-03-SC | Tampering | dependency installs | n/a | closed | zoneinfo stdlib |

### Plan 03-04 — expiry sweep (HITL-03)
| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-03-04-01 | Tampering | mutate expires_at to far future | mitigate | closed | proposal_writer.py:310-317 server-set; operator mutation needs SQLCipher access (single-operator) |
| T-03-04-02 | Tampering | jobstore poisoning via module:fn | mitigate | closed | scheduler/jobs.py:189 `"gekko.approval.expiry:expire_stale_proposals"` string ref; SQLCipher jobstore (P1-09) |
| T-03-04-03 | Info disclosure | expiry DM leaks proposal contents | accept | closed | Accepted-risks log AR-07 (operator is owner; no cross-user flow) |
| T-03-04-04 | DoS | flood of expiries | mitigate | closed | jobs.py / expiry.py:15 `max_instances=1`, `coalesce=True`, misfire_grace_time |
| T-03-04-05 | Repudiation | operator denies expiry | mitigate | closed | expiry.py:338 `expiration` audit event w/ payload; walk_chain integrity |
| T-03-04-06 | EoP | sweep races click → double-exec | mitigate | closed | same-state idempotent transition (proposals.py) + dedup table (03-02) |
| T-03-04-07 | Tampering | future LLM call in expiry breaks firewall | mitigate | closed | AST gate tests/unit/test_expiry_no_sdk_import.py:29-33 red-lights claude_agent_sdk/anthropic |
| T-03-04-SC | Tampering | dependency installs | n/a | closed | No new deps |

### Plan 03-05 — dashboard auth/login/edit-size/CSRF
| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-03-05-01 | Spoofing | forge session cookie | mitigate | closed | app.py:243-250 SessionMiddleware itsdangerous-signed, `secret_key=os.urandom(32).hex()` (D-58 ephemeral) |
| T-03-05-02 | Tampering | open-redirect via `next` | mitigate | closed | routes.py:167-181 urlparse + reject scheme/netloc/`//`/`/\\`; default /approvals |
| T-03-05-03 | Repudiation | operator denies dashboard approve | mitigate | closed | claim_action records actor_gekko_user_id + source="dashboard" (routes.py:375-382); approval event |
| T-03-05-04 | Info disclosure | passphrase logged | mitigate | closed | `_REDACT_KEYS` extended (passphrase/session/gekko_session) — logging_config |
| T-03-05-05 | Info disclosure | session cookie logged | mitigate | closed | same redaction processor (gekko_session substring) |
| T-03-05-06 | DoS | passphrase brute-force | accept | closed | Accepted-risks log AR-08 (localhost-only D-57/REG-03; rate-limit P6) |
| T-03-05-07 | EoP | edit-size bypasses OrderGuard | mitigate | closed | AST gate test_edit_size_not_direct_broker.py:35-58 (zero direct place_order); actions.py _check_edit_size_caps; orderguard.py:218,226 |
| T-03-05-08 | Tampering | view_submission private_metadata tamper | mitigate | closed | server re-fetches ref_price/target from payload_json at submit (routes.py:804-814; slack_handler.py:690-699) |
| T-03-05-09 | Spoofing | CSRF on POST /approvals | mitigate | closed | SameSite=Strict (app.py:248) + CSP `script-src 'self'` (base.html.j2:26); CSRF tokens P6 (AR-09) |
| T-03-05-SC | Tampering | dependency installs | n/a | closed | No new deps |

### Plan 03-06 — daily P&L digest
| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-03-06-01 | Tampering | manipulated NYSE schedule | accept | closed | Accepted-risks log AR-10 (bundled calendar, no remote surface) |
| T-03-06-02 | Info disclosure | P&L DM to leaked Slack account | accept | closed | Accepted-risks log AR-11 (operator is recipient by construction) |
| T-03-06-03 | DoS | unbounded audit-log walk | accept | closed | Accepted-risks log AR-12 (daily window bounds N) |
| T-03-06-04 | Repudiation | operator denies digest | mitigate | closed | daily_pnl.py:439-447 `daily_pnl` audit event w/ payload; walk_chain |
| T-03-06-05 | Spoofing | tamper Slack mention to redirect digest | mitigate | closed | `_send_slack_dm`/`_send_slack_dm_blocks` identity-split seam on settings.slack_user_id |
| T-03-06-06 | EoP | wrong quiet-hours category for daily_pnl | mitigate | closed | AST gate tests/unit/test_quiet_hours_dm_gate.py classifies call sites; daily_pnl.py:339 bypass set |
| T-03-06-07 | Tampering | severity emoji prefix stripped | mitigate | closed | tests/unit/test_severity_tier_dm.py:151-248 asserts ⚠️/❌ prefixes |
| T-03-06-SC | Tampering | dependency installs | n/a | closed | No new deps |

### Plan 03-07 — walking skeleton + README
| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-03-07-01 | Tampering | test fixture leaks creds | accept | closed | Accepted-risks log AR-13 (in-memory SQLCipher, test-only passphrases) |
| T-03-07-02 | Info disclosure | README leaks workspace id | mitigate | closed | README placeholders only (verified 03-07-SUMMARY self-check) |
| T-03-07-03 | Repudiation | operator denies demo run | accept | closed | Accepted-risks log AR-14 (deferred-items.md manual status tracking) |
| T-03-07-SC | Tampering | dependency installs | n/a | closed | No new deps |

### Plan 03-08 — two-router auth gating (CR-01 / D-57)
| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-03-08-01 | EoP | POST /live-confirm (HITL-06) | mitigate | closed | routes.py:135 `router=APIRouter(dependencies=[Depends(require_session)])`; :1783 live_confirm_post on router |
| T-03-08-02 | Tampering | POST /kill,/unkill | mitigate | closed | routes.py:1516,1560 on auth router |
| T-03-08-03 | EoP | POST /promote-to-live | mitigate | closed | routes.py:1662 on auth router + typed-confirm gate :1681 |
| T-03-08-04 | Tampering | POST /trigger (API spend) | mitigate | closed | routes.py:1431 on auth router |
| T-03-08-05 | Info disclosure | GET /live-confirm/{id} detail | mitigate | closed | routes.py:1705 on auth router; require_session → 302 |
| T-03-08-06 | EoP | strategy CRUD | mitigate | closed | routes.py:1213,1361 user_id from require_session; queries filter user_id |
| T-03-08-07 | Spoofing | session cookie forgery | mitigate | closed | app.py:243-250 HMAC-signed SessionMiddleware, ephemeral secret (D-58) |
| T-03-08-SC | Tampering | dependency installs | accept | closed | No new deps |

### Plan 03-09 — audit honesty (CR-02/03/04)
| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-03-09-01 | Info disclosure | fill events expose strategy_name+side | accept | closed | Accepted-risks log AR-15 (encrypted per-user SQLCipher DB; needed for P&L) |
| T-03-09-02 | Repudiation | daily_pnl claims delivery when suppressed | mitigate | closed | daily_pnl.py:446-447 records `delivered`/`suppressed_by_quiet_hours` |
| T-03-09-03 | DoS | expiry DM dropped in quiet hours | mitigate | closed | expiry.py:374-386 category=`executor_error` (bypass set) |
| T-03-09-04 | Repudiation | operator misses expiry, no proof | mitigate | closed | expiry.py:338 expiration event written before DM; DM guaranteed via bypass |
| T-03-09-SC | Tampering | dependency installs | accept | closed | No new deps |

### Plan 03-10 — retry-gate removal, claim_action sole guard (WR-08)
| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-03-10-01 | Tampering | at-least-once → double-exec | mitigate | closed | dedup.py claim_action UNIQUE INSERT (0004:171-183); sole guard |
| T-03-10-02 | Repudiation | dead retry-gate misleads maintainers | mitigate | closed | `_extract_retry_num`/`body["headers"]` removed — grep finds only docstring refs (slack_handler.py:38-44,171,473) |
| T-03-10-03 | Spoofing | crafted retry headers | accept | closed | Accepted-risks log AR-16 (Socket Mode = no HTTP headers; claim_action enforces) |
| T-03-10-SC | Tampering | dependency installs | accept | closed | No new deps |

### Plan 03-11 — edit-size cap gate
| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-03-11-01 | Tampering | _check_edit_size_caps qty param | mitigate | closed | routes.py:739-742 Decimal parse + <=0 reject; actions.py:95-96 |
| T-03-11-02 | Tampering | oversized qty bypass cap | mitigate | closed | actions.py:101-117 new_notional > max_order_notional (server-derived from hard_caps×equity) |
| T-03-11-03 | EoP | edit-size as OrderGuard bypass | mitigate | closed | actions.py absolute cap + orderguard.py:218,226 check_hard_caps + check_qty_price_sanity re-run independently |
| T-03-11-04 | Spoofing | corrupt payload_json permissive caps | mitigate | closed | routes.py:875-908 LIVE fail-closed (reject edit), PAPER fail-open (OrderGuard re-checks) |
| T-03-11-05 | DoS | broker.get_account timeout | mitigate | closed | routes.py:860 `asyncio.wait_for(..., timeout=2.5)`; equity=0 fail-open (OrderGuard still applies) |
| T-03-11-SC | Tampering | dependency installs | accept | closed | No new deps |

### Plan 03-12 — dashboard executor dispatch
| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-03-12-01 | EoP | approve endpoint user_id to executor | mitigate | closed | routes.py:355 user_id from require_session == settings.gekko_user_id (:99-117) |
| T-03-12-02 | Tampering | proposal_id path param | mitigate | closed | routes.py:390-394 WHERE proposal_id AND user_id ownership filter |
| T-03-12-03 | Info disclosure | executor failure in HTMX card | accept | closed | Accepted-risks log AR-17 (card shows status chip only; no raw exception) |
| T-03-12-SC | Tampering | dependency installs | accept | closed | No new deps |

### Plan 03-13 — poll route auth
| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-03-13-01 | Spoofing | /approvals/poll unauth access | mitigate | closed | routes.py:308 on auth router; unauthenticated → 302 /login |
| T-03-13-02 | Info disclosure | proposal data in poll | accept | closed | Accepted-risks log AR-18 (session-scoped to gekko_user_id; single-operator) |
| T-03-13-03 | DoS | poll interval hammers DB | accept | closed | Accepted-risks log AR-19 (30s interval; indexed SELECT; negligible) |
| T-03-13-SC | Tampering | dependency installs | accept | closed | No new deps |

### Plan 03-14 — slider display-only + URL button
| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-03-14-01 | Tampering | edit-submit qty field | mitigate | closed | routes.py:919 `_check_edit_size_caps` + orderguard.py:218,226 two-layer; slider max display-only |
| T-03-14-02 | Tampering | forged negative/zero qty | mitigate | closed | routes.py:741-742 new_qty<=0 reject; actions.py:95-96 |
| T-03-14-03 | Info disclosure | equity in template context | accept | closed | Accepted-risks log AR-20 (dollar number, not credential; redaction strips key/secret_blob) |
| T-03-14-04 | EoP | unauth GET/POST edit-size | mitigate | closed | routes.py:531,711 on auth router (require_session) |
| T-03-14-05 | Tampering | proposal_id in Slack URL | accept | closed | Accepted-risks log AR-21 (opaque UUID; require_session gate; single-operator) |
| T-03-14-SC | Tampering | dependency installs | mitigate | closed | No new packages (all existing source/templates) |

### Plan 03-15 — terminal-state / replay hardening
| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-03-15-01 | Tampering | transition_status in approve/reject/edit | mitigate | closed | routes.py:61-70 `_TERMINAL_STATUSES`; guards at :400,486,979 + defensive ValueError catch :408-411,492-495,1022-1025 → re-render card, no mutation |
| T-03-15-02 | Repudiation | duplicate re-read fresh session edit | mitigate | closed | routes.py:1029-1055 Bug B: duplicate/terminal re-read on FRESH session (sf4); claim_action UNIQUE is dedup primitive |
| T-03-15-03 | EoP | non-HX edit_size_get full page | accept | closed | Accepted-risks log AR-22 (routes.py:685-689 redirect to /approvals; same content, require_session enforced) |
| T-03-15-04 | Info disclosure | tracebacks in 500 responses | mitigate | closed | routes.py terminal guards + ValueError catches eliminate 500 paths; FastAPI default handler sanitizes |
| T-03-15-05 | DoS | repeated terminal clicks → write storm | accept | closed | Accepted-risks log AR-23 (status guard prevents transition; one bounded dedup_click per repeat) |
| T-03-15-SC | Tampering | dependency installs | accept | closed | No new deps |

---

## Threat Flags (from SUMMARY `## Threat Flags`)

All 15 plan summaries report either "None" or an explicit mapping back to existing
`T-03-NN-*` threat IDs. **No unregistered flags** — no net-new attack surface appeared
during implementation that lacks a threat mapping.

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-01 | T-03-01-04 | New User columns (timezone, quiet_hours_*) are lifestyle metadata, not credentials; repr already excludes credential columns | Plan author (03-01) | 2026-06-23 |
| AR-02 | T-03-01-06 | Extreme proposal_timeout = single-operator self-harm (REG-03); hard ceiling deferred to P5 | Plan author (03-01) | 2026-06-23 |
| AR-03 | T-03-02-04 | Ephemeral shows original first-writer id by design (D-43); sent only to duplicate-clicker | Plan author (03-02) | 2026-06-23 |
| AR-04 | T-03-02-05 | Dedup-table storage exhaustion gated by signing secret + cross-user + UNIQUE; bounded growth | Plan author (03-02) | 2026-06-23 |
| AR-05 | T-03-03-03 | strategy_name is a non-sensitive user label visible across the audit trail (D-13) | Plan author (03-03) | 2026-06-23 |
| AR-06 | T-03-03-06 | LLM 24/7-silence override narrows awake-time (safer side); per-strategy override is P6 | Plan author (03-03) | 2026-06-23 |
| AR-07 | T-03-04-03 | Expiry DM contents go to the proposal owner (operator); no cross-user flow | Plan author (03-04) | 2026-06-23 |
| AR-08 | T-03-05-06 | Passphrase brute-force gated by localhost-only binding (D-57/REG-03); rate-limit P6 | Plan author (03-05) | 2026-06-23 |
| AR-09 | T-03-05-09 | CSRF tokens deferred to P6 multi-user; SameSite=Strict + CSP `script-src 'self'` cover single-operator | Plan author (03-05) | 2026-06-23 |
| AR-10 | T-03-06-01 | pandas_market_calendars bundles NYSE calendar locally; no remote attack surface | Plan author (03-06) | 2026-06-23 |
| AR-11 | T-03-06-02 | Daily P&L DM recipient is the operator by construction; Slack account compromise out of scope | Plan author (03-06) | 2026-06-23 |
| AR-12 | T-03-06-03 | Audit aggregation N bounded by daily window (~tens-hundreds in v1 swing scope) | Plan author (03-06) | 2026-06-23 |
| AR-13 | T-03-07-01 | Cassette fixtures use in-memory SQLCipher + test-only passphrases; no real creds | Plan author (03-07) | 2026-06-23 |
| AR-14 | T-03-07-03 | Demo-run repudiation tracked via deferred-items.md manual status field | Plan author (03-07) | 2026-06-23 |
| AR-15 | T-03-09-01 | Fill audit fields (strategy_name, side) needed for P&L attribution; stored in encrypted per-user DB (D-19) | Plan author (03-09) | 2026-06-23 |
| AR-16 | T-03-10-03 | Socket Mode has no HTTP headers; retry gate was advisory only — claim_action is the enforceable guarantee | Plan author (03-10) | 2026-06-23 |
| AR-17 | T-03-12-03 | HTMX card shows status chip only (FAILED/FILLED); no raw exception or credential leak | Plan author (03-12) | 2026-06-23 |
| AR-18 | T-03-13-02 | Poll response session-scoped to gekko_user_id; single-operator (REG-03); no cross-user data | Plan author (03-13) | 2026-06-23 |
| AR-19 | T-03-13-03 | 30s poll interval; single-user SQLite WAL; indexed SELECT; negligible load | Plan author (03-13) | 2026-06-23 |
| AR-20 | T-03-14-03 | Equity is a dollar number (not a credential); same value the operator sees in their broker; redaction strips key/secret_blob | Plan author (03-14) | 2026-06-23 |
| AR-21 | T-03-14-05 | proposal_id is a non-secret opaque UUID; require_session gate ensures only authenticated operator can act | Plan author (03-14) | 2026-06-23 |
| AR-22 | T-03-15-03 | Non-HX edit-size redirects to /approvals (same content, no new capability); require_session enforced either path | Plan author (03-15) | 2026-06-23 |
| AR-23 | T-03-15-05 | Terminal-state clicks: status guard prevents transition; one bounded dedup_click per repeat — not a write storm | Plan author (03-15) | 2026-06-23 |

*Accepted risks do not resurface in future audit runs.*

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-06-23 | 98 | 98 | 0 | gsd-security-auditor (Claude Opus 4.8) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer / n/a-supply-chain)
- [x] Accepted risks documented in Accepted Risks Log (AR-01 … AR-23)
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-06-23
