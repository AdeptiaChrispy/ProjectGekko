---
type: quick
slug: 260612-dix-raise-rationale-cap-to-5000-slack-render
created: 2026-06-12
completed: 2026-06-12
origin: Plan 01-09 Task 5 manual walking-skeleton demo discovery
tags:
  - schema-headroom
  - slack-render-guard
  - defense-in-depth
  - 01-09-followup
key-files:
  modified:
    - src/gekko/schemas/proposal.py
    - src/gekko/reporter/slack.py
    - tests/unit/test_proposal_schema.py
    - tests/unit/test_slack_block_kit.py
commits:
  - 9bc8c36 fix(schemas): raise proposal rationale cap to 5000 chars (01-09 demo finding)
  - 03a9b8e fix(reporter/slack): truncate rationale before mrkdwn escape (01-09 demo finding)
metrics:
  tasks_completed: 2
  tests_added: 10
  tests_total_in_suites: 54
duration_minutes: ~20
---

# Quick 260612-dix: Raise rationale cap to 5000 + Slack render guard — Summary

Fix the Plan 01-09 Task 5 walking-skeleton demo crash where Sonnet emitted a
realistic ~1200-3500-char trade rationale and tripped the
`TradeProposal.rationale` `max_length=1000` Pydantic guard, with a
defense-in-depth Slack renderer truncation guard so the 5000-char headroom
doesn't trip Slack's hard 3000-char section.text ceiling.

## What changed

**Task 1 — Schema headroom (commit `9bc8c36`)**

- `src/gekko/schemas/proposal.py:95` — `TradeProposal.rationale`
  `max_length=1000 → 5000`
- `src/gekko/schemas/proposal.py:133` — `NoActionProposal.rationale`
  `max_length=1000 → 5000`
- `tests/unit/test_proposal_schema.py` — appended 4 boundary tests
  (`test_trade_proposal_rationale_accepts_4999_chars`,
  `test_trade_proposal_rationale_rejects_5001_chars`,
  `test_no_action_rationale_accepts_4999_chars`,
  `test_no_action_rationale_rejects_5001_chars`)

**Task 2 — Slack renderer truncate guard (commit `03a9b8e`)**

- `src/gekko/reporter/slack.py` — added module-private
  `_truncate_for_slack(text, limit=2900)` helper with constants
  `_SLACK_SECTION_RAW_LIMIT = 2900` and
  `_SLACK_TRUNCATION_MARKER = "…[truncated; see audit log for full text]"`
- `src/gekko/reporter/slack.py:194` — `rationale_md` now routes through
  `_escape_mrkdwn(_truncate_for_slack(proposal.rationale))` (truncate first,
  then escape, because escape can expand length)
- `src/gekko/reporter/slack.py:308` — same composed wrapping for
  `rationale_safe` (NoActionProposal DM path)
- `tests/unit/test_slack_block_kit.py` — appended 6 tests covering helper
  boundary cases, long-rationale card truncation, short-rationale
  not-truncated, and no-action message truncation

## Verification

| Verify step | Result |
| --- | --- |
| Task 1: `uv run pytest tests/unit/test_proposal_schema.py -v` | 29/29 pass (25 pre-existing + 4 new) |
| Task 2: `uv run pytest tests/unit/test_slack_block_kit.py -v` | 25/25 pass (19 pre-existing + 6 new) |
| Final sweep at HEAD: both suites together | 54/54 pass |
| `uv run ruff check` on `proposal.py`, `slack.py` | All checks passed |
| Schema/reporter-adjacent regression scope (150 tests across `test_proposal_schema`, `test_slack_block_kit`, `test_rationale_capture`, `test_proposal_writer`, `test_approval_proposals`, `test_strategy_diff`, `test_strategy_schema`, `test_research_brief_schema`, `test_executor`) | 150/150 pass |

## Deviations from plan

**1. [Rule 1 — Plan arithmetic correction]** The plan's `<behavior>` block
said the truncated total length should be `2900 + 40 = 2940`. The actual
marker `"…[truncated; see audit log for full text]"` is **41 characters**
in Python (the leading `…` is U+2026 horizontal ellipsis, one Python
char). The implementation uses the exact marker string the plan defined —
only the test assertion was adjusted to use
`assert len(result) == 2900 + len(_SLACK_TRUNCATION_MARKER)` (i.e. 2941)
and `assert len(result) < 3000` (still well under Slack's ceiling). No
production behavior change vs. plan intent. Docstring also updated to
mention the 41-char marker length explicitly.

## Deferred issues

Two pre-existing items surfaced during regression checking, confirmed
pre-existing on a clean tree at commit `9bc8c36` (the post-Task-1 base)
via `git stash` round-trip. Captured in
`.planning/quick/260612-dix-raise-rationale-cap-to-5000-slack-render/deferred-items.md`:

1. **`tests/unit/test_cli.py::test_doctor_missing_envvar_exits_nonzero`**
   fails — likely a Plan 01-09 vault/cache isolation regression, not
   touching this quick task's scope.
2. **`tests/unit/test_slack_block_kit.py`** has 4 pre-existing F401
   unused-import warnings on `UTC`, `datetime`, `typing.Any`, `pytest`.

Neither is caused by 260612-dix edits. Per the GSD executor scope-boundary
rule, both are logged but not fixed here.

## Self-Check: PASSED

- Created files: `deferred-items.md` (3037 bytes), this SUMMARY.md — both
  present in working tree.
- Commits: `9bc8c36` and `03a9b8e` both present in `git log --oneline -3`.
- Per-task verify steps passed.
- No unexpected deletions: `git diff --diff-filter=D --name-only HEAD~2 HEAD`
  returns nothing.
