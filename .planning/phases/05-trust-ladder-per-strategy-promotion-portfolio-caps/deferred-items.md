# Phase 05 — Deferred Items (out-of-scope discoveries)

Logged per execute-plan SCOPE BOUNDARY: issues NOT caused by this plan's
changes are recorded here and left untouched.

| Discovered during | Item | Status | Note |
|-------------------|------|--------|------|
| Plan 05-01 Task 3 | `tests/unit/test_approval_proposals.py::test_handle_edit_size_stub_acks_and_opens_modal` fails (`assert 'views_open' in ['ack']`) | pre-existing, out-of-scope | Verified pre-existing by stashing all Task-3 edits and re-running — the test still fails. It asserts a retired D-62 edit-size Bolt handler opens a modal; the handler now logs `slack.edit_size.button.retired` and only acks (edit-size became a URL button per D-62). The test was not updated when the handler was retired. Unrelated to the approval/cap_rejection payload enrichment in this plan. Route into a future Phase-3 follow-up or quick-task. |
