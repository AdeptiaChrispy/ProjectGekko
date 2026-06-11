"""APScheduler 3.x AsyncIOScheduler — Plan 01-09 Task 2 (CADENCE-02).

Wires APScheduler's :class:`AsyncIOScheduler` against
:class:`SQLAlchemyJobStore` so daily-fire jobs (D-08) persist across
``gekko serve`` restarts. The Phase 1 wave gate test asserts a job
scheduled in process A is visible in process B after restart.

Cross-platform timezone handling: Python's :mod:`zoneinfo` requires the
``tzdata`` package on Windows (Pitfall 5). The dependency is pinned in
``pyproject.toml`` and :func:`schedule_strategy_daily` raises a clear
:class:`ValueError` if the IANA name can't resolve.

**AUTH-03 / T-01-03-05 invariant.** The job-store engine is built by
:func:`gekko.db.engine.get_sync_engine` — a pre-built ``Engine`` is
handed in via the ``sync_engine`` parameter. The passphrase NEVER
appears in :func:`repr(engine)` or :func:`str(engine.url)`; the
connect-event PRAGMA key handler keeps it in a closure. APScheduler
must NEVER be given a URL string for the SQLCipher DB.

D-22: a single SQLCipher DB hosts both app data and the APScheduler
``apscheduler_jobs`` table without contention (SQLite WAL mode).
APScheduler creates its own table on first ``scheduler.start()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from gekko.logging_config import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.engine import Engine

log = get_logger(__name__)


def build_scheduler(sync_engine: "Engine") -> AsyncIOScheduler:
    """Build a single :class:`AsyncIOScheduler` with SQLAlchemyJobStore.

    :param sync_engine: Pre-built synchronous SQLAlchemy :class:`Engine`
        from :func:`gekko.db.engine.get_sync_engine`. The passphrase
        lives in the engine's connect-event handler closure — never in
        the URL string — per AUTH-03 / T-01-03-05.

    :returns: A configured but NOT yet started scheduler. The caller
        (typically the FastAPI lifespan) calls ``scheduler.start()``
        and ``scheduler.shutdown(wait=False)`` at the appropriate
        startup/shutdown points.

    Per D-22 / RESEARCH §A7, hosting the APScheduler job store in the
    same SQLCipher DB as app data is intentional — single-process,
    single-file, single-passphrase. SQLite WAL handles the modest
    contention between user-driven writes and scheduler-internal
    job-state updates.
    """
    jobstores = {"default": SQLAlchemyJobStore(engine=sync_engine)}
    return AsyncIOScheduler(jobstores=jobstores, timezone="UTC")


def _parse_schedule_time(schedule_time: str) -> tuple[int, int, ZoneInfo]:
    """Parse the ``HH:MM IANA/TZ`` format used by ``Strategy.schedule_time``.

    :raises ValueError: When the format is wrong, the time components
        are out of range, or the IANA timezone is unknown (Pitfall 5 —
        missing ``tzdata`` on Windows).
    """
    try:
        time_part, tz_part = schedule_time.rsplit(" ", 1)
    except ValueError as exc:
        msg = (
            f"Invalid schedule_time format {schedule_time!r}; "
            "expected 'HH:MM IANA/Timezone' (e.g. '10:00 America/New_York')"
        )
        raise ValueError(msg) from exc

    try:
        hh_str, mm_str = time_part.split(":")
        hh, mm = int(hh_str), int(mm_str)
    except (ValueError, AttributeError) as exc:
        msg = (
            f"Invalid HH:MM in schedule_time: {time_part!r} "
            "(expected two integer fields separated by ':')"
        )
        raise ValueError(msg) from exc

    if not (0 <= hh < 24) or not (0 <= mm < 60):
        msg = f"Invalid HH:MM in schedule_time: {time_part!r} (out of range)"
        raise ValueError(msg)

    try:
        tz = ZoneInfo(tz_part)
    except ZoneInfoNotFoundError as exc:
        msg = (
            f"Invalid IANA timezone {tz_part!r} in schedule_time. "
            "On Windows install the `tzdata` package."
        )
        raise ValueError(msg) from exc

    return hh, mm, tz


def schedule_strategy_daily(
    scheduler: AsyncIOScheduler,
    *,
    user_id: str,
    strategy_name: str,
    schedule_time: str,
) -> str:
    """Add (or replace) a daily ``trigger_strategy_run`` job per D-08.

    :param scheduler: A scheduler built via :func:`build_scheduler`.
    :param user_id: Owner of the strategy. Becomes part of the job id
        and is passed positionally into ``trigger_strategy_run``.
    :param strategy_name: Strategy slug. Same.
    :param schedule_time: ``'HH:MM IANA/Timezone'`` string. See
        :func:`_parse_schedule_time` for format rules.

    :returns: The deterministic job id ``f"run-{user_id}-{strategy_name}"``.

    ``replace_existing=True`` — re-scheduling the same strategy updates
    the existing job rather than producing a duplicate.
    """
    hh, mm, tz = _parse_schedule_time(schedule_time)
    job_id = f"run-{user_id}-{strategy_name}"

    # trigger_strategy_run is imported by string id ("module:fn") because
    # APScheduler's SQLAlchemyJobStore pickles the job and re-loads it on
    # the new process during a restart — pickling a bound function ref
    # works in P1 but is fragile across refactors. The string ref is the
    # forward-compatible form.
    scheduler.add_job(
        "gekko.agent.runtime:trigger_strategy_run",
        CronTrigger(hour=hh, minute=mm, timezone=tz),
        kwargs={
            "user_id": user_id,
            "strategy_name": strategy_name,
            "source": "schedule",
        },
        id=job_id,
        replace_existing=True,
    )
    log.info(
        "scheduler.job.added",
        job_id=job_id,
        user_id=user_id,
        strategy_name=strategy_name,
        schedule_time=schedule_time,
    )
    return job_id


def unschedule_strategy(
    scheduler: AsyncIOScheduler,
    *,
    user_id: str,
    strategy_name: str,
) -> bool:
    """Remove a scheduled job by ``(user_id, strategy_name)``.

    :returns: ``True`` if the job existed and was removed, ``False``
        otherwise. Best-effort — APScheduler raises if the job id is
        unknown; we catch and return False so callers can drop the
        "is this scheduled?" check.
    """
    job_id = f"run-{user_id}-{strategy_name}"
    try:
        scheduler.remove_job(job_id)
        log.info(
            "scheduler.job.removed",
            job_id=job_id,
            user_id=user_id,
            strategy_name=strategy_name,
        )
        return True
    except Exception:  # noqa: BLE001 - best-effort
        return False


__all__: tuple[str, ...] = (
    "build_scheduler",
    "schedule_strategy_daily",
    "unschedule_strategy",
)
