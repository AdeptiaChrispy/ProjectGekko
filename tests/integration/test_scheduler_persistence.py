"""APScheduler persistence + cron-trigger tests — Plan 01-09 Task 2 (CADENCE-02).

Eight behaviors:

1. ``build_scheduler(sync_engine)`` returns an :class:`AsyncIOScheduler`
   wired to ``SQLAlchemyJobStore`` over the passed sync engine.
2. ``schedule_strategy_daily(...)`` adds a job with a deterministic
   ``run-{user_id}-{strategy_name}`` id.
3. The job uses ``CronTrigger(hour=hh, minute=mm, timezone=ZoneInfo(tz))``
   per D-08.
4. ``replace_existing=True`` — re-scheduling updates the existing job
   (no duplicate).
5. ``unschedule_strategy(...)`` removes the job; returns True; subsequent
   call with the same args returns False.
6. **Persistence (CADENCE-02 wave gate).** A scheduler built from sync
   engine A schedules a job + shuts down; a NEW scheduler built from a
   fresh sync engine B over the SAME DB file + passphrase sees the
   previously-scheduled job in ``get_jobs()``.
7. IANA timezone resolution works on the executor's platform.
8. Malformed ``schedule_time`` raises :class:`ValueError` before the
   job is added.
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from gekko.db.engine import get_sync_engine
from gekko.scheduler.jobs import (
    build_scheduler,
    schedule_strategy_daily,
    unschedule_strategy,
)

pytestmark = pytest.mark.integration


_PASSPHRASE = "test-scheduler-passphrase"


def _make_sync_engine(tmp_path: Path, name: str = "apsched.db") -> Path:
    """Return the SQLCipher DB path for scheduler tests."""
    return tmp_path / name


# ---------------------------------------------------------------------------
# 1. build_scheduler shape
# ---------------------------------------------------------------------------


def test_build_scheduler_wires_sqlalchemy_jobstore(tmp_path: Path) -> None:
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    db = _make_sync_engine(tmp_path)
    engine = get_sync_engine(db, _PASSPHRASE)
    try:
        sched = build_scheduler(engine)
        assert isinstance(sched, AsyncIOScheduler)
        store = sched._jobstores["default"]
        assert isinstance(store, SQLAlchemyJobStore)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 2-3. schedule_strategy_daily + CronTrigger shape
# ---------------------------------------------------------------------------


def test_schedule_strategy_daily_uses_cron_trigger_and_deterministic_id(
    tmp_path: Path,
) -> None:
    from apscheduler.triggers.cron import CronTrigger

    db = _make_sync_engine(tmp_path)
    engine = get_sync_engine(db, _PASSPHRASE)
    try:
        sched = build_scheduler(engine)
        # Don't start() — only the in-memory job state is needed for shape.
        job_id = schedule_strategy_daily(
            sched,
            user_id="alice",
            strategy_name="ai-infra",
            schedule_time="10:00 America/New_York",
        )
        assert job_id == "run-alice-ai-infra"
        job = sched.get_job(job_id)
        assert job is not None
        assert isinstance(job.trigger, CronTrigger)
        # CronTrigger stores fields as ListField objects; render via str().
        trigger_str = str(job.trigger)
        assert "hour='10'" in trigger_str
        assert "minute='0'" in trigger_str
        # CronTrigger.__str__ omits the timezone — check the attribute directly.
        assert str(job.trigger.timezone) == "America/New_York"
        # And the call kwargs carry source="schedule".
        assert job.kwargs["source"] == "schedule"
        assert job.kwargs["user_id"] == "alice"
        assert job.kwargs["strategy_name"] == "ai-infra"
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 4. replace_existing
# ---------------------------------------------------------------------------


async def test_schedule_strategy_daily_replaces_existing(tmp_path: Path) -> None:
    db = _make_sync_engine(tmp_path)
    engine = get_sync_engine(db, _PASSPHRASE)
    try:
        sched = build_scheduler(engine)
        # Start the scheduler (paused) so add_job writes directly to the
        # SQLAlchemyJobStore instead of queuing as a pending job —
        # `replace_existing` only dedupes against the jobstore, not the
        # in-memory pending list.
        sched.start(paused=True)
        try:
            schedule_strategy_daily(
                sched,
                user_id="alice",
                strategy_name="ai-infra",
                schedule_time="10:00 America/New_York",
            )
            schedule_strategy_daily(
                sched,
                user_id="alice",
                strategy_name="ai-infra",
                schedule_time="11:30 America/New_York",
            )
            jobs = sched.get_jobs()
            assert len(jobs) == 1
            trigger_str = str(jobs[0].trigger)
            assert "hour='11'" in trigger_str
            assert "minute='30'" in trigger_str
        finally:
            sched.shutdown(wait=False)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 5. unschedule_strategy
# ---------------------------------------------------------------------------


def test_unschedule_strategy_removes_job_and_returns_bool(tmp_path: Path) -> None:
    db = _make_sync_engine(tmp_path)
    engine = get_sync_engine(db, _PASSPHRASE)
    try:
        sched = build_scheduler(engine)
        schedule_strategy_daily(
            sched,
            user_id="alice",
            strategy_name="ai-infra",
            schedule_time="10:00 America/New_York",
        )
        assert unschedule_strategy(sched, user_id="alice", strategy_name="ai-infra")
        assert unschedule_strategy(sched, user_id="alice", strategy_name="ai-infra") is False
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 6. CADENCE-02 wave gate — persistence across "restart"
# ---------------------------------------------------------------------------


async def test_scheduled_job_persists_across_scheduler_restart(tmp_path: Path) -> None:
    """The wave gate test for CADENCE-02.

    Build scheduler1 with sync_engine1, schedule a job, start + shutdown.
    Build scheduler2 with a FRESH sync_engine2 over the SAME DB file +
    passphrase; the previously-scheduled job must be visible in
    ``scheduler2.get_jobs()``.
    """
    db = _make_sync_engine(tmp_path, "apsched_persist.db")

    # ----- Process A: schedule a job, start, shutdown. ---------------------
    engine1 = get_sync_engine(db, _PASSPHRASE)
    try:
        sched1 = build_scheduler(engine1)
        schedule_strategy_daily(
            sched1,
            user_id="bob",
            strategy_name="value-tilt",
            schedule_time="09:30 America/New_York",
        )
        sched1.start(paused=True)  # paused — we don't need a running clock.
        sched1.shutdown(wait=False)
    finally:
        engine1.dispose()

    # ----- Process B: fresh engine, fresh scheduler, same DB. --------------
    engine2 = get_sync_engine(db, _PASSPHRASE)
    try:
        sched2 = build_scheduler(engine2)
        sched2.start(paused=True)
        try:
            jobs = sched2.get_jobs()
            assert any(j.id == "run-bob-value-tilt" for j in jobs), (
                f"expected run-bob-value-tilt to survive restart; got {[j.id for j in jobs]}"
            )
        finally:
            sched2.shutdown(wait=False)
    finally:
        engine2.dispose()


# ---------------------------------------------------------------------------
# 7. tz resolution
# ---------------------------------------------------------------------------


def test_zoneinfo_resolves_america_new_york() -> None:
    """ZoneInfo('America/New_York') must not raise — tzdata required on Windows (Pitfall 5)."""
    tz = ZoneInfo("America/New_York")
    assert tz is not None
    assert str(tz) == "America/New_York"


# ---------------------------------------------------------------------------
# 8. malformed schedule_time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-schedule",  # no space at all
        "10:00 NotARealTz/AtAll",  # bad IANA
        "25:00 America/New_York",  # bad HH
        "10:99 America/New_York",  # bad MM
        "ten:zero America/New_York",  # bad numeric
    ],
)
def test_malformed_schedule_time_raises_value_error(
    tmp_path: Path, bad: str
) -> None:
    db = _make_sync_engine(tmp_path)
    engine = get_sync_engine(db, _PASSPHRASE)
    try:
        sched = build_scheduler(engine)
        with pytest.raises(ValueError):
            schedule_strategy_daily(
                sched,
                user_id="alice",
                strategy_name="bad",
                schedule_time=bad,
            )
        # And no job was added.
        assert sched.get_job("run-alice-bad") is None
    finally:
        engine.dispose()
