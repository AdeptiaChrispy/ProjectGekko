---
type: quick
created: 2026-06-12
slug: 260612-dix-raise-rationale-cap-to-5000-slack-render
origin: Plan 01-09 Task 5 manual walking-skeleton demo discovery
files_modified:
  - src/gekko/schemas/proposal.py
  - src/gekko/reporter/slack.py
  - tests/unit/test_proposal_schema.py
  - tests/unit/test_slack_block_kit.py
autonomous: true
---

<objective>
Fix the Plan 01-09 Task 5 walking-skeleton demo crash where Sonnet emitted a
realistic ~1200-3500-char trade rationale and tripped the
`TradeProposal.rationale` `max_length=1000` Pydantic guard. Anthropic's tool-use
protocol treats JSON Schema `maxLength` as a SOFT hint, so we cannot rely on the
LLM to self-cap — we must give the schema enough headroom (5000 chars) AND
defend Slack's hard 3000-char section-block limit at the renderer layer with a
local truncate guard.

Purpose: unblock the end-to-end `/gekko run` demo path with the same
defense-in-depth pattern as the four prior demo-discovery fixes in commit
297a882 (raise the cap where the LLM authors, clamp where the downstream system
has a hard limit).

Output:
- `rationale` accepts up to 5000 chars on both `TradeProposal` and
  `NoActionProposal`
- Slack reporter truncates rationale to ≤ 2900 raw chars + visible truncation
  marker BEFORE mrkdwn escape, keeping the rendered section block under Slack's
  3000-char ceiling
- Test coverage for both the new schema headroom and the renderer guard
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
</execution_context>

<context>
@CLAUDE.md
@.planning/STATE.md
@src/gekko/schemas/proposal.py
@src/gekko/reporter/slack.py
@tests/unit/test_proposal_schema.py
@tests/unit/test_slack_block_kit.py
</context>

<background>

**The bug** (reproduced 2026-06-12 via `/gekko run ai-infra-bull` slash command):

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for TradeProposal
rationale
  String should have at most 1000 characters [type=string_too_long, ...]
```

**Why headroom (not retry / not prompt-tightening) is the right fix:**

- Anthropic's tool-use docs say JSON Schema `maxLength` is a soft hint to the
  model — Sonnet routinely emits slightly over the cap on rich rationales (thesis
  + 3-5 evidence cites + alternatives discussion + risk justification).
- Realistic trade rationales observed during the demo: ~1500-3500 chars.
- 5000 is 5x the prior cap and gives comfortable headroom without affecting the
  audit-chain canonical-bytes footprint meaningfully (~5KB max per row vs ~500B
  typical).
- Prompt-injection retry on `ValidationError` is deferred to Phase 4 hardening
  (out of scope per user direction).

**Why Slack still needs a truncate guard:**

- Slack `section.text` is hard-capped at 3000 chars. A 5000-char rationale would
  cause `chat_postMessage` to return `invalid_blocks` and surface a different
  error.
- The mrkdwn escape (`_escape_mrkdwn`) can EXPAND length (`\` before
  `< > * _ ~ | \``). Truncate the raw rationale BEFORE escape so the visible
  truncation marker doesn't get mangled by escape logic.
- LLM rationale prose contains mrkdwn metacharacters rarely (mostly natural
  English), so post-escape expansion is small in practice — 2900 raw + ~40-char
  marker leaves ample room under 3000.

**Exact line-number map (verified by reading the files):**

- `src/gekko/schemas/proposal.py:95` — `TradeProposal.rationale`
- `src/gekko/schemas/proposal.py:133` — `NoActionProposal.rationale`
- `src/gekko/reporter/slack.py:194` — `rationale_md = _escape_mrkdwn(proposal.rationale)` (TradeProposal card path)
- `src/gekko/reporter/slack.py:228` — Block Kit section consumes `rationale_md` (no further change here once line 194 is fixed)
- `src/gekko/reporter/slack.py:308` — `rationale_safe = _escape_mrkdwn(no_action.rationale)` (NoActionProposal DM path)
- `src/gekko/reporter/slack.py:316` — message body consumes `rationale_safe` (no further change here once line 308 is fixed)

**Out of scope (DO NOT touch):**

- Decision-agent system prompt
- Retry loop on `ValidationError`
- Audit chain / canonical bytes
- Agent runtime
- Any cap higher than 5000
</background>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Raise rationale cap to 5000 on both proposal schemas + tests</name>
  <files>
    src/gekko/schemas/proposal.py,
    tests/unit/test_proposal_schema.py
  </files>
  <behavior>
    - `TradeProposal.model_validate(... rationale=4999 chars ...)` succeeds
    - `TradeProposal.model_validate(... rationale=5001 chars ...)` raises `ValidationError` (`string_too_long`)
    - `NoActionProposal.model_validate(... rationale=4999 chars ...)` succeeds
    - `NoActionProposal.model_validate(... rationale=5001 chars ...)` raises `ValidationError` (`string_too_long`)
    - All pre-existing schema tests still pass unchanged (the canonical sample
      `"Strong technicals; sector beat."` at line 62 is 30 chars, well under both
      caps — no fixture edits needed)
  </behavior>
  <action>
    1. In `src/gekko/schemas/proposal.py`:
       - Line 95: change `rationale: str = Field(..., min_length=1, max_length=1000)` to `rationale: str = Field(..., min_length=1, max_length=5000)` on `TradeProposal`.
       - Line 133: change `rationale: str = Field(..., min_length=1, max_length=1000)` to `rationale: str = Field(..., min_length=1, max_length=5000)` on `NoActionProposal`.
       - No other field edits. Do not retouch the docstrings — the schema-as-rationale-capture invariant (D-12, D-15) still holds, just with more headroom.
    2. In `tests/unit/test_proposal_schema.py`, append four new tests at the END of the file (do NOT edit existing fixtures — `_trade_proposal_kwargs` and `_alts` should be reused via overrides):
       - `test_trade_proposal_rationale_accepts_4999_chars`: build a `TradeProposal` via `_trade_proposal_kwargs(rationale="x" * 4999)` and assert it constructs without error. Assert `len(tp.rationale) == 4999`.
       - `test_trade_proposal_rationale_rejects_5001_chars`: same pattern but with `"x" * 5001`; wrap in `pytest.raises(ValidationError)` and assert `"string_too_long"` appears in `str(exc_info.value)`.
       - `test_no_action_rationale_accepts_4999_chars`: construct a `NoActionProposal` directly (no helper exists — supply `user_id="alice"`, `strategy_name="ai-infra"`, `decision_id="d1"`, `rationale="x" * 4999`, `factors_considered=["price_vs_thesis"]`, `confidence=Decimal("0.5")`). Assert it constructs.
       - `test_no_action_rationale_rejects_5001_chars`: same as above but `"x" * 5001`; assert `ValidationError` with `"string_too_long"`.
    3. Do NOT add cap-boundary tests at exactly 1000 / 1001 (those would have asserted the old behavior; they're not in the existing file — `grep` confirmed `max_length=1000` is absent from the test file already, so nothing to update).
  </action>
  <verify>
    <automated>uv run pytest tests/unit/test_proposal_schema.py -v</automated>
  </verify>
  <done>
    - Both `Field(..., min_length=1, max_length=5000)` declarations land on the two `rationale` fields.
    - Four new tests pass, all pre-existing schema tests still pass.
    - `pyright` (or `mypy`) reports no new type errors on the edited file.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Add Slack truncate guard at the renderer + tests</name>
  <files>
    src/gekko/reporter/slack.py,
    tests/unit/test_slack_block_kit.py
  </files>
  <behavior>
    - `_truncate_for_slack("x" * 100)` returns `"x" * 100` unchanged (under default 2900 limit).
    - `_truncate_for_slack("x" * 2900)` returns `"x" * 2900` unchanged (at boundary, no truncation).
    - `_truncate_for_slack("x" * 2901)` returns `"x" * 2900 + "…[truncated; see audit log for full text]"`, total len = 2900 + 40 = 2940 (well under 3000).
    - `build_proposal_card(tp_with_4500_char_rationale)` produces a Block Kit list where the Rationale section's mrkdwn text is ≤ 3000 chars AND contains the substring `"…[truncated; see audit log for full text]"`.
    - `build_proposal_card(tp_with_short_rationale)` (existing 90-char fixture) produces a Block Kit list whose Rationale section text does NOT contain the truncation marker.
    - `build_no_action_message(no_action_with_4500_char_rationale)` returns a string where (a) the rationale portion is truncated and (b) the truncation marker is present.
    - All 14 existing `test_slack_block_kit.py` tests still pass — including the mrkdwn-escape prompt-injection test at line ~342, since the truncation marker contains no mrkdwn metacharacters.
  </behavior>
  <action>
    1. In `src/gekko/reporter/slack.py`, add a module-private helper near the
       top of the "Block builders — internal helpers" section (after `_WS_RUN`
       and before `_escape_mrkdwn`, so the helper is defined before its callers):

       ```
       _SLACK_SECTION_RAW_LIMIT = 2900
       _SLACK_TRUNCATION_MARKER = "…[truncated; see audit log for full text]"
       ```

       Then define `def _truncate_for_slack(text: str, limit: int = _SLACK_SECTION_RAW_LIMIT) -> str:`
       with a docstring explaining that Slack `section.text` is hard-capped at
       3000 chars, the cap is applied BEFORE `_escape_mrkdwn` (because escape
       can expand length), and 2900 + ~40-char marker leaves headroom for both
       the marker and any small post-escape expansion. Body:

       - If `len(text) <= limit`, return `text` unchanged.
       - Else return `text[:limit] + _SLACK_TRUNCATION_MARKER`.

       No fenced code in the action prose beyond the constants above (they are
       directive identifier definitions, not implementation logic).

    2. At line 194 (`rationale_md = _escape_mrkdwn(proposal.rationale)`),
       change to: first apply `_truncate_for_slack(proposal.rationale)`,
       then pass that into `_escape_mrkdwn`. Single composed expression is
       fine: `rationale_md = _escape_mrkdwn(_truncate_for_slack(proposal.rationale))`.

    3. At line 308 (`rationale_safe = _escape_mrkdwn(no_action.rationale)`),
       apply the same pattern: `rationale_safe = _escape_mrkdwn(_truncate_for_slack(no_action.rationale))`.

    4. Lines 228 and 316 require NO changes — they already consume the
       wrapped `rationale_md` / `rationale_safe` variables.

    5. Export `_truncate_for_slack` in the test file via a direct module
       import (`from gekko.reporter.slack import _truncate_for_slack`) — the
       underscore prefix is fine for unit-test reach-through, this is the
       same pattern the existing tests use for `_escape_mrkdwn`-style internals
       if any (otherwise the new helper is a sibling-private). Do NOT add it
       to `__all__` — it stays module-private.

    6. In `tests/unit/test_slack_block_kit.py`, append new tests at the END
       of the file:

       - `test_truncate_for_slack_short_text_unchanged`: assert
         `_truncate_for_slack("hello world") == "hello world"`.
       - `test_truncate_for_slack_at_boundary_unchanged`: assert
         `_truncate_for_slack("x" * 2900) == "x" * 2900`.
       - `test_truncate_for_slack_over_boundary_truncates`: build
         `"x" * 2901`, assert result starts with `"x" * 2900`, ends with the
         truncation marker, and total length is 2940.
       - `test_card_rationale_truncated_when_long`: build a `TradeProposal`
         via the existing `_sample_trade_proposal()` helper with overridden
         `rationale="A" * 4500` (use the same evidence/alternatives fixtures
         already in the file), call `build_proposal_card(tp)`, find the
         Rationale section block (the one whose text starts with `"*Rationale:* "`),
         assert its text length is ≤ 3000 AND contains
         `"…[truncated; see audit log for full text]"`.
       - `test_card_rationale_not_truncated_when_short`: re-use the existing
         `_sample_trade_proposal()` (~90-char rationale at line 87-90),
         assert the Rationale section text does NOT contain the truncation
         marker.
       - `test_no_action_message_truncates_long_rationale`: build a
         `NoActionProposal` with `rationale="B" * 4500`, call
         `build_no_action_message(na)`, assert the truncation marker is
         present AND the message contains `"Reviewed"` (sanity that the
         outer template still renders).

    7. Do NOT modify the existing prompt-injection test (around line 342) —
       its `nasty` rationale is short, so it will not trigger truncation.
       Re-running the existing tests is sufficient regression coverage.
  </action>
  <verify>
    <automated>uv run pytest tests/unit/test_slack_block_kit.py -v</automated>
  </verify>
  <done>
    - `_truncate_for_slack` exists as a module-private helper in
      `src/gekko/reporter/slack.py`.
    - Lines 194 and 308 both route rationale through
      `_truncate_for_slack` BEFORE `_escape_mrkdwn`.
    - Six new tests pass; all 14 pre-existing `test_slack_block_kit.py`
      tests still pass.
    - `ruff check` and `ruff format --check` are clean on the edited file.
  </done>
</task>

</tasks>

<verification>
Run the full unit-test suite to confirm no upstream collateral breakage
(e.g. `test_rationale_capture.py`, which exercises the proposal schema
end-to-end through the audit chain, still passes — its fixtures use short
rationales, so no edits expected):

```
uv run pytest tests/unit -v
```

Lint + type-check the two edited source files:

```
uv run ruff check src/gekko/schemas/proposal.py src/gekko/reporter/slack.py
uv run ruff format --check src/gekko/schemas/proposal.py src/gekko/reporter/slack.py
uv run pyright src/gekko/schemas/proposal.py src/gekko/reporter/slack.py
```
</verification>

<success_criteria>
- `TradeProposal` and `NoActionProposal` accept rationales up to 5000 chars; reject 5001+.
- `gekko.reporter.slack._truncate_for_slack` exists and is wired into both rationale render paths.
- Rendered Slack section blocks remain ≤ 3000 chars even when the LLM emits a 4500-char rationale.
- Full unit-test suite green (`uv run pytest tests/unit`).
- Lint + type-check clean on both edited source files.
- The original demo failure mode (`ValidationError: rationale ... at most 1000 characters` on a realistic Sonnet trade rationale) cannot recur because realistic rationales fit comfortably under 5000 and downstream Slack rendering is now bounded.
</success_criteria>

<output>
After both tasks pass verify steps, commit each task as a separate atomic
commit (per GSD convention):

- Task 1: `fix(schemas): raise proposal rationale cap to 5000 chars (01-09 demo finding)`
- Task 2: `fix(reporter/slack): truncate rationale before mrkdwn escape (01-09 demo finding)`

No SUMMARY file required (this is a quick/dix, not a phase plan). The PLAN.md
itself is the durable record of the fix.
</output>
