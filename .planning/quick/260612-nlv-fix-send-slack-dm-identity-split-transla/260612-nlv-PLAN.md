---
phase: quick-260612-nlv
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/gekko/execution/executor.py
  - tests/unit/test_executor.py
autonomous: true
requirements:
  - QUICK-260612-NLV-01
tags:
  - slack
  - executor
  - identity-split
  - hitl
  - bugfix
must_haves:
  truths:
    - "Calling _send_slack_dm with any gekko_user_id routes the message to settings.slack_user_id (e.g. 'U08LRFFRBS4'), not to the gekko_user_id literal (e.g. 'chris')."
    - "The on_fill_event happy path delivers a fill-confirmation DM without raising SlackApiError(channel_not_found)."
    - "The existing 8 executor unit tests + the chain integration test still pass — no regression on the audit chain, on_fill_event state transition, or BrokerOrderError path."
  artifacts:
    - path: "src/gekko/execution/executor.py"
      provides: "_send_slack_dm reads settings.slack_user_id and passes it as channel= to chat_postMessage; gekko_user_id parameter retained for audit/log metadata only."
      contains: "settings.slack_user_id"
    - path: "tests/unit/test_executor.py"
      provides: "New test asserting chat_postMessage(channel=...) receives settings.slack_user_id, NOT the gekko_user_id argument; complementary test that the text body is unchanged."
      contains: "test_send_slack_dm_translates_gekko_user_id_to_slack_user_id"
  key_links:
    - from: "src/gekko/execution/executor.py:_send_slack_dm"
      to: "src/gekko/config.py:Settings.slack_user_id"
      via: "get_settings().slack_user_id"
      pattern: "settings\\.slack_user_id"
    - from: "src/gekko/execution/executor.py:_send_slack_dm"
      to: "slack_app.client.chat_postMessage"
      via: "channel kwarg now bound to settings.slack_user_id"
      pattern: "chat_postMessage\\(channel=settings\\.slack_user_id"
---

<objective>
Fix the `_send_slack_dm` identity-split bug discovered during Plan 01-09 Task 5
manual demo (2026-06-12): the function passes its incoming `user_id` argument
(the internal `gekko_user_id`, e.g. `"chris"`) directly to Slack's
`chat.postMessage` as the `channel=` kwarg. Slack expects a Slack channel/user
id (e.g. `"U08LRFFRBS4"`), so every fill-confirmation DM in the demo crashed
with `SlackApiError: channel_not_found`. The audit chain itself is untouched
(the `fill` event commits inside the DB transaction at `executor.py:360`
BEFORE the DM call at `:410`), so this is purely an operator-facing fix.

This is the 6th demo-discovery fix in the same bug class as commit `297a882`
(which split slack_user_id from gekko_user_id in slash command handler,
approval-handler, cross-user check, and post_run_result but missed
`_send_slack_dm`).

Purpose: restore the operator-facing fill-confirmation DM so the HITL trust
loop closes end-to-end on the next paper-trade demo, without disturbing the
audit chain, the public `_send_slack_dm(user_id, text)` signature, or any
caller site.

Output: 1-line behavioral fix in `executor.py` (read `settings.slack_user_id`
and pass it as `channel=` to `chat_postMessage`), 1 import line, 1 new
load-bearing test asserting the translation, 1 complementary test confirming
the message body is unchanged.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@./CLAUDE.md
@src/gekko/execution/executor.py
@src/gekko/config.py
@src/gekko/approval/slack_handler.py
@tests/unit/test_executor.py
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Translate gekko_user_id → settings.slack_user_id inside _send_slack_dm</name>
  <files>src/gekko/execution/executor.py, tests/unit/test_executor.py</files>

  <behavior>
    - **Load-bearing test (NEW)** — `test_send_slack_dm_translates_gekko_user_id_to_slack_user_id`:
      * Monkeypatch `gekko.config.get_settings` (or `gekko.execution.executor.get_settings`, whichever the module already imports — it already imports `get_settings` from `gekko.config` at executor.py:68) so `settings.slack_user_id == "U08LRFFRBS4"`.
      * Build an `AsyncMock` for `chat_postMessage` and a stand-in `slack_app` object exposing `.client.chat_postMessage = that_mock`. Insert it into `sys.modules` under `"gekko.slack.app"` via `monkeypatch.setitem(sys.modules, "gekko.slack.app", fake_module)` so the lazy `from gekko.slack.app import slack_app` inside `_send_slack_dm` resolves to the stand-in.
      * `await executor._send_slack_dm(user_id="chris", text="Paper order filled: BUY 1 NVDA @ $204.97")`.
      * Assert `chat_postMessage.await_args.kwargs["channel"] == "U08LRFFRBS4"`. THIS IS THE LOAD-BEARING ASSERTION.
      * Assert `chat_postMessage.await_args.kwargs["channel"] != "chris"` (explicitly defends against the regression class).
      * Assert `chat_postMessage.await_args.kwargs["text"] == "Paper order filled: BUY 1 NVDA @ $204.97"` (no body mangling).
    - **Complementary test (NEW)** — `test_send_slack_dm_preserves_text_verbatim`:
      * Same monkeypatch setup, but pass a text containing mrkdwn-ish characters (e.g. `"*bold* _italic_ <https://example.com|link>"`) and assert it round-trips unchanged through the `text=` kwarg. Confirms the fix didn't accidentally escape/truncate the body.
    - **Regression coverage (EXISTING — must still pass)**:
      * `test_execute_proposal_broker_error_transitions_to_failed` already monkeypatches `executor._send_slack_dm` directly with `fake_send_dm(_user_id, msg)`. Signature stays `(user_id, text)`, so this test continues to pass without edits.
      * `test_on_fill_event_transitions_to_filled_and_dms` already monkeypatches `executor._send_slack_dm` and asserts `sent_dms[0]["msg"]` contains `"NVDA"`. Continues to pass.
  </behavior>

  <action>
    Edit `src/gekko/execution/executor.py` lines 117-126 ONLY. Do NOT touch the call sites at lines 248-249 or 410, do NOT change the public signature `async def _send_slack_dm(user_id: str, text: str) -> None`, do NOT touch `execute_proposal`, `on_fill_event`, imports, or any other module.

    Required edit to `_send_slack_dm`:

      1. Keep the function signature `async def _send_slack_dm(user_id: str, text: str) -> None` exactly as-is. The `user_id` parameter becomes audit/log metadata only — callers continue to pass `gekko_user_id` (which is fine; routing no longer uses it).
      2. Inside the function body, after the lazy `from gekko.slack.app import slack_app` import, read `settings = get_settings()` (the module already imports `get_settings` at line 68 — reuse that import; do NOT add a new import).
      3. Change the `channel=` kwarg on the `chat_postMessage` call from `channel=user_id` to `channel=settings.slack_user_id`. The `text=` kwarg is unchanged.
      4. Refresh the docstring to make the routing rule explicit, exactly matching the existing pattern in `src/gekko/approval/slack_handler.py:135` (cross-user check) — i.e. the routing target is `settings.slack_user_id`; the `user_id` parameter is preserved for caller-API stability + logging/audit metadata. Reference commit `297a882` and Plan 01-09 Task 5 demo finding #6 in the docstring so the next person to read this code sees the bug-class context without having to grep STATE.md.

    Concretely the body becomes (described, not pasted): lazy-import slack_app → `settings = get_settings()` → `await slack_app.client.chat_postMessage(channel=settings.slack_user_id, text=text)`.

    Add the two new tests at the end of `tests/unit/test_executor.py`, after `test_executor_module_does_not_import_claude_agent_sdk` (currently the last test in the file, starting at line 562). Use the same imports / monkeypatch / `AsyncMock` patterns already established at the top of the file (lines 28-47) and in `test_execute_proposal_broker_error_transitions_to_failed` (lines 290-338) for consistency. The two new tests do NOT need `temp_sqlcipher_db` — they only exercise the in-memory Slack stub and the settings monkeypatch.

    SCOPE LOCKS (per task brief):
      - Do NOT change the public signature of `_send_slack_dm(user_id, text)`.
      - Do NOT refactor caller sites at `executor.py:248-249` (BrokerOrderError DM) or `executor.py:410` (fill-confirmation DM) — they continue to pass `gekko_user_id`, now ignored for routing but kept for logging.
      - Do NOT extend the fix to other Slack-DM-sending code paths in this commit (`slack_handler.py`'s `client.chat_postMessage(channel=slack_user_id, ...)` calls are already correct and out of scope).
      - Do NOT bump caps, touch schemas, or modify anything outside `executor.py` + `tests/unit/test_executor.py`.
  </action>

  <verify>
    <automated>uv run pytest tests/unit/test_executor.py -x -v</automated>
  </verify>

  <done>
    - `tests/unit/test_executor.py` contains both new tests and they pass.
    - All 9 pre-existing tests in `tests/unit/test_executor.py` still pass (no regression on happy-path, market-closed, non-APPROVED, BrokerOrderError, duplicate-COID, on_fill_event, Decimal normalization, schema-conformance, or claude_agent_sdk grep-gate).
    - `git diff src/gekko/execution/executor.py` shows changes ONLY inside the `_send_slack_dm` function body + its docstring; lines 1-116 and 128-417 are untouched.
    - `_send_slack_dm`'s signature is still `async def _send_slack_dm(user_id: str, text: str) -> None`.
    - The string `channel=settings.slack_user_id` appears exactly once in `src/gekko/execution/executor.py` (inside `_send_slack_dm`); the string `channel=user_id` no longer appears anywhere in that file.
  </done>
</task>

</tasks>

<verification>
- `uv run pytest tests/unit/test_executor.py -x -v` — all 11 tests pass (9 existing + 2 new).
- `uv run pytest tests/integration/test_slack_approval_to_executor.py -x` — the chain integration test still passes (no regression on the audit chain, on_fill_event state transition, or the broker error DM path; integration test monkeypatches `_send_slack_dm` directly so signature stability is what matters here).
- Manual smoke check (operator, optional, post-merge): re-run the Plan 01-09 Task 5 demo flow with a paper BUY → wait for fill → confirm the "Paper order filled: …" DM lands in Slack without a `channel_not_found` SlackApiError in the executor logs.
</verification>

<success_criteria>
- The load-bearing assertion `chat_postMessage.await_args.kwargs["channel"] == settings.slack_user_id` passes in the new unit test.
- The complementary assertion `chat_postMessage.await_args.kwargs["text"] == <input text>` passes (no body regression).
- The full `tests/unit/test_executor.py` module passes (11 tests).
- The chain integration test in `tests/integration/test_slack_approval_to_executor.py` still passes unchanged.
- `_send_slack_dm`'s public signature `(user_id: str, text: str) -> None` is unchanged.
- No caller site (`executor.py:248-249`, `executor.py:410`) is modified.
- No file outside `src/gekko/execution/executor.py` and `tests/unit/test_executor.py` is modified.
</success_criteria>

<output>
Create `.planning/quick/260612-nlv-fix-send-slack-dm-identity-split-transla/260612-nlv-SUMMARY.md` when done. Recommended commit message:

```
fix(01-09): translate gekko_user_id → slack_user_id in _send_slack_dm

Plan 01-09 Task 5 manual demo (2026-06-12) surfaced that the operator's
fill-confirmation DM never arrived: _send_slack_dm passed channel="chris"
(internal gekko_user_id) to Slack chat.postMessage, which expects a Slack
channel/user id like "U08LRFFRBS4". Same bug class as commit 297a882
(which fixed slack_user_id vs gekko_user_id split in 4 other call sites
but missed _send_slack_dm). Audit chain unaffected — the `fill` event
commits inside the DB transaction before the DM call.

Fix: read settings.slack_user_id inside _send_slack_dm and route there.
Public signature (user_id, text) is unchanged; user_id is now audit/log
metadata only. Caller sites at executor.py:248-249 and :410 are not
modified.
```
</output>
