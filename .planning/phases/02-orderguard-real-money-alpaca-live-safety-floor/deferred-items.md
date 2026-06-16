# Deferred Items — Phase 02

Out-of-scope discoveries surfaced during execution. Per execute-plan rules these
are logged here and NOT auto-fixed in the current plan (they predate the changes
and have no causal relation to OrderGuard).

## Pre-existing test failures (predate Phase 02)

### `tests/unit/test_cli.py::test_doctor_missing_envvar_exits_nonzero`

- **Discovered during:** Plan 02-02 Task 5 full-suite verification
- **Symptom:** `assert result.exit_code != 0` fails because `gekko doctor` exits
  0 even when required env vars are missing.
- **Earliest commit touching the file:** `be1771f feat(01-01): typer cli stub +
  gekko doctor + smoke tests` (Phase 1 plan 01-01)
- **Reproduction with HEAD reset:** confirmed pre-existing — fails before any
  Plan 02-02 code was applied (verified via `git stash` + rerun).
- **Causal link to OrderGuard:** none. `gekko doctor` invokes the CLI subcommand
  added in Plan 01-01; OrderGuard does not touch the CLI module.
- **Suggested owner:** future plan that revisits `gekko doctor` env-var
  validation (likely 02-06 when the live-credentials vault path adds new
  required env vars). At that point the test's env-var deletion list should be
  re-audited against settings.py — the test deletes `ANTHROPIC_API_KEY`,
  `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, but the doctor command may have
  added env-var fallbacks (e.g., .env file loading) that make those deletions
  insufficient to trigger the failure path.

### `tests/unit/test_config.py::test_missing_anthropic_key_raises_validation_error`

- **Discovered during:** Plan 02-02 Task 5 full-suite verification
- **Symptom:** `with pytest.raises(ValidationError)` does not raise — the
  monkeypatch deletes `ANTHROPIC_API_KEY` from env, but `Settings` still
  validates (loads from `.env` file in repo root, bypassing the delete).
- **Causal link to OrderGuard:** none. Phase-1 Settings module not touched
  by 02-02.
- **Reproduction with HEAD reset:** confirmed pre-existing (verified via
  `git stash` + rerun).
- **Suggested fix:** the test must set `monkeypatch.setenv("ENV_FILE", "")`
  or pass `_env_file=None` into Settings, OR the test must use a tmp_path
  cwd. Out of scope for plan 02-02.

### `tests/unit/test_research_tools.py::test_finnhub_news_degrades_gracefully_without_key`

- **Discovered during:** Plan 02-02 Task 5 full-suite verification
- **Symptom:** Test deletes `FINNHUB_API_KEY` and asserts the news fetch
  returns []; instead it returns 5 real news items, meaning the .env
  file is being read despite the delenv.
- **Causal link to OrderGuard:** none. Phase-1 research module not
  touched by 02-02.
- **Suggested fix:** same root cause as the Settings test above — repo's
  `.env` file overrides test monkeypatch.delenv. Likely a single
  pytest conftest fixture would fix all three in one swoop. Out of
  scope for plan 02-02.

## Notes

- This file should be flushed (entries resolved or escalated to a fresh plan)
  before Phase 02 closes.
- Per the executor agent's scope-boundary rule, fixing pre-existing failures
  in this plan would conflate Wave 2's diff with unrelated CLI work and would
  exceed the 3-attempt auto-fix limit. The right move is a separate
  ergonomics/test-hardening plan.
