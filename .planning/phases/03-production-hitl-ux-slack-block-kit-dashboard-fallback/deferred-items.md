# Deferred Items — Phase 03

Manual-only verifications that cannot be replayed in a cassette. Each row must
be completed by the operator before Phase 3 is declared fully closed. Per the
Phase 1 + Phase 2 closeout pattern, the automated walking-skeleton cassette
(``tests/integration/test_p3_walking_skeleton.py``) is the load-bearing gate;
these manual checks are the final wall-clock evidence.

## Manual-Only Verifications

| Category | Item | Status | Note |
|----------|------|--------|------|
| HITL-02 | Real Slack dup-click survives at-least-once delivery | pending (operator demo) | Wall-clock + real Slack retry; cannot mock Slack's actual retry timing in a cassette. Demo: click Approve twice rapidly in real Slack; observe ONE execution + ONE ephemeral "already approved" toast. |
| HITL-03 | 60s sweep latency observed on real wall clock | pending (operator demo) | Cannot mock APScheduler clock and Slack chat.update simultaneously in a deterministic cassette. Demo: create a proposal with ``expires_at=utcnow()+30s``; observe card swap to EXPIRED within ≤90s of due time + receive expiry DM. |
| HITL-05 | Quiet-hours predicate crosses a real DST boundary | pending (operator demo) | freezegun + ZoneInfo together is fragile across DST; real clock is the truth. Defer to Spring 2027 OR run a one-shot CI job with system date set to 2027-03-13 (DST spring-forward day). |
| DASH-04 | Dashboard ``/approvals`` end-to-end in browser with cookie session | pending (operator demo) | HTMX behavior + cookie attributes + real "Slack-down" scenario; cannot be cassette-replayed. Demo: stop Slack Socket Mode, navigate to ``http://localhost:8000/login``, enter passphrase, approve a proposal from ``/approvals``, observe execution + fill DM (restore Slack Socket Mode after). |
| REPT-01 | Daily P&L DM fires at 16:30 ET on a real trading day | pending (operator demo) | APScheduler CronTrigger cannot be deterministically jumped to 16:30 across timezones in a unit test. Observe one real-day DM at 16:30 America/New_York; verify content includes today's fills, gross P&L, open positions count, and errors count. |

## Pre-existing test failures (pre-Phase-03)

The following failures predate Phase 03 and are out of scope for this phase's
executor. They are reproduced from the Phase 02 deferred-items.md for
traceability.

| Test | Symptom | Causal link to Phase 03 |
|------|---------|------------------------|
| ``test_cli.py::test_doctor_missing_envvar_exits_nonzero`` | ``gekko doctor`` exits 0 when required env vars are missing | None — Phase 1 CLI |
| ``test_config.py::test_missing_anthropic_key_raises_validation_error`` | ``Settings`` reads from ``.env`` file, bypassing ``monkeypatch.delenv`` | None — Phase 1 Settings |
| ``test_research_tools.py::test_finnhub_news_degrades_gracefully_without_key`` | Finnhub returns real news despite ``delenv`` because ``.env`` is loaded | None — Phase 1 research tools |

## Notes

- This file should be updated (rows marked ``completed``) as each manual
  verification is performed before Phase 03 is closed.
- Per the Phase 1 + Phase 2 closeout pattern, the operator replies with
  ``demo_passed`` + supporting evidence (audit dump, Slack screenshots) for
  each row.
- The automated cassette at
  ``tests/integration/test_p3_walking_skeleton.py`` covers the same
  scenarios in cassette mode (no real Slack, no real Alpaca). All 4 cassette
  tests must remain green before any manual demo is attempted.
