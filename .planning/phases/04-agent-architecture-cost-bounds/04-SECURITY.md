# SECURITY.md — Phase 4: Agent Architecture & Cost Bounds

**Audit date:** 2026-06-25
**ASVS Level:** L1
**block_on:** high
**Verdict:** SECURED — all `mitigate` threats verified present in code; all `accept` rationales hold.
**Scope calibration:** Single-user / friends-and-family self-hosted tool. No public attack surface; all dashboard routes auth-gated by `require_session`; SQLCipher at rest. Severity calibrated accordingly.

Register origin: authored at plan time. All 8 SUMMARY "Threat Flags" report NO new threats. Job = verify mitigations exist; not a novel-vuln scan.

---

## Mitigate threats — verified present (file:line evidence)

| Threat ID | Category | Evidence |
|-----------|----------|----------|
| T-04-SC (04-01) | Tampering | AST gate `test_decision_never_haiku_model` — tests/unit/test_decision_prompt_isolation.py:317-358 (walks all src/gekko/agent/*.py, fails on `model="haiku"` keyword in any decision-fn) |
| T-04-02 | Tampering | User-scoped columns — db/models.py:212-214; settings route auth-gated via `require_session` router dep — dashboard/routes.py:135; ceiling write filters user_id — routes.py:1203-1213 |
| T-04-04 | Tampering | `_FROZEN_EVENT_TYPES_PRE` literal copy — migrations 0005_p4_cost_ceiling.py:53-77; matches 0004 POST; suite validates frozen vocab |
| T-04-05 | Tampering | Deterministic pre-`query()` Python gate fires BEFORE any query dispatch — runtime.py:623-659 (`check_cost_ceiling` called at :628, halt return at :655). Module is LLM-free: cost_ceiling.py reads DB + Decimal math only (no SDK import) |
| T-04-06 | Tampering | Decision path uses `model="sonnet"` only — decision.py:155; AST gate at test_decision_prompt_isolation.py:317; degradation flag `_degradation_mode` routes to triage/researcher trim only, never `_run_decision` — runtime.py:688, 805-816 |
| T-04-07 | Tampering | `_INJECTION_PATTERNS` regex — runtime.py:154-158; scanned AFTER brief parse, BEFORE `_run_decision` — runtime.py:825-849; logs `suspicious_content` event |
| T-04-10 | Spoofing | `model="haiku"` appears ONLY in `trigger_strategy_run` triage block — runtime.py:722; NOT in `_run_decision` / `build_decision_prompt`; AST gate confirms (decision-fn name patterns at test :67-73 exclude trigger_strategy_run) |
| T-04-11 | Tampering | `Decimal(str(result_msg.total_cost_usd or 0.0))` — runtime.py:362, 467, 745-749 (never `Decimal(float)`); test_cost_usd_is_decimal_not_float — test_cost_ledger.py:85-97 |
| T-04-13 | Tampering | `reschedule_job` (preserves kwargs) — scheduler/jobs.py:282; all registrars `replace_existing=True` — jobs.py:146,193,241; `SQLAlchemyJobStore` — jobs.py:62 |
| T-04-14 | Elevation of Privilege | `/spend` on auth-gated `router` (`Depends(require_session)`) — routes.py:135,1255-1259; all queries filter `EventRow.user_id == user_id` — routes.py:1284,1320,1362 |
| T-04-15 | Tampering | Ceiling validated as Decimal, `<= 0` rejected, stored as `str(ceiling_decimal)` normalized — routes.py:1142-1151, 1197, 1213 |
| T-04-17 | Information Disclosure | `{% extends "base.html.j2" %}` — spend.html.j2:18; NO external script src, NO inline handlers (grep confirms); base carries script-src 'self' CSP + vendored HTMX (SRI gate) |
| T-04-06-01 | Information Disclosure | Router-level `Depends(require_session)` — routes.py:135; spend_get queries filter user_id — routes.py:1284,1320,1362; payload unwrap `payload.get("payload", payload)` touches no scoping — routes.py:1339 |
| T-04-06-02 | Tampering | Inner `inner.get("cost_usd", "0")` → `Decimal(str(...))` — routes.py:1340,1376; no float in unwrap path |
| T-04-07-01 | Tampering | Idempotent repair UPDATE targets exact 6-char over-quoted `'''5.00'''` — migrations 0006_p4_cost_ceiling_repair.py:93-96; clean rows untouched |
| T-04-07-02 | DoS | Defensive try/except → `DEFAULT_DAILY_CEILING_USD` at read sites — routes.py:1099-1106 (settings_get), 1174-1182 (settings_post err), 1233-1241 (settings_post re-read), 1289-1298 (spend_get); mirrors cost_ceiling.py:150-162 |
| T-04-07-03 | Information Disclosure | spend_get ceiling parse wrapped (no 500 → no stack-trace leak) — routes.py:1289-1298 |
| T-04-08-01 | Tampering | `async with session_factory() as session, session.begin():` wraps the sent-date write in explicit txn — cost_ceiling.py:133; rollback only on exception |
| T-04-08-02 | DoS | `session.begin()` commit persists `cost_alert_*_sent_date` — cost_ceiling.py:230-237; same-day second call returns just_crossed=False; integration test test_cost_ceiling_dedup.py:80-166 (real SQLCipher round-trip) |

---

## Accept threats — rationale spot-checked, holds

| Threat ID | Category | Rationale check |
|-----------|----------|-----------------|
| T-04-01 | Tampering | Test infra only; no prod path / PII / creds. HOLDS. |
| T-04-03 | Information Disclosure | pricing.py constants are public Anthropic $/MTok figures; no creds/PII — pricing.py:43-51. HOLDS. |
| T-04-SC (04-02/03/04/05/06/07/08) | Tampering | "No new packages" — Phase 4 reuses existing stack (APScheduler, SQLAlchemy, cryptography). HOLDS. |
| T-04-08 | Information Disclosure | suspicious_content payload = source_type + source_url + pattern_matched + run_id only (no quote_text) — runtime.py:835-841; SQLCipher at rest. HOLDS. |
| T-04-09 | Spoofing | cost_alert DM in same `_BYPASS_CATEGORIES` set as kill_active/executor_error — executor.py:250-253; operator-safety info, same bypass class. HOLDS. |
| T-04-12 | DoS | Haiku triage gated on `if _degradation_mode:` (80-99%), NOT halt(100%) — runtime.py:711; halt returns earlier at runtime.py:655. Haiku ~1/3 Sonnet cost (pricing.py:43-46). HOLDS. |
| T-04-16 | DoS | Ceiling=$0.01 is the user's own intentional choice; raise in Settings to resume. Self-hosted single-user. HOLDS. |
| T-04-06-03 | Information Disclosure | strategy_name read from already-user-scoped payload; unwrap does not widen query — routes.py:1343. HOLDS. |
| T-04-07-04 | Tampering | 0005 server_default edit affects fresh-install only; 0006 repair covers live DB — 0006:83-109. HOLDS. |
| T-04-08-03 | Tampering | Fail-open user-not-found early-returns inside `session.begin()` with no staged mutation → no-op commit — cost_ceiling.py:135-147. HOLDS. |
| T-04-08-04 | Elevation of Privilege | Halt enforcement separate from dedup: runtime.py gates on `_ceiling.action == "halt"` (runtime.py:629) independent of just_crossed flags; un-persisted dedup only affects DM spam, never halt. HOLDS. |

---

## Unregistered flags

None. All 8 SUMMARY "Threat Flags" sections report no new attack surface beyond the plan registers.

---

## Summary

- **Mitigate threats:** 19/19 verified present with file:line evidence.
- **Accept threats:** 13/13 rationales spot-checked and hold.
- **Open / BLOCKER:** 0.
- **Implementation files:** unmodified (read-only audit).

Phase 4 is cleared to ship under ASVS L1 / block_on=high.
