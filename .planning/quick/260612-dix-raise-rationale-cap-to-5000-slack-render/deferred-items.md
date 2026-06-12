# Deferred Items — Quick 260612-dix

Items discovered during execution that are **pre-existing** on a clean tree
(verified via `git stash` round-trip at commit `9bc8c36`) and therefore out
of scope for this quick task per the GSD executor scope-boundary rule. Each
item is unrelated to the rationale-cap / Slack-truncate fix.

## Pre-existing test failure

**`tests/unit/test_cli.py::test_doctor_missing_envvar_exits_nonzero`**

- **Symptom:** `assert result.exit_code != 0` fails — `gekko doctor` returns
  exit code 0 even when every required env-var
  (`ANTHROPIC_API_KEY`, `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_SECRET_KEY`,
  `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`) is stripped via
  `monkeypatch.delenv(..., raising=False)`.
- **Hypothesis:** The CLI likely now reads env-vars from the new vault
  bootstrap (`gekko.vault.passphrase` / `gekko init` flow added in
  Plan 01-09) and finds cached values from the operator's environment that
  `monkeypatch.delenv` can't reach. Test probably needs updating to also
  invalidate the `@lru_cache`'d `get_settings()` and/or clear the vault.
- **Action:** Flag for next CLI / doctor maintenance pass — fix the
  doctor test isolation. Not touched here because (a) the test exercises a
  module unrelated to schemas/reporter, and (b) attempting to fix it would
  put this quick task above its 3-fix scope-attempt limit.

## Pre-existing lint warnings

**`tests/unit/test_slack_block_kit.py` — unused imports (F401)**

- `from datetime import UTC, datetime` — neither symbol referenced
- `from typing import Any` — not referenced
- `import pytest` — not referenced (no `pytest.raises` or `pytest.fixture`
  decorators in the file as of this writing)

All four were present at commit `9bc8c36` before any 260612-dix edit (verified
by `uv run ruff check tests/unit/test_slack_block_kit.py` on the stashed
clean tree). Out of scope; flag for next test-hygiene pass.

## Pre-existing format drift

`uv run ruff format --check src/gekko/schemas/proposal.py
tests/unit/test_proposal_schema.py` reports 3 lines that would be
reformatted — all in code blocks I did NOT edit (line-continuation
collapses in `_trade_proposal_kwargs` arg lists and the
`alternatives_considered` `Field(...)` line). Out of scope.

## What this quick task DID touch

- `src/gekko/schemas/proposal.py` lines 95, 133 — `rationale` cap 1000 → 5000
- `src/gekko/reporter/slack.py` — added `_truncate_for_slack` helper +
  threaded it through the two `_escape_mrkdwn(rationale)` call sites
- `tests/unit/test_proposal_schema.py` — appended 4 boundary tests
- `tests/unit/test_slack_block_kit.py` — appended 6 truncation tests
  (and imported `_truncate_for_slack` for reach-through testing)

Per-task verify (`uv run pytest tests/unit/test_proposal_schema.py -v` →
29/29 pass; `uv run pytest tests/unit/test_slack_block_kit.py -v` →
25/25 pass) and lint of the edited source files (`uv run ruff check
src/gekko/schemas/proposal.py src/gekko/reporter/slack.py` → all checks
passed) are clean.
