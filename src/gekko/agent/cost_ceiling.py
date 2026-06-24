"""Deterministic daily cost-ceiling guard — Plan 04-03 Task 1 (COST-01/COST-04).

Provides :func:`check_cost_ceiling`, the single predicate that answers
"how much of today's LLM cost ceiling has the user consumed, and what
should the agent do?" (allow / degrade / halt).

Design decisions (D-01, D-02, D-03, D-06, D-07, D-08, D-09):

* **Per-user pooled** (D-01): sums ALL ``llm_cost`` events for the user
  across all strategies.  One ceiling covers the whole account.
* **Configurable ceiling** (D-02): ``User.daily_cost_ceiling_usd`` is TEXT
  (money-as-TEXT pattern).  ``None`` or empty falls back to
  :data:`gekko.agent.pricing.DEFAULT_DAILY_CEILING_USD` ($5.00).
* **Timezone-midnight reset** (D-03): uses ``User.timezone`` (IANA name,
  defaults to ``"America/New_York"``).  DO NOT add a second timezone field.
* **Two-tier action** (D-07/D-04):
    - pct >= 100% → ``halt``   — hard stop, no LLM calls dispatched
    - pct >= 80%  → ``degrade`` — slower cadence, Haiku triage gate
    - pct <  80%  → ``allow``   — proceed normally
* **One DM per day** (D-06/D-08): ``just_crossed_80`` / ``just_crossed_100``
  are True on the FIRST call that tips over a threshold; the column
  ``cost_alert_*_sent_date`` is updated and **committed** via the
  ``session.begin()`` context manager so the next call returns False.
  No repeat DM spam.
* **Deterministic** (T-04-05): no LLM calls anywhere in this module.
  The agent CANNOT reason past this gate.

Analog: :mod:`gekko.approval.quiet_hours` — same ``_get_session_factory``
test seam, same ZoneInfo pattern, same deterministic pre-LLM gate shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from gekko.db.models import Event, User
from gekko.logging_config import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Test seam — mirrors the _get_session_factory pattern in quiet_hours.py
# ---------------------------------------------------------------------------


def _get_session_factory(user_id: str):  # type: ignore[return]
    """Build a session factory + owning engine for ``user_id``.

    Mirrors the same indirection used by :mod:`gekko.approval.quiet_hours`
    so tests have a single seam to monkeypatch (PATTERNS §2d).
    """
    from gekko.config import get_settings
    from gekko.db.engine import get_async_engine
    from gekko.db.session import make_session_factory
    from gekko.vault.passphrase import get_passphrase as _get_passphrase

    settings = get_settings()
    engine = get_async_engine(settings.db_path_for(user_id), _get_passphrase())
    return make_session_factory(engine), engine


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class CeilingCheck:
    """Result of :func:`check_cost_ceiling`.

    :param action: ``"allow"`` / ``"degrade"`` / ``"halt"`` — what the
        orchestrator should do.  Derived from :attr:`pct`.
    :param current_spend: Today's total LLM spend in USD (Decimal, per-user
        pooled, tz-midnight reset).
    :param ceiling: The effective daily ceiling in USD (from DB or fallback).
    :param pct: ``current_spend / ceiling * 100`` as a Decimal (0-100+).
    :param just_crossed_80: True ONLY on the first cycle that tips ``pct``
        above 80%.  False on all subsequent cycles in the same day, and
        whenever ``pct < 80``.
    :param just_crossed_100: True ONLY on the first cycle that tips ``pct``
        above 100%.  False on all subsequent same-day halt returns.
    """

    action: Literal["allow", "degrade", "halt"]
    current_spend: Decimal
    ceiling: Decimal
    pct: Decimal
    just_crossed_80: bool
    just_crossed_100: bool


# ---------------------------------------------------------------------------
# Public guard function
# ---------------------------------------------------------------------------


async def check_cost_ceiling(
    session_factory: object,
    *,
    user_id: str,
) -> CeilingCheck:
    """Return a :class:`CeilingCheck` for ``user_id``.

    **Deterministic** — this function NEVER calls the LLM.  It reads from
    the DB, does pure Python Decimal arithmetic, and returns a dataclass.

    :param session_factory: An async SQLAlchemy session factory.  When
        ``None`` (or not supplied as a keyword argument) the production code
        uses :func:`_get_session_factory` to build one — tests inject their
        own factory directly.
    :param user_id: The internal Gekko user id.
    :returns: A :class:`CeilingCheck` with ``action``, spend, pct, and
        the ``just_crossed_*`` DM-gate flags.
    """
    from gekko.agent.pricing import DEFAULT_DAILY_CEILING_USD

    own_engine = False
    engine = None

    if session_factory is None:
        session_factory, engine = _get_session_factory(user_id)
        own_engine = True

    try:
        async with session_factory() as session, session.begin():  # type: ignore[attr-defined]
            # --- Load User row -------------------------------------------
            user: User | None = await session.get(User, user_id)
            if user is None:
                log.warning("cost_ceiling.user_not_found", user_id=user_id)
                # Fail-open: return allow so the agent runs rather than
                # silently halting due to a missing user row.
                return CeilingCheck(
                    action="allow",
                    current_spend=Decimal("0"),
                    ceiling=DEFAULT_DAILY_CEILING_USD,
                    pct=Decimal("0"),
                    just_crossed_80=False,
                    just_crossed_100=False,
                )

            # --- Ceiling -------------------------------------------------
            ceiling_str = user.daily_cost_ceiling_usd
            if ceiling_str:
                try:
                    ceiling = Decimal(ceiling_str)
                except Exception:
                    ceiling = DEFAULT_DAILY_CEILING_USD
            else:
                ceiling = DEFAULT_DAILY_CEILING_USD

            # Guard against zero/negative ceiling (should never happen but
            # dividing by zero is worse than a wrong percentage).
            if ceiling <= Decimal("0"):
                ceiling = DEFAULT_DAILY_CEILING_USD

            # --- Today's start in user's timezone (D-03) -----------------
            tz_name: str = user.timezone or "America/New_York"
            try:
                tz = ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                log.warning(
                    "cost_ceiling.invalid_timezone",
                    user_id=user_id,
                    tz_name=tz_name,
                )
                tz = ZoneInfo("America/New_York")

            now_utc = datetime.now(UTC)
            now_local = now_utc.astimezone(tz)
            today_start_local = datetime(
                now_local.year, now_local.month, now_local.day, tzinfo=tz
            )
            today_start_utc_str = today_start_local.astimezone(UTC).isoformat()

            # --- Fetch today's llm_cost events (per-user pooled / D-01) --
            rows = (
                await session.execute(
                    select(Event.payload_json).where(
                        Event.user_id == user_id,
                        Event.event_type == "llm_cost",
                        Event.ts >= today_start_utc_str,
                    )
                )
            ).all()

            # Sum in Python using Decimal (RESEARCH §RQ-3 — do NOT use
            # SQL json_extract/SUM which returns TEXT in SQLite).
            current_spend = Decimal("0")
            for (payload_json,) in rows:
                try:
                    payload = json.loads(payload_json)
                    # payload_json is the full canonical-subset string
                    # ({"event_type":...,"payload":...,"ts":...,"user_id":...})
                    # The actual cost_usd lives in the inner "payload" dict.
                    inner = payload.get("payload", payload)
                    cost_str = inner.get("cost_usd", "0")
                    current_spend += Decimal(str(cost_str))
                except Exception:
                    log.warning(
                        "cost_ceiling.bad_cost_row",
                        user_id=user_id,
                        raw=payload_json[:200],
                    )

            # --- Tier computation ----------------------------------------
            pct = (current_spend / ceiling) * Decimal("100")

            if pct >= Decimal("100"):
                action: Literal["allow", "degrade", "halt"] = "halt"
            elif pct >= Decimal("80"):
                action = "degrade"
            else:
                action = "allow"

            # --- "One DM" gate (D-06/D-08) ------------------------------
            today_local_date_str = now_local.date().isoformat()  # YYYY-MM-DD

            just_crossed_80 = False
            just_crossed_100 = False

            if action in ("degrade", "halt"):
                if user.cost_alert_80_sent_date != today_local_date_str:
                    just_crossed_80 = True
                    user.cost_alert_80_sent_date = today_local_date_str

            if action == "halt":
                if user.cost_alert_100_sent_date != today_local_date_str:
                    just_crossed_100 = True
                    user.cost_alert_100_sent_date = today_local_date_str

            # Flush the updated date columns if anything changed.
            if just_crossed_80 or just_crossed_100:
                await session.flush()

    finally:
        if own_engine and engine is not None:
            await engine.dispose()

    return CeilingCheck(
        action=action,
        current_spend=current_spend,
        ceiling=ceiling,
        pct=pct,
        just_crossed_80=just_crossed_80,
        just_crossed_100=just_crossed_100,
    )


__all__: tuple[str, ...] = ("CeilingCheck", "check_cost_ceiling")
