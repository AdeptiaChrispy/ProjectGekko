# Project State: Project Gekko

**Last updated:** 2026-06-08

## Project Reference

**Core Value:** A trustworthy autonomous agent that turns a plain-English investment thesis into actual, monitored trades on the user's own brokerage account — starting human-in-the-loop with small dollars and graduating to autonomy as trust is earned.

**Current Focus:** Phase 1 — Foundation & Vertical Slice (Alpaca Paper + Slack HITL)

## Current Position

- **Phase:** 1 (Foundation & Vertical Slice)
- **Plan:** Not yet planned
- **Status:** Roadmap complete; awaiting `/gsd-plan-phase 1`
- **Progress:** Phase 0 / 9 phases complete (0%)

```
[..................] 0%
```

## Performance Metrics

| Metric | Value |
|--------|-------|
| Phases planned | 9 |
| Phases complete | 0 |
| v1 requirements mapped | 78 / 78 |
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

- [ ] User to approve roadmap (or provide revision feedback)
- [ ] Run `/gsd-plan-phase 1` to decompose Phase 1 into executable plans
- [ ] Resolve "wash-sale default" decision before Phase 2 plan-phase
- [ ] Resolve "default LLM cost ceiling" value before Phase 4 plan-phase
- [ ] Resolve "trust ladder promotion criteria" placeholder before Phase 5 plan-phase

### Blockers

None.

## Session Continuity

**Next action:** `/gsd-plan-phase 1` to decompose Foundation & Vertical Slice into executable plans.

**Resumable from:** This document + ROADMAP.md + REQUIREMENTS.md + research/ provide full context for any agent to pick up the work.

---
*State initialized: 2026-06-08 after roadmap creation*
