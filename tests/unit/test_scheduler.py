"""Anomaly + snapshot scheduler registration — TRUST-04 (Plan 05-04 Task 3).

Asserts the contract for the two new scheduler jobs landed by Plan 04's edit to
``gekko.scheduler.jobs``:

  * ``register_anomaly_evaluator`` — an APScheduler **3.x** ``IntervalTrigger``
    job (coalesce=True, max_instances=1) that runs the anomaly evaluator tick to
    catch unrealized drift between fills.
  * ``register_market_open_snapshot`` — a ``CronTrigger`` market-open job that
    writes the stable start-of-day denominator ``evaluate_drawdown`` reads
    (RESEARCH Open Q #3).

Both jobs are registered against an in-memory ``AsyncIOScheduler`` here (the
SQLAlchemyJobStore persistence is exercised in the integration suite). The shape
checks confirm the 3.x trigger types, the restart-safe knobs, and deterministic
job ids.
"""

from __future__ import annotations

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from gekko.scheduler import jobs


def _sched() -> AsyncIOScheduler:
    """An in-memory scheduler (default MemoryJobStore) for shape assertions."""
    return AsyncIOScheduler(timezone="UTC")


def test_register_anomaly_evaluator_exists() -> None:
    assert hasattr(jobs, "register_anomaly_evaluator")


def test_register_market_open_snapshot_exists() -> None:
    assert hasattr(jobs, "register_market_open_snapshot")


def test_anomaly_evaluator_registers_interval_trigger() -> None:
    """The anomaly evaluator registers a 3.x IntervalTrigger with safe knobs."""
    sched = _sched()
    job_id = jobs.register_anomaly_evaluator(sched, user_id="alice")
    assert job_id == "anomaly-eval-alice"
    job = sched.get_job(job_id)
    assert job is not None
    # APScheduler 3.x IntervalTrigger (NOT the 4.x AsyncScheduler API).
    assert isinstance(job.trigger, IntervalTrigger)
    # Restart-safe knobs (mirror the expiry sweep / digest jobs).
    assert job.coalesce is True
    assert job.max_instances == 1
    assert job.kwargs["user_id"] == "alice"


@pytest.mark.asyncio
async def test_anomaly_evaluator_replaces_existing() -> None:
    """Re-registering the same user updates rather than duplicates the job."""
    sched = _sched()
    sched.start(paused=True)
    try:
        jobs.register_anomaly_evaluator(sched, user_id="alice")
        jobs.register_anomaly_evaluator(sched, user_id="alice")
        anomaly_jobs = [
            j for j in sched.get_jobs() if j.id == "anomaly-eval-alice"
        ]
        assert len(anomaly_jobs) == 1
    finally:
        sched.shutdown(wait=False)


def test_market_open_snapshot_registers_cron_trigger() -> None:
    """The market-open snapshot registers a CronTrigger with safe knobs."""
    sched = _sched()
    job_id = jobs.register_market_open_snapshot(sched, user_id="alice")
    assert job_id == "sod-snapshot-alice"
    job = sched.get_job(job_id)
    assert job is not None
    # APScheduler 3.x CronTrigger (NOT 4.x).
    assert isinstance(job.trigger, CronTrigger)
    assert job.coalesce is True
    assert job.max_instances == 1
    assert job.kwargs["user_id"] == "alice"
    # Fires near NYSE open in America/New_York (the handler applies the NYSE
    # schedule gate to skip closed / half days).
    assert str(job.trigger.timezone) == "America/New_York"


@pytest.mark.asyncio
async def test_market_open_snapshot_replaces_existing() -> None:
    sched = _sched()
    sched.start(paused=True)
    try:
        jobs.register_market_open_snapshot(sched, user_id="alice")
        jobs.register_market_open_snapshot(sched, user_id="alice")
        snap_jobs = [
            j for j in sched.get_jobs() if j.id == "sod-snapshot-alice"
        ]
        assert len(snap_jobs) == 1
    finally:
        sched.shutdown(wait=False)


def test_jobs_reference_module_string_callables() -> None:
    """Both jobs reference their handler via a module:fn string so the
    SQLAlchemyJobStore can pickle them across restarts (bound-ref pickling is
    fragile across refactors — Plan 01-09 lock)."""
    sched = _sched()
    jobs.register_anomaly_evaluator(sched, user_id="alice")
    jobs.register_market_open_snapshot(sched, user_id="alice")
    anomaly = sched.get_job("anomaly-eval-alice")
    snapshot = sched.get_job("sod-snapshot-alice")
    # APScheduler stores the string ref as the func when given "module:fn".
    assert "gekko.scheduler.jobs:" in str(anomaly.func_ref)
    assert "gekko.scheduler.jobs:" in str(snapshot.func_ref)


def test_no_apscheduler_4x_apis_in_jobs_source() -> None:
    """jobs.py uses APScheduler 3.x APIs only (NOT the 4.x AsyncScheduler)."""
    from pathlib import Path

    src = Path(jobs.__file__).read_text(encoding="utf-8")
    # No 4.x AsyncScheduler import or instantiation (docstrings may *mention*
    # it as a negative example — the existing reschedule helper does — so we
    # gate on real usage, not the bare substring).
    assert "import AsyncScheduler" not in src
    assert "AsyncScheduler(" not in src
    assert "apscheduler.triggers.interval" in src
    assert "apscheduler.triggers.cron" in src
