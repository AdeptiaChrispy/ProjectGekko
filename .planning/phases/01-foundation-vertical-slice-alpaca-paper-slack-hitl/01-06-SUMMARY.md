---
phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl
plan: 06
subsystem: schemas
tags: [pydantic, strategy, research-brief, trade-proposal, no-action, event-payload, discriminated-union, plain-english-diff, snapshot-versioning, d-01, d-02, d-05, d-08, d-10, d-11, d-12, d-14, d-15, strat-03, strat-04, strat-05, strat-06, res-08, rept-04, forward-compat]
requires:
  - phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl
    plan: 03
    provides: |
      gekko.db.models.Strategy ORM row (D-05 snapshot-row versioning columns
      strategy_id / user_id / strategy_name / version / payload_json /
      created_at + UniqueConstraint on (user_id, strategy_name, version)) —
      next_version() queries SELECT MAX(version) here; the Pydantic
      Strategy.model_dump_json() output is what callers persist into the
      payload_json column.
  - phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl
    plan: 04
    provides: |
      gekko.audit.canonical.normalize_decimals — Plans 01-07 / 01-08 MUST call
      it on any TradeProposal / NoActionProposal / fill payload BEFORE
      append_event. EventPayload is the typed write-site validator; the
      audit-log writer itself still accepts a plain dict (Plan 01-04 contract
      unchanged).
  - phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl
    plan: 05
    provides: |
      gekko.core.types.OrderSide / OrderType / TimeInForce StrEnums (used as
      TradeProposal field types — single source of truth for the order
      vocabulary); gekko.core.ids.compute_client_order_id (Plan 01-07's
      ProposalWriter computes this and embeds it in TradeProposal.client_order_id,
      which the schema enforces as min=max=32 chars matching the sha256[:32]
      hex output).
provides:
  - "gekko.schemas.strategy — Strategy + HardCaps + Guidance Pydantic models (D-01, D-05, D-08, STRAT-03..06, RES-08). HardCaps carries the defensive le=Decimal('0.20') ceiling on max_position_pct per RESEARCH §Code Examples. schedule_time validated via zoneinfo.ZoneInfo (tzdata dep). watchlist field_validator upper-cases + strips + deduplicates preserving first-seen order. mode Literal['paper','live'] default 'paper' per D-24 / STRAT-06 — UI confirmation for paper→live flip is enforced by Plan 01-09, not the schema."
  - "gekko.schemas.strategy.next_version(session, *, user_id, strategy_name) -> int — D-05 snapshot-row versioning helper: SELECT MAX(version) FROM strategies WHERE user_id=:uid AND strategy_name=:sn, returns max+1 (or 1 if none)."
  - "gekko.schemas.diff — compute_field_changes(before, after) -> dict[str, Any] + generate_strategy_diff(before, after) -> str. Deterministic Python implementation per RESEARCH §'Don't Hand-Roll' (P6 may replace with LLM-generated prose). Plain-English form per D-02 ('You changed max-position from 5% to 7% and added MSFT, GOOGL to the watchlist.'); percent formatting for pct caps via Decimal.normalize + format(.,'f'); add/remove sets for watchlist; schedule add/remove/change variants; mode flip + thesis-edit + name-rename sentinels."
  - "gekko.schemas.research — ResearchBrief (D-10 load-bearing Researcher → Decision contract) with model_config = ConfigDict(extra='allow') for P4 forward-compat (RESEARCH §Pattern 2); EvidenceSnippet with Literal source_type allowlist {alpaca_quote, finnhub_news, edgar_filing, web_fetch} and HttpUrl source_url + quote_text untrusted-content channel; TickerSnapshot with upper-cased ticker + Decimal last_price/bid/ask. research_budget_used kept as dict[str, Any] (not sub-model) so P4 can extend keys without re-versioning."
  - "gekko.schemas.proposal — TradeProposal + NoActionProposal + AlternativeConsidered (D-11, D-12, REPT-04). TradeProposal enforces evidence Field(min_length=3, max_length=5), alternatives_considered Field(min_length=1), confidence Decimal[0,1], qty Decimal>0, client_order_id Field(min_length=32, max_length=32), side OrderSide enum, order_type OrderType enum, model_validator couples LIMIT→limit_price and STOP→stop_price (market orders accept limit_price field informationally — executor reads order_type, not which prices are set). NoActionProposal enforces factors_considered Field(min_length=1) + confidence Decimal[0,1]. Both use extra='ignore' for forward-compat."
  - "gekko.schemas.event — typed EventPayload helpers covering the D-14 vocabulary as a Pydantic v2 discriminated union via Discriminator(callable) + Annotated[..., Tag(value)]. 9 variants: decision / proposal / approval / rejection / order_submitted / fill / kill_switch / cap_rejection / error. Callers in Plans 01-07 / 01-08 use this at write site for type safety; append_event itself still accepts plain dict so Plan 01-04's API contract is unchanged."
  - "gekko.schemas package re-exports the full surface so callers can write `from gekko.schemas import Strategy, TradeProposal, EventPayload`."
affects:
  - 01-07 (agent runtime — Researcher emits ResearchBrief; Decision emits TradeProposal OR NoActionProposal via SDK tool-use; ProposalWriter validates Pydantic + computes compute_client_order_id + sets client_order_id field + calls normalize_decimals(payload) + append_event('proposal', payload). The Decision agent's tool input_schema MUST mirror TradeProposal — single source of truth.)
  - 01-08 (Slack + executor — approval handler emits ApprovalEventPayload / RejectionEventPayload at the write site; executor emits OrderSubmittedEventPayload + FillEventPayload; reporter renders Block Kit from TradeProposal model)
  - 01-09 (CLI + dashboard — strategy create/save calls next_version(); strategy edit page uses generate_strategy_diff to render the D-02 plain-English diff; `gekko audit verify --detailed` (P3 polish) could use EventPayload TypeAdapter to deserialize and pretty-print payloads)
tech-stack:
  added: []
  patterns:
    - "Forward-compat by ConfigDict(extra='allow') on ResearchBrief (Researcher→Decision boundary; P4 hardening adds optional fields without breaking) vs. ConfigDict(extra='ignore') on TradeProposal/NoActionProposal (audit-log write boundary; unknown extras silently dropped rather than persisted). Different config choices for different boundary semantics."
    - "Pydantic v2 discriminated union via Discriminator(callable) + Annotated[..., Tag(value)]: extract the discriminator from dict-or-model uniformly via a small callable. Cleaner than the legacy `discriminator=` field-name shorthand when the input might be a dict (Plan 01-07's SDK tool-call output) or a Pydantic instance (Plan 01-08's constructed payload)."
    - "Deterministic plain-English diff (D-02): walk HardCaps + scalar fields + watchlist-as-sets; format pct caps via Decimal.normalize + format(.,'f') so 5.00% renders as 5% and 7.50% as 7.5%; join sentences with comma+and per English convention. RESEARCH §'Don't Hand-Roll' notes the LLM-generated path is also acceptable but P6 can switch in if needed."
    - "Snapshot-row versioning via next_version() — pure read function, callers own the subsequent INSERT and any BEGIN IMMEDIATE serialization. The (user_id, strategy_name) scoping comes from the SQL WHERE; the UniqueConstraint in models.py is the database-side belt-and-braces."
    - "TradeProposal.client_order_id schema strictness (min=max=32) is the LAST gate that catches drift between gekko.core.ids.compute_client_order_id (sha256[:32]) and the persisted proposal row. Plan 01-07's ProposalWriter computes the id, sets it on the row, and embeds it in the model — Pydantic validation catches any inconsistency."
    - "EvidenceSnippet field-trust convention: source_type + summary are TRUSTED (Researcher-authored, inside trust boundary); quote_text is the ONLY untrusted-content channel. P4's prompt-injection defense wraps quote_text in <UNTRUSTED>...</UNTRUSTED> at the Decision-agent prompt boundary — the schema preserves bytes verbatim; sanitization is a prompt-layer concern."
key-files:
  created:
    - src/gekko/schemas/strategy.py
    - src/gekko/schemas/diff.py
    - src/gekko/schemas/research.py
    - src/gekko/schemas/proposal.py
    - src/gekko/schemas/event.py
    - tests/unit/test_strategy_schema.py
    - tests/unit/test_strategy_diff.py
    - tests/unit/test_strategy_versioning.py
    - tests/unit/test_research_brief_schema.py
    - tests/unit/test_proposal_schema.py
  modified:
    - src/gekko/schemas/__init__.py (re-export the full Plan 01-06 surface)
key-decisions:
  - "Strategy.mode is Literal['paper','live'] default 'paper'. The schema accepts both — UI confirmation for the paper→live flip is enforced by Plan 01-09 dashboard, NOT the schema. Schema-layer rejection of mode='live' would couple Pydantic and UI logic; STRAT-06 explicitly requires UI confirmation, not validation rejection."
  - "HardCaps.max_position_pct carries a defensive ceiling of le=Decimal('0.20') (20%). Per RESEARCH §'Code Examples', concentrating more than 20% in a single position is an architectural smell; schema rejection catches it at validation time before it reaches Plan 02's OrderGuard. The other three caps use Pydantic's gt/ge bounds without a defensive upper limit (callers know their own risk tolerance)."
  - "ResearchBrief uses ConfigDict(extra='allow') for P4 forward-compat — the load-bearing forward-compatibility mechanism per RESEARCH §'Pattern 2'. P4 hardening will add `injected_content_flags`, `source_allowlist_violations`, `sanitization_applied` as additional optional fields; ConfigDict(extra='allow') stashes them in model_extra rather than rejecting. research_budget_used kept as dict[str, Any] (not sub-model) so P4 can extend its keys without re-versioning the brief schema."
  - "TradeProposal + NoActionProposal use extra='ignore' (NOT 'allow'). Different from ResearchBrief: TradeProposal sits AT the audit-log write boundary (D-15 says payload_json IS this model_dump). Allowing unknown extras into the persisted JSON would make the audit-log payload shape less predictable; 'ignore' keeps the canonical form clean while still tolerating older deserialized rows."
  - "generate_strategy_diff is deterministic Python, NOT LLM-generated for P1. Per RESEARCH §'Don't Hand-Roll', the LLM-generated diff path is acceptable but the deterministic implementation is simpler for P1 and the API surface (`compute_field_changes` + `generate_strategy_diff`) is stable. P6 may replace with LLM-generated prose if Chris wants richer narratives."
  - "EvidenceSnippet.quote_text is THE untrusted-content channel; source_type + summary are trusted. P4 prompt-injection defense wraps quote_text in <UNTRUSTED>...</UNTRUSTED> at the Decision-agent prompt boundary. The Pydantic schema preserves the bytes verbatim — sanitization is a prompt-layer concern, not a schema concern. Documented in EvidenceSnippet's class docstring."
  - "EventPayload uses Pydantic v2's Discriminator(callable) + Annotated[..., Tag(value)] union pattern rather than the legacy `discriminator='event_kind'` shorthand. Reason: callers pass either dicts (Plan 01-07 SDK tool-call output) or constructed Pydantic models (Plan 01-08); a callable discriminator extracts the kind uniformly. EventPayload is enforced at the WRITE site (Plans 01-07 / 01-08 use TypeAdapter(EventPayload).validate_python(payload) before append_event); append_event itself still accepts plain dict, so Plan 01-04's contract is unchanged."
  - "TradeProposal.client_order_id is Field(min_length=32, max_length=32) — exactly the sha256[:32] output. The schema strictness is the LAST gate that catches drift between gekko.core.ids.compute_client_order_id and the persisted proposal row. Any mismatch raises ValidationError at Plan 01-07's ProposalWriter."
  - "TradeProposal.evidence Field(min_length=3, max_length=5) and alternatives_considered Field(min_length=1) are the D-12 differentiator — the one-shot architectural decision per CONTEXT.md §'specifics'. Cannot be retrofitted from free-form prose; the schema rejection is what makes the v2 retrospective dashboard possible. Plan 01-07's Decision agent gets a structured re-prompt loop on failure, not a silent accept."
  - "next_version() is a pure READ function — callers own the subsequent INSERT and any BEGIN IMMEDIATE serialization. P1 has a single user per deployment (D-21), so concurrent writers within the same user_id are improbable; if Phase 4 / Phase 7 introduce them, the caller adds the txn isolation, not next_version."
  - "EventPayload covers all 9 D-14 event_type values including kill_switch (P-future) and cap_rejection (P2 OrderGuard) with minimal P1 shapes. Forward-compat: each variant uses extra='ignore' so P2-Pn can add fields to existing variants without breaking P1-persisted payloads."
patterns-established:
  - "Pattern: extra='allow' on the Researcher→Decision boundary (ResearchBrief) for P4 forward-compat — unknown future fields are PRESERVED. extra='ignore' on the audit-log boundary (TradeProposal, NoActionProposal, EventPayload variants) — unknown future fields are DROPPED but don't cause validation failure. The choice depends on whether the unknown bytes should round-trip or simply not break."
  - "Pattern: Pydantic v2 discriminated union via Discriminator(callable) + Annotated[..., Tag(value)] when the input might be either dict or Pydantic instance. The callable handles both shapes uniformly via isinstance(dict) + getattr fallback."
  - "Pattern: snapshot-row versioning via next_version(session, *, user_id, strategy_name) -> int. Pure read; callers own the INSERT. Reusable for any future snapshot-row table (e.g., a future guidance-versioning history)."
  - "Pattern: deterministic plain-English diff for snapshot-shaped data structures. Walk shared fields + dict-key sets for collections; format percentages via Decimal.normalize + format(.,'f') for trailing-zero cleanup; join with comma+and per English convention. The function returns 'No changes.' on equality so callers don't need to special-case the empty diff path."
  - "Pattern: schema-layer client_order_id strictness (min=max=32) as the LAST gate against drift between the id-computing function and the persisted row. Reusable for any future deterministic-id pattern where the function output's length is stable."
  - "Pattern: untrusted-content channel labeling at the schema layer. EvidenceSnippet.quote_text is documented as the only untrusted field; P4's prompt-injection defense layers atop. Future schemas with externally-sourced content should follow the same single-field convention so the prompt boundary has exactly one wrapper site."
requirements-completed:
  - STRAT-04
  - STRAT-05
  - STRAT-06
  - REPT-04
  - RES-08
metrics:
  duration_minutes: 25
  completed: "2026-06-09T14:00:00Z"
---

# Phase 01 Plan 06: Pydantic Schema Contracts Summary

**The locked Wave-1 Pydantic shapes — Strategy + HardCaps + Guidance (D-01, STRAT-03..06, RES-08), ResearchBrief + EvidenceSnippet + TickerSnapshot (D-10 Researcher→Decision contract, P4 forward-compat via `extra='allow'`), TradeProposal + NoActionProposal + AlternativeConsidered (D-11 + D-12 structured-rationale differentiator), EventPayload discriminated union over the D-14 vocabulary, plain-English `generate_strategy_diff` per D-02, and snapshot-row `next_version()` per D-05 — landed across three TDD task pairs with 88 new unit tests + ruff + mypy --strict clean across 37 src files.**

## Performance

- **Duration:** ~25 min (~13:46 → ~14:00 UTC on 2026-06-09)
- **Tasks:** 3 (all `tdd="true"`, so each got separate RED + GREEN commits = 6 task commits)
- **Files created:** 10 (5 src/gekko/schemas, 5 tests/unit)
- **Files modified:** 1 (src/gekko/schemas/__init__.py — re-export surface)
- **Tests added:** 88 (40 strategy/diff/versioning + 23 research + 25 proposal/event); unit suite now 238 tests passing
- **Plans completed:** 6 of 9 (Wave 1 closed)

## Accomplishments

- **STRAT-04 closed.** `next_version()` returns max+1 (or 1 if none) scoped by (user_id, strategy_name) — the snapshot-row versioning helper Plan 01-09 wires into the strategy create/save flow. `generate_strategy_diff()` provides the plain-English D-02 diff ("You changed max-position from 5% to 7% and added MSFT, GOOGL to the watchlist.")
- **STRAT-05 closed.** Strategy carries `(user_id, name, version)` as the snapshot key; the existing UniqueConstraint in `gekko.db.models.Strategy` enforces uniqueness across multiple named strategies per user. Plan 01-09 wires per-strategy APScheduler jobs.
- **STRAT-06 closed.** `Strategy.mode` is `Literal["paper", "live"]` default `"paper"` per D-24. The schema accepts both; Plan 01-09 dashboard enforces the explicit-confirmation UI step for the paper→live flip.
- **REPT-04 closed.** `TradeProposal` carries the D-12 structured-rationale invariants at the schema layer: `evidence: list[EvidenceSnippet]` with `Field(min_length=3, max_length=5)`, `alternatives_considered: list[AlternativeConsidered]` with `Field(min_length=1)`, `confidence: Decimal[0, 1]`, `client_order_id` exactly 32 chars. D-15 says the audit log's `payload_json` for `event_type='proposal'` IS this model_dump — so the schema's validation IS the rationale-capture gate.
- **RES-08 closed.** `Guidance(text, scope, expires_at, ...)` Pydantic model captures the structured guidance record (timestamp, scope, expiry) per the RES-08 acceptance criterion. Plan 01-07's Researcher prompt will inject active (non-expired) Guidance rows.
- **ResearchBrief forward-compat live.** `model_config = ConfigDict(extra="allow")` + `research_budget_used: dict[str, Any]` — verified by `test_forward_compat_unknown_field_accepted`: passing `injected_content_flags`, `source_allowlist_violations`, `sanitization_applied`, and an arbitrary `future_field_p4` all deserialize without error. The brief schema is additive for P4 hardening as RESEARCH §Pattern 2 requires.
- **EventPayload discriminated union live.** All 9 D-14 event_type variants (decision, proposal, approval, rejection, order_submitted, fill, kill_switch, cap_rejection, error) validate via `TypeAdapter(EventPayload).validate_python(...)` with discriminator-callable extracting `event_kind`. Unknown kinds raise `ValidationError`.
- **All gates green:** 238 unit tests pass (150 prior + 88 new); ruff + mypy --strict clean across 37 source files.

## Task Commits

Three TDD pairs (RED commit then GREEN commit per the plan's `tdd="true"` invariant):

1. **Task 1 RED** — failing tests for Strategy schema + HardCaps + Guidance + diff + versioning — `5339c86` (test)
2. **Task 1 GREEN** — `strategy.py` + `diff.py` + `next_version()` + `__init__.py` re-exports — `ce0f65a` (feat)
3. **Task 2 RED** — failing tests for ResearchBrief + EvidenceSnippet + TickerSnapshot — `1af6098` (test)
4. **Task 2 GREEN** — `research.py` + `__init__.py` extension — `da19736` (feat)
5. **Task 3 RED** — failing tests for TradeProposal + NoActionProposal + EventPayload — `ed964b5` (test)
6. **Task 3 GREEN** — `proposal.py` + `event.py` + `__init__.py` extension — `9b9f818` (feat)

## Files Created (10)

### Source layer (5)

- `src/gekko/schemas/strategy.py` — `HardCaps` (bounded Decimal/int knobs with the defensive `le=Decimal("0.20")` ceiling on max_position_pct), `Strategy` (D-01 minimal v1 fields with `watchlist` upper+strip+dedupe normalizer, `schedule_time` IANA-tz validator via `zoneinfo.ZoneInfo`, `mode` Literal default 'paper' per STRAT-06, `created_by_chat` provenance, `thesis` Field(min_length=1, max_length=2000)), `Guidance` (STRAT-03 / RES-08 scope Literal['strategy','global'] + expires_at), `next_version(session, *, user_id, strategy_name)` D-05 helper
- `src/gekko/schemas/diff.py` — `compute_field_changes(before, after)` + `generate_strategy_diff(before, after)` per D-02; deterministic Python implementation (RESEARCH §'Don't Hand-Roll'); percent formatting for pct caps, USD formatting for dollar caps, watchlist add/remove sets, thesis-edit sentinel, schedule add/remove/change variants
- `src/gekko/schemas/research.py` — `EvidenceSnippet` (Literal source_type allowlist, HttpUrl source_url, relevance_score Decimal[0,1], quote_text untrusted channel), `TickerSnapshot` (ticker upper+strip), `ResearchBrief` (D-10 load-bearing brief with `model_config = ConfigDict(extra="allow")` for P4 forward-compat, research_budget_used as `dict[str, Any]` for P4 extensibility)
- `src/gekko/schemas/proposal.py` — `AlternativeConsidered`, `TradeProposal` (D-11/D-12 with evidence 3-5, alternatives ≥1, confidence 0-1, client_order_id min=max=32, side OrderSide enum, order_type OrderType enum, model_validator coupling LIMIT→limit_price + STOP→stop_price), `NoActionProposal` (factors_considered ≥1, confidence 0-1), `Proposal = TradeProposal | NoActionProposal` union alias
- `src/gekko/schemas/event.py` — 9 typed payload classes (Decision / Proposal / Approval / Rejection / OrderSubmitted / Fill / KillSwitch / CapRejection / Error) + `EventPayload` discriminated union via Pydantic v2's `Discriminator(_extract_event_kind)` + `Annotated[..., Tag(value)]`

### Tests (5)

- `tests/unit/test_strategy_schema.py` — 26 tests covering HardCaps bounds (positive, defensive ceiling, unit interval), Strategy field validation (watchlist normalize+dedupe, mode paper/live/invalid+default, schedule_time IANA-tz, thesis bounds, created_by_chat), Guidance (scope, expires_at)
- `tests/unit/test_strategy_diff.py` — 10 tests covering compute_field_changes (no-change, pct change, watchlist add/remove, thesis change, schedule add) and generate_strategy_diff (no-change sentence, pct in diff, watchlist add prose, thesis edit prose, schedule add prose)
- `tests/unit/test_strategy_versioning.py` — 4 tests covering next_version (first save = 1, increments after each save, scoped by user+strategy, payload_json round-trips via model_validate_json)
- `tests/unit/test_research_brief_schema.py` — 23 tests covering EvidenceSnippet (allowlist parametrized + URL accept/optional + relevance bounds + quote_text passthrough), TickerSnapshot (uppercased, bid/ask optional), ResearchBrief (forward-compat extra field acceptance, evidence 0-10, tickers_examined 0-20, dict budget extensibility, JSON round-trip)
- `tests/unit/test_proposal_schema.py` — 25 tests covering AlternativeConsidered, TradeProposal (3-5 evidence + ≥1 alternative + buy/sell side + qty>0 + confidence 0-1 + limit requires limit_price + stop requires stop_price + market keeps limit_price field + client_order_id 32 chars + extra='ignore' + Decimal serialization), NoActionProposal (factors ≥1, confidence 0-1), EventPayload (8 variant validations + unknown_kind rejection)

## Files Modified (1)

- `src/gekko/schemas/__init__.py` — re-exports the full Plan 01-06 public surface so callers can write `from gekko.schemas import Strategy, ResearchBrief, TradeProposal, EventPayload, generate_strategy_diff, next_version` rather than reaching into the submodules.

## Plan `<output>` block answers

The plan asked the executor to record three things in this SUMMARY:

1. **Forward-compat fields earmarked for P4.** Documented in `src/gekko/schemas/research.py`'s `ResearchBrief` docstring:
   - `injected_content_flags: list[str]` — "fields P4's source-allowlist enforcement will set when it detects a flagged piece of untrusted content"
   - `source_allowlist_violations: list[str]` — "the URLs/domains P4 saw but refused to fetch"
   - `sanitization_applied: bool` — "whether P4 wrapped quote_text in `<UNTRUSTED>` markers before the Decision agent saw it"

   These are NOT in P1's schema — they're explicitly P4's scope. The `extra='allow'` config preserves them in `model_extra` when P4 starts emitting them; P1-persisted briefs round-trip cleanly because the additions are additive.

2. **EventPayload enforcement at write vs. read time.** **Recommendation: enforce at the write site in Plans 01-07 and 01-08.** The pattern is:

   ```python
   from pydantic import TypeAdapter
   from gekko.schemas.event import EventPayload, FillEventPayload

   adapter = TypeAdapter(EventPayload)
   payload = FillEventPayload(client_order_id=..., broker_order_id=..., filled_qty=..., filled_avg_price=..., ticker=...)
   # Validate (raises on shape drift before persistence):
   adapter.validate_python(payload.model_dump())
   # Persist:
   await append_event(session, user_id=..., strategy_id=..., event_type="fill", payload=payload.model_dump())
   ```

   `append_event` itself still accepts plain dict (Plan 01-04's contract), so the typed validation is opt-in at the caller. The reason to enforce at write time: catching shape drift before the row is persisted is cheap; catching it at read time (Plan 01-09's `gekko audit verify --detailed`) means corrupted rows are already in the DB. The cost is a few microseconds per write — well worth it.

3. **Plain-English strategy diff: deterministic Python (chosen).** Per RESEARCH §'Don't Hand-Roll', both deterministic Python and LLM-generated prose are acceptable. The deterministic path:
   - is faster (microseconds vs. a Claude API call)
   - has stable output (the same before/after always produces the same string)
   - has no API spend
   - has obvious test coverage

   P6 may replace with LLM-generated prose if Chris wants richer narratives (e.g., the LLM could write "You loosened your position concentration limit by 2 percentage points and added two tech-mega-caps to your watchlist."). The function signature `generate_strategy_diff(before, after) -> str` is stable; swapping the implementation is a Plan-P6 internal refactor.

## Decisions Made

See frontmatter `key-decisions` for the full 11-item list. The four most consequential:

1. **`Strategy.mode` is the schema-layer flag; UI confirmation is Plan 01-09's job.** STRAT-06 explicitly requires "flipping live requires an explicit confirmation step" — that's a UI invariant, not a Pydantic invariant. The schema accepts both modes; the dashboard's form submission step is the confirmation.

2. **`ResearchBrief` extra='allow', `TradeProposal` extra='ignore'.** Different boundary semantics — the brief is the Researcher→Decision contract (P4 will add fields; preserve them in `model_extra`), the proposal is the audit-log persistence contract (D-15: payload_json IS this model_dump; unknown extras would muddy the canonical form).

3. **Deterministic plain-English diff (NOT LLM-generated for P1).** Per RESEARCH §'Don't Hand-Roll'. The function signature is stable; P6 may swap the implementation.

4. **`TradeProposal.client_order_id` schema-level strictness (min=max=32 chars).** The LAST gate against drift between `gekko.core.ids.compute_client_order_id` (sha256[:32]) and the persisted proposal row. Plan 01-07's ProposalWriter computes the id, sets it on the row, AND embeds it in the model — any inconsistency raises ValidationError before persistence.

## Deviations from Plan

### Auto-fixed during execution

**1. [Rule 1 — Lint] ruff I001 (import sort) + UP017 (datetime.UTC alias).**

- **Found during:** Task 1 GREEN ruff verification on tests/unit/test_strategy_schema.py + test_strategy_versioning.py
- **Issue:** Initial test file used `from datetime import datetime, timezone` and `datetime.now(timezone.utc)`; ruff's UP017 wants `from datetime import UTC, datetime` and `datetime.now(UTC)`. Also a couple of import-block ordering issues (I001).
- **Fix:** `uv run ruff check --fix` auto-applied — 12 errors → 0; re-ran tests, still 40/40 passing.
- **Files modified:** `tests/unit/test_strategy_schema.py`, `tests/unit/test_strategy_versioning.py`
- **Committed in:** `ce0f65a` (Task 1 GREEN — applied before commit)

**2. [Rule 1 — Lint] ruff I001 on tests/unit/test_research_brief_schema.py + src/gekko/schemas/research.py.**

- **Found during:** Task 2 GREEN ruff verification
- **Issue:** Same import-ordering issue (I001) post-write.
- **Fix:** Auto-fix.
- **Committed in:** `da19736` (Task 2 GREEN — applied before commit)

**3. [Rule 1 — Lint] ruff I001 on tests/unit/test_proposal_schema.py + src/gekko/schemas/event.py + src/gekko/schemas/proposal.py.**

- **Found during:** Task 3 GREEN ruff verification
- **Issue:** Three I001 import-ordering issues across the new Task 3 files.
- **Fix:** Auto-fix.
- **Committed in:** `9b9f818` (Task 3 GREEN — applied before commit)

---

**Total deviations:** 3 auto-fixes, all lint-only (import ordering + datetime.UTC alias modernization).
**Impact on plan:** No scope creep, no behavior change.

## Issues Encountered

None outside the auto-fixed deviations above. One observation worth surfacing for Plans 01-07 / 01-08:

- **`TradeProposal` reuses `gekko.core.types.OrderSide` and `OrderType` StrEnums directly** rather than re-declaring `Literal["buy", "sell"]` and `Literal["limit", "market", "stop"]`. Pydantic v2 happily validates StrEnum values from both raw strings (`"buy"`) and enum instances (`OrderSide.BUY`) — confirmed by `test_side_must_be_buy_or_sell` (raw string "hold" rejected) and `test_valid_construction` (raw string "buy" accepted). Plan 01-07's Decision-agent tool input_schema can mirror this — the SDK passes raw strings and Pydantic narrows them to the enum.

## Known Stubs

None goal-blocking. Three intentional Wave 1 → Wave 2+ deepening points:

- **P4 forward-compat fields (`injected_content_flags`, `source_allowlist_violations`, `sanitization_applied`)** are NOT in the P1 ResearchBrief schema; they're explicitly P4's scope. The `extra='allow'` config preserves them when P4 starts emitting them.
- **`Strategy.created_by_chat` is a single boolean flag** for STRAT-01 (chat) vs STRAT-02 (form) provenance. A richer provenance shape (chat transcript ref, edit history) is deferred — D-01's "minimal v1 fields" wins.
- **`EventPayload` variants for `kill_switch` and `cap_rejection` carry minimal P1 shapes.** Plan 02 (OrderGuard) deepens `CapRejectionEventPayload` with the full cap-decision audit fields; a future kill-switch UX in P3/P4 may extend `KillSwitchEventPayload`. Both variants use `extra='ignore'` so the additions don't break P1-persisted payloads.

## Self-Check: PASSED

Files verified present:

- `src/gekko/schemas/strategy.py` — FOUND
- `src/gekko/schemas/diff.py` — FOUND
- `src/gekko/schemas/research.py` — FOUND
- `src/gekko/schemas/proposal.py` — FOUND
- `src/gekko/schemas/event.py` — FOUND
- `src/gekko/schemas/__init__.py` — FOUND (modified)
- `tests/unit/test_strategy_schema.py` — FOUND
- `tests/unit/test_strategy_diff.py` — FOUND
- `tests/unit/test_strategy_versioning.py` — FOUND
- `tests/unit/test_research_brief_schema.py` — FOUND
- `tests/unit/test_proposal_schema.py` — FOUND

Commits verified in git log:

- `5339c86` — FOUND (Task 1 RED)
- `ce0f65a` — FOUND (Task 1 GREEN)
- `1af6098` — FOUND (Task 2 RED)
- `da19736` — FOUND (Task 2 GREEN)
- `ed964b5` — FOUND (Task 3 RED)
- `9b9f818` — FOUND (Task 3 GREEN)

Test gates verified green:

- [x] `uv run pytest tests/unit/test_strategy_schema.py tests/unit/test_strategy_diff.py tests/unit/test_strategy_versioning.py --no-header` → 40 passed
- [x] `uv run pytest tests/unit/test_research_brief_schema.py --no-header` → 23 passed
- [x] `uv run pytest tests/unit/test_proposal_schema.py --no-header` → 25 passed
- [x] `uv run pytest tests/unit --no-header -q` → 238 passed (150 prior + 88 new)
- [x] `uv run ruff check .` → All checks passed (after auto-fix during each Task)
- [x] `uv run mypy src` → Success: no issues found in 37 source files
- [x] STRAT-04 closed (next_version + generate_strategy_diff)
- [x] STRAT-05 closed (per-strategy snapshot keying via models.Strategy UniqueConstraint + name selector)
- [x] STRAT-06 closed (Strategy.mode Literal default 'paper'; UI confirmation deferred to 01-09)
- [x] REPT-04 closed (TradeProposal evidence 3-5 + alternatives ≥1 + confidence 0-1)
- [x] RES-08 closed (Guidance Pydantic model with timestamp + scope + expires_at)

Smoke tests confirmed:

- `python -c "from gekko.schemas.strategy import Strategy, HardCaps; from decimal import Decimal; s = Strategy(strategy_id='s1', user_id='alice', name='ai-infra', version=1, thesis='bullish', watchlist=['nvda','amd'], hard_caps=HardCaps(max_position_pct=Decimal('0.05'), max_daily_loss_usd=Decimal('200'), max_trades_per_day=3, max_sector_exposure_pct=Decimal('0.25')), created_at='2026-06-08T00:00:00Z'); print(s.model_dump_json())"` →
  `{"strategy_id":"s1","user_id":"alice","name":"ai-infra","version":1,"thesis":"bullish","watchlist":["NVDA","AMD"],"hard_caps":{"max_position_pct":"0.05","max_daily_loss_usd":"200","max_trades_per_day":3,"max_sector_exposure_pct":"0.25"},"schedule_time":null,"mode":"paper","created_by_chat":false,"created_at":"2026-06-08T00:00:00Z"}`
  Watchlist normalized to ["NVDA","AMD"], mode default "paper", Decimal serialized as string.

- ResearchBrief P4-forward-compat smoke: `ResearchBrief.model_validate({...future_field_p4: 'ok'..., injected_content_flags: ['suspected']})` constructs successfully — forward-compat invariant live.

- `generate_strategy_diff(before, after)` smoke: returned `"You changed max-position from 5% to 7% and added GOOGL, MSFT to the watchlist."` — D-02 plain-English form verified.

- `generate_strategy_diff(before, before)` → `"No changes."` — empty-diff sentinel verified.

## TDD Gate Compliance

All three task pairs followed the strict RED → GREEN sequence:

| Task | RED commit | RED state | GREEN commit | GREEN state |
| ---- | ---------- | --------- | ------------ | ----------- |
| 1 (Strategy + diff + versioning) | `5339c86` test | ModuleNotFoundError on import | `ce0f65a` feat | 40/40 pass |
| 2 (ResearchBrief) | `1af6098` test | ModuleNotFoundError on import | `da19736` feat | 23/23 pass |
| 3 (Proposal + Event) | `ed964b5` test | ModuleNotFoundError on import | `9b9f818` feat | 25/25 pass |

No GREEN commit landed before its RED counterpart. Module-not-found is the canonical RED state for new-module TDD per the workflow rules.

## Next Plan Readiness

Plan 01-07 (agent runtime — Researcher + Decision subagents, BudgetTracker, in-process tools, ProposalWriter, trigger_strategy_run, compile_strategy_from_chat) is unblocked.

It can now:

- `from gekko.schemas import Strategy, Guidance, ResearchBrief, EvidenceSnippet, TickerSnapshot, TradeProposal, NoActionProposal, AlternativeConsidered, EventPayload`
- Construct the Decision-agent's tool input_schemas to MIRROR TradeProposal + NoActionProposal exactly (single source of truth — the JSON schema produced by `TradeProposal.model_json_schema()` IS what the SDK tool definition uses).
- Researcher emits `ResearchBrief` Pydantic instance; parent runtime serializes `brief.model_dump_json()` into the Decision-agent's prompt inside `<RESEARCH_BRIEF source="researcher">...</RESEARCH_BRIEF>` delimiters (Pitfall 9 wrapping).
- ProposalWriter:
  1. Validates the Decision-agent's tool-call output via `TradeProposal(**...)` or `NoActionProposal(**...)` — Pydantic catches D-12 violations (< 3 evidence, no alternatives, etc.) before persistence.
  2. Computes `compute_client_order_id(strategy_id=..., decision_id=..., side=..., qty=..., ticker=...)` and embeds it in `TradeProposal.client_order_id` — schema enforces the 32-char match.
  3. Calls `normalize_decimals(payload)` on the model_dump BEFORE `append_event` (Plan 01-04 caller-side normalization invariant).
  4. Builds and validates the `EventPayload` discriminated-union shape at the write site (recommended pattern from this plan's `<output>` answer block) before passing to `append_event`.

Plan 01-08 (Slack + Executor) is unblocked. It can:

- Build the Block Kit card from `TradeProposal` fields (ticker, side, qty, rationale, confidence, evidence URLs, alternatives_considered descriptions).
- Construct `ApprovalEventPayload` / `RejectionEventPayload` / `OrderSubmittedEventPayload` / `FillEventPayload` at write sites and validate via `TypeAdapter(EventPayload)` before `append_event`.
- Trigger Executor with a `Proposal | NoActionProposal` instance; `isinstance(p, TradeProposal)` switches between place-order and no-action paths.

Plan 01-09 (CLI + Dashboard) is unblocked. It can:

- Wire `gekko strategy create` to call `next_version()`, construct `Strategy(...)`, persist `strategy.model_dump_json()` as the `payload_json` column.
- Render the D-02 plain-English diff on the strategy-edit page via `generate_strategy_diff(latest_minus_1, draft_changes)`.
- Render the proposal-detail view by reading `Proposal.payload_json` and deserializing via `TradeProposal.model_validate_json(...)` (or `NoActionProposal` for the no-action variant).

The schema shapes are **LOCKED** for the Phase 1 audit-log persistence boundary. Any future plan that wants to remove or rename a field on TradeProposal / NoActionProposal / EventPayload variants requires a coordinated migration that invalidates every existing event row's hash. Additive changes are safe (extra='allow' on ResearchBrief / extra='ignore' on TradeProposal + EventPayload variants).

---
*Phase: 01-foundation-vertical-slice-alpaca-paper-slack-hitl*
*Completed: 2026-06-09*
