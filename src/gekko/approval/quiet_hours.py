"""Quiet-hours predicate — Plan 03-03 Task 1 (HITL-05).

Provides :func:`_resolve_quiet_hours`, the single predicate that answers
"should the agent skip this scheduled cycle (or suppress this DM) because
the operator is in a configured quiet-hours window?".

Design decisions (D-46, D-47, D-49):

* **Strategy override wins** (D-47): when a strategy carries BOTH
  ``quiet_hours_start`` and ``quiet_hours_end``, those values supersede the
  user-level window. A half-set strategy (only one endpoint non-null) is
  treated as *not set* and falls back to the user window.
* **User-timezone** (D-49): ``User.timezone`` is the IANA timezone string for
  tz-conversion. Defaults to ``"America/New_York"`` when ``None``. The
  strategy does NOT carry its own timezone (D-47 design decision).
* **DST safety**: ``zoneinfo`` handles DST transitions implicitly (Plan 01-08
  Pitfall 5 / ``tzdata`` pinned in ``pyproject.toml`` for Windows).
* **Overnight wrap**: when ``start > end`` (e.g. 22:00 – 07:00) the window
  spans midnight.  In-window iff ``local_now.time() >= start OR
  local_now.time() < end``.
* **Pure predicate**: no side effects, no audit events, no DMs.  The caller
  decides what to do with True / False.
* **No SDK import**: the module deliberately has no Agent SDK or LLM provider
  dependency — it is a deterministic Python firewall per the grep gate in
  ``tests/unit/test_executor.py``.
"""

from __future__ import annotations

import json
from datetime import datetime
from datetime import time as _time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from gekko.db.models import Strategy as StrategyRow
from gekko.db.models import User
from gekko.logging_config import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Test seam — mirrors the _get_session_factory pattern in executor.py (PATTERNS §2d)
# ---------------------------------------------------------------------------


def _get_session_factory(user_id: str):  # type: ignore[return]
    """Build a session factory + owning engine for ``user_id``.

    Mirrors the same indirection used by :mod:`gekko.execution.executor`
    so tests have a single seam to monkeypatch (PATTERNS §2d).
    """
    from gekko.config import get_settings
    from gekko.db.engine import get_async_engine
    from gekko.db.session import make_session_factory
    from gekko.vault.passphrase import get_passphrase as _get_passphrase

    settings = get_settings()
    engine = get_async_engine(
        settings.db_path_for(user_id), _get_passphrase()
    )
    return make_session_factory(engine), engine


# ---------------------------------------------------------------------------
# Predicate
# ---------------------------------------------------------------------------


async def _resolve_quiet_hours(
    user_id: str,
    now: datetime,
    strategy_name: str | None = None,
) -> bool:
    """Return ``True`` iff ``now`` falls in the operative quiet-hours window.

    Loads the :class:`User` row by ``user_id`` and (optionally) the
    :class:`Strategy` row by ``strategy_name`` to determine the operative
    window per D-47 precedence (strategy override wins when BOTH endpoints
    are set; otherwise falls back to user window).

    :param user_id: The internal gekko user id (FK to ``users.user_id``).
    :param now: An **aware** UTC datetime representing "now".  Must carry
        timezone info so ``.astimezone(ZoneInfo(...))`` works correctly.
    :param strategy_name: Optional strategy name.  When supplied and the
        strategy row carries BOTH quiet-hours endpoints, those values
        override the user-level window (D-47).
    :returns: ``True`` when ``now`` (converted to the user's local TZ) falls
        within the operative quiet-hours window; ``False`` otherwise.
    :raises ValueError: When ``User.timezone`` is non-null but not a valid
        IANA timezone name (T-03-03-01 — whitelist via
        ``ZoneInfoNotFoundError``).
    """
    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            # Load User row.
            user: User | None = await session.get(User, user_id)
            if user is None:
                log.warning(
                    "quiet_hours.user_not_found",
                    user_id=user_id,
                )
                return False

            # Load Strategy row by (user_id, strategy_name) if requested.
            strategy_row: StrategyRow | None = None
            if strategy_name is not None:
                strategy_row = (
                    await session.execute(
                        select(StrategyRow)
                        .where(
                            StrategyRow.user_id == user_id,
                            StrategyRow.strategy_name == strategy_name,
                        )
                        .order_by(StrategyRow.version.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()

            # Snapshot the relevant columns before closing the session so we
            # don't hold a DB connection during ZoneInfo arithmetic.
            user_start: str | None = user.quiet_hours_start
            user_end: str | None = user.quiet_hours_end
            tz_name: str = user.timezone or "America/New_York"

            # Extract strategy-level quiet hours from payload_json if available.
            strategy_start: str | None = None
            strategy_end: str | None = None
            if strategy_row is not None and strategy_row.payload_json:
                try:
                    payload = json.loads(strategy_row.payload_json)
                    strategy_start = payload.get("quiet_hours_start")
                    strategy_end = payload.get("quiet_hours_end")
                except Exception:  # noqa: BLE001
                    pass

    finally:
        if engine is not None:
            await engine.dispose()

    # --- Timezone resolution (PATTERNS §2h) ----------------------------------
    # T-03-03-01: validate IANA tz string via ZoneInfoNotFoundError whitelist.
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        msg = f"Invalid IANA timezone {tz_name!r} for user {user_id}"
        raise ValueError(msg) from exc

    # --- Strategy-override precedence (D-47) ---------------------------------
    # "set" means BOTH endpoints non-null. A half-set strategy falls back to
    # the user window.
    if strategy_start is not None and strategy_end is not None:
        window_start_str = strategy_start
        window_end_str = strategy_end
    else:
        window_start_str = user_start
        window_end_str = user_end

    # If neither the user nor the strategy has a window configured, return
    # False: agent runs 24/7.
    if window_start_str is None or window_end_str is None:
        return False

    # --- Window comparison ---------------------------------------------------
    # Convert ``now`` to the user's local wall clock.
    local_now = now.astimezone(tz)
    local_time = local_now.time()

    # Parse window endpoints.  The DB stores HH:MM or HH:MM:SS strings.
    start = _time.fromisoformat(window_start_str)
    end = _time.fromisoformat(window_end_str)

    # Overnight wrap (start > end): window spans midnight.
    # In-window iff: local_time >= start  OR  local_time < end
    if start > end:
        return local_time >= start or local_time < end

    # Same-day window (start <= end): in-window iff start <= local_time < end.
    return start <= local_time < end
