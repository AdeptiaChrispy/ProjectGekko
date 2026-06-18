"""APScheduler sweep persistence + coalesce restart test — Plan 03-04 Task 3 (HITL-03).

Integration test covering:
(a) register_expire_stale_sweep adds the job to the jobstore
(b) job persists across scheduler restart (SQLAlchemyJobStore round-trip)
(c) re-registering with replace_existing=True does not create a duplicate
(d) Registered job carries coalesce=True, max_instances=1, misfire_grace_time=300
(e) Job references expire_stale_proposals via module:fn string ref (pickle-safe)

Mirrors the shape of ``tests/integration/test_scheduler_persistence.py``
(Plan 01-09) — same sync-engine factory pattern, same scheduler lifecycle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gekko.db.engine import get_sync_engine
from gekko.scheduler.jobs import (
    build_scheduler,
    register_daily_pnl_cron,
    register_expire_stale_sweep,
)

pytestmark = pytest.mark.integration


_PASSPHRASE = "test-sweep-persistence"  # nosec: test-only
_USER_ID = "test-sweep-user"


# ---------------------------------------------------------------------------
# Helper — mirrors _make_sync_engine in test_scheduler_persistence.py
# ---------------------------------------------------------------------------


def _make_db_path(tmp_path: Path, name: str = "sweep_test.db") -> Path:
    """Return the path to a temp SQLCipher DB file (does not create it)."""
    return tmp_path / name


# ---------------------------------------------------------------------------
# (a) + (b) Job added and persists across restart
# ---------------------------------------------------------------------------


async def test_sweep_job_persists_across_scheduler_restart(tmp_path: Path) -> None:
    """Job registered via register_expire_stale_sweep persists in jobstore across restart."""
    db = _make_db_path(tmp_path, "sweep_persist.db")

    # Process A: register the sweep, start (paused), shutdown.
    engine1 = get_sync_engine(db, _PASSPHRASE)
    try:
        scheduler1 = build_scheduler(engine1)
        scheduler1.start(paused=True)  # paused: no jobs fire, just jobstore writes
        try:
            job_id = register_expire_stale_sweep(scheduler1, user_id=_USER_ID)
            assert job_id == f"expire-stale-{_USER_ID}"

            job = scheduler1.get_job(job_id)
            assert job is not None, f"Job {job_id!r} not found after registration"
            assert job.coalesce is True
            assert job.max_instances == 1
            assert job.misfire_grace_time == 300
        finally:
            scheduler1.shutdown(wait=False)
    finally:
        engine1.dispose()

    # Process B: fresh engine over SAME DB file — job must survive.
    engine2 = get_sync_engine(db, _PASSPHRASE)
    try:
        scheduler2 = build_scheduler(engine2)
        scheduler2.start(paused=True)
        try:
            job_after_restart = scheduler2.get_job(job_id)
            assert job_after_restart is not None, (
                f"Job {job_id!r} not found after restart — SQLAlchemyJobStore did not persist it"
            )
        finally:
            scheduler2.shutdown(wait=False)
    finally:
        engine2.dispose()


# ---------------------------------------------------------------------------
# (c) replace_existing=True — no duplicate on double-register
# ---------------------------------------------------------------------------


async def test_sweep_job_replace_existing_no_duplicate(tmp_path: Path) -> None:
    """Registering the same sweep twice with replace_existing=True produces one job."""
    db = _make_db_path(tmp_path)
    engine = get_sync_engine(db, _PASSPHRASE)
    try:
        scheduler = build_scheduler(engine)
        scheduler.start(paused=True)
        try:
            job_id_1 = register_expire_stale_sweep(scheduler, user_id=_USER_ID)
            job_id_2 = register_expire_stale_sweep(scheduler, user_id=_USER_ID)

            assert job_id_1 == job_id_2  # same deterministic id

            # Exactly ONE job for this user in the scheduler.
            all_jobs = scheduler.get_jobs()
            sweep_jobs = [j for j in all_jobs if j.id == f"expire-stale-{_USER_ID}"]
            assert len(sweep_jobs) == 1, (
                f"Expected 1 sweep job, got {len(sweep_jobs)}: {[j.id for j in sweep_jobs]}"
            )
        finally:
            scheduler.shutdown(wait=False)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# (d) coalesce=True semantics — job carries correct restart-safe flags
# ---------------------------------------------------------------------------


async def test_sweep_job_coalesce_flag_set(tmp_path: Path) -> None:
    """Registered job carries coalesce=True, max_instances=1, misfire_grace_time=300."""
    db = _make_db_path(tmp_path)
    engine = get_sync_engine(db, _PASSPHRASE)
    try:
        scheduler = build_scheduler(engine)
        scheduler.start(paused=True)
        try:
            job_id = register_expire_stale_sweep(scheduler, user_id=_USER_ID)
            job = scheduler.get_job(job_id)
            assert job is not None

            # These knobs are what make the job restart-safe.
            assert job.coalesce is True, "coalesce must be True (piled-up missed fires collapse to one)"
            assert job.max_instances == 1, "max_instances must be 1 (no overlapping runs)"
            assert job.misfire_grace_time == 300, (
                "misfire_grace_time must be 300s (5 min catch-up window after restart)"
            )
        finally:
            scheduler.shutdown(wait=False)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# (e) String ref format — job func resolves to the correct callable
# ---------------------------------------------------------------------------


async def test_sweep_job_uses_module_fn_string_ref(tmp_path: Path) -> None:
    """Registered job references expire_stale_proposals via 'module:fn' string ref."""
    db = _make_db_path(tmp_path)
    engine = get_sync_engine(db, _PASSPHRASE)
    try:
        scheduler = build_scheduler(engine)
        scheduler.start(paused=True)
        try:
            job_id = register_expire_stale_sweep(scheduler, user_id=_USER_ID)
            job = scheduler.get_job(job_id)
            assert job is not None

            # APScheduler resolves the module:fn string ref to a callable.
            # job.func should resolve to expire_stale_proposals.
            from gekko.approval.expiry import expire_stale_proposals

            # APScheduler 3.x: job.func is the callable (already resolved from string ref
            # when the scheduler is running).
            assert job.func is expire_stale_proposals, (
                f"Job func should be expire_stale_proposals, got {job.func}"
            )
        finally:
            scheduler.shutdown(wait=False)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# (f) daily_pnl cron job — added in Plan 03-06 Task 2
# ---------------------------------------------------------------------------


async def test_daily_pnl_cron_job_registered(tmp_path: Path) -> None:
    """register_daily_pnl_cron adds the daily P&L cron job to the scheduler."""
    db = _make_db_path(tmp_path, "daily_pnl_persist.db")
    engine = get_sync_engine(db, _PASSPHRASE)
    try:
        scheduler = build_scheduler(engine)
        scheduler.start(paused=True)
        try:
            job_id = register_daily_pnl_cron(scheduler, user_id=_USER_ID)
            assert job_id == f"daily-pnl-{_USER_ID}"

            job = scheduler.get_job(job_id)
            assert job is not None, f"Job {job_id!r} not found after registration"
            assert job.coalesce is True
            assert job.max_instances == 1

            # Module:fn string ref resolves to send_daily_pnl_digest.
            from gekko.reporter.daily_pnl import send_daily_pnl_digest

            assert job.func is send_daily_pnl_digest, (
                f"Job func should be send_daily_pnl_digest, got {job.func}"
            )
        finally:
            scheduler.shutdown(wait=False)
    finally:
        engine.dispose()


async def test_daily_pnl_cron_persists_across_restart(tmp_path: Path) -> None:
    """daily_pnl cron job persists in jobstore across scheduler restart."""
    db = _make_db_path(tmp_path, "daily_pnl_restart.db")

    # Process A: register, start paused, shutdown.
    engine1 = get_sync_engine(db, _PASSPHRASE)
    try:
        scheduler1 = build_scheduler(engine1)
        scheduler1.start(paused=True)
        try:
            job_id = register_daily_pnl_cron(scheduler1, user_id=_USER_ID)
        finally:
            scheduler1.shutdown(wait=False)
    finally:
        engine1.dispose()

    # Process B: fresh engine over SAME DB — job must survive.
    engine2 = get_sync_engine(db, _PASSPHRASE)
    try:
        scheduler2 = build_scheduler(engine2)
        scheduler2.start(paused=True)
        try:
            job_after_restart = scheduler2.get_job(job_id)
            assert job_after_restart is not None, (
                f"Daily P&L cron job {job_id!r} not found after restart"
            )
        finally:
            scheduler2.shutdown(wait=False)
    finally:
        engine2.dispose()
