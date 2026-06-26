# Phase 5: Trust Ladder (Per-Strategy Promotion & Portfolio Caps) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-26
**Phase:** 05-trust-ladder-per-strategy-promotion-portfolio-caps
**Areas discussed:** Promotion gate criteria, Portfolio-level caps, Anomaly auto-demotion, Capital scaling rungs

---

## Promotion gate criteria

### Clean approvals required
| Option | Description | Selected |
|--------|-------------|----------|
| 10 clean approvals | ~1–2 weeks of clean operation for swing-horizon strategies | ✓ |
| 5 clean approvals | Faster to autonomy; less track record | |
| 20 clean approvals | Conservative; ~3–4 weeks | |

### Disqualifier ("no cap breaches")
| Option | Description | Selected |
|--------|-------------|----------|
| No cap-breach in qualifying window | Clean streak; any `cap_rejection` resets/blocks | ✓ |
| No cap-breach ever | A single historical breach blocks until manually cleared | |
| Approvals only; breaches don't block | Weakest gate | |

### Paper/live interaction
| Option | Description | Selected |
|--------|-------------|----------|
| Per-mode; auto on paper AND live independently | Live+auto stacks Phase-2 live promotion + first-live gate | ✓ |
| Auto only after live promotion | Trust ladder is live-money-only | |
| Single mode-agnostic record | Blurs the paper/live safety boundary | |

### Promote/demote surface
| Option | Description | Selected |
|--------|-------------|----------|
| Dashboard + CLI, no Slack promote | Matches Phase-2 D-31 | ✓ |
| Add a Slack promote command | Breaks the "no casual mobile promote" principle | |
| Dashboard only | No CLI parity | |

### Trust state on strategy edit
| Option | Description | Selected |
|--------|-------------|----------|
| Material edits (watchlist/hard_caps) reset to propose-only + restart streak | Trust earned per-configuration; thesis-only edits don't reset | ✓ |
| Any edit resets to propose-only | Simplest/most conservative | |
| Edits never reset trust | Most convenient, weakest safety | |

**User's choice:** All recommended options.
**Notes:** Confirms the three-axis model (paper/live × propose-only/auto × capital ladder) and that material-edit trust reset keys off D-05 snapshot versioning.

---

## Portfolio-level caps

### Which caps ship (multi-select)
| Option | Description | Selected |
|--------|-------------|----------|
| Max total exposure (% equity, all strategies) | Headline portfolio cap (SC-2) | ✓ |
| Max sector concentration (all strategies) | Aggregate sector exposure across strategies | ✓ |
| Max correlated-strategy exposure | Needs pragmatic definition | ✓ |
| Max total daily loss (USD, all strategies) | Portfolio-wide circuit breaker | ✓ |

### "Correlated-strategy" definition
| Option | Description | Selected |
|--------|-------------|----------|
| Same-ticker overlap across strategies | Cheap, deterministic, explainable | ✓ |
| Treat correlated = same-sector | Alias for sector cap | |
| Defer to a later phase | Ship total + sector only | |

### Scope of portfolio caps
| Option | Description | Selected |
|--------|-------------|----------|
| All orders (HITL + auto) | "Portfolio is portfolio"; uniform in OrderGuard | ✓ |
| Only auto-within-caps orders | HITL trades bypass | |

### Config location
| Option | Description | Selected |
|--------|-------------|----------|
| User-level Settings with defaults | Alongside cost ceiling + quiet hours | ✓ |
| Hardcoded defaults, not editable | Less work; no runtime tuning | |
| Per-strategy opt-in | More complex; weakens guarantee | |

**User's choice:** All four caps + same-ticker correlation + all-orders scope + user-level Settings.
**Notes:** Exact default cap numbers left to researcher/planner discretion.

---

## Anomaly auto-demotion

### Drawdown metric
| Option | Description | Selected |
|--------|-------------|----------|
| Single-day drawdown | Fastest signal for sudden runaway-loop; matches daily-loss data | ✓ |
| Since-promotion peak-to-trough | Needs high-water-mark tracking; slower | |
| Rolling N-day drawdown | Smooths noise; reacts slower to a one-day blowup | |

### Threshold + configurability
| Option | Description | Selected |
|--------|-------------|----------|
| Per-strategy, default 10% single-day | Early-warning before the hard daily-loss cap | ✓ |
| Per-strategy, default 5% | More twitchy; more false positives | |
| Per-strategy, default 15% | More tolerant; larger loss before firing | |

### Cancel/halt scope
| Option | Description | Selected |
|--------|-------------|----------|
| Cancel this strategy's pending auto-orders + demote to propose-only | Surgical; strategy keeps running under HITL | ✓ |
| Cancel + fully halt the strategy | Strategy goes dark until re-promoted | |
| Cancel + trip portfolio-wide review | Demote all auto strategies | |

### DM quiet-hours behavior
| Option | Description | Selected |
|--------|-------------|----------|
| Bypass quiet hours | Operator-safety-critical, same tier as kill/cap-rejection | ✓ |
| Respect quiet hours | Treats it as informational | |

**User's choice:** All recommended options.
**Notes:** Anomaly threshold is explicitly a separate, lower trip than `max_daily_loss_usd` — removes autonomy before the hard cap halts trading.

---

## Capital scaling rungs

### Rung shape
| Option | Description | Selected |
|--------|-------------|----------|
| Arbitrary USD ceiling; any increase needs fresh confirmation | Lowering is free; no artificial rungs | ✓ |
| Fixed ladder ($1K → $10K → $100K) | Rigid named rungs | |
| Multiplier-based rungs | Same rigidity, relative | |

### Enforcement representation
| Option | Description | Selected |
|--------|-------------|----------|
| New per-strategy absolute USD capital ceiling in OrderGuard | Caps total deployed capital; stacks with max_position_pct | ✓ |
| Reinterpret existing caps; no new field | Conflates concentration with capital | |
| Capital limit = max notional per order | Doesn't bound cumulative exposure | |

### Starting rung on first auto-promotion
| Option | Description | Selected |
|--------|-------------|----------|
| $1,000 default | Matches SC-3 + "start small dollars" | ✓ |
| $500 default | More conservative | |
| No default — user sets at promotion | One more required input | |

### Trust impact of scaling
| Option | Description | Selected |
|--------|-------------|----------|
| Separate rung: confirm + audit, trust untouched | Two independent ladders | ✓ |
| Scaling up also resets to propose-only | Heavy friction (another 10-approval streak) | |
| No confirmation, just a setting | Violates SC-3 | |

**User's choice:** All recommended options.

---

## Auto-execution review surface (follow-up)

| Option | Description | Selected |
|--------|-------------|----------|
| Real-time informational DM (no buttons) + daily digest | Keeps operator in the loop; respects quiet hours | ✓ |
| Daily digest only | Quieter; review once a day | |
| Explore more gray areas instead | — | |

**User's choice:** Real-time informational DM + digest. Signaled ready for context.

---

## Claude's Discretion

- State representation (trust columns on `StrategyMetadata` + Alembic migration).
- Clean-approval streak counting from the `events` log + window-reset mechanics.
- Exact default numbers for the four portfolio caps.
- Auto-execute branch insertion point in `trigger_strategy_run` → `execute_proposal`.
- Anomaly evaluation cadence (post-fill vs scheduler tick).
- New audit `event_type` values vs reusing existing ones.
- A safety-invariant AST/test gate for "auto-execute requires met criteria".

## Deferred Ideas

- Market-data correlation engine (using same-ticker overlap instead).
- Email digests (Phase 6).
- Portfolio-wide anomaly cascade (kept demotion surgical).
- Fixed/multiplier capital rungs (using arbitrary ceiling).
