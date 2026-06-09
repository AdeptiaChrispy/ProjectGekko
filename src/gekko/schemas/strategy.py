"""Strategy + HardCaps + Guidance Pydantic models — Plan 01-06 Task 1.

The canonical shapes for D-01 (minimal v1 strategy fields), D-05 (snapshot-row
versioning), D-08 (schedule_time IANA-tz format), STRAT-03 / RES-08 (Guidance),
and STRAT-06 (paper/live mode flag).

Plans 01-07 (agent runtime), 01-08 (Slack/executor), and 01-09 (CLI/dashboard)
all import these models. The shapes are LOCKED — any change here must be
forward-compatible.

References:
  * .planning/phases/01-foundation.../01-CONTEXT.md  D-01, D-02, D-05, D-06, D-08, STRAT-03..06
  * .planning/phases/01-foundation.../01-RESEARCH.md  §"Code Examples — Strategy Pydantic model"
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# HardCaps (D-01)
# ---------------------------------------------------------------------------


class HardCaps(BaseModel):
    """Per-strategy hard caps (D-01).

    Four bounded Decimal/int knobs the Researcher + Decision + OrderGuard
    layers all respect. The schema enforces the bounds at validation time so a
    malformed strategy never reaches the broker.

    * ``max_position_pct`` — fraction (0..0.20] of account equity for any
      single position. The 20% ceiling is the defensive cap per RESEARCH
      §"Code Examples"; concentrating more than 20% in one name is an
      architectural smell and the schema rejects.
    * ``max_daily_loss_usd`` — absolute dollar loss across the strategy in
      one day. Strictly positive Decimal.
    * ``max_trades_per_day`` — strictly positive integer.
    * ``max_sector_exposure_pct`` — fraction (0..1] of account equity in any
      one sector.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    max_position_pct: Decimal = Field(..., gt=Decimal("0"), le=Decimal("0.20"))
    max_daily_loss_usd: Decimal = Field(..., gt=Decimal("0"))
    max_trades_per_day: int = Field(..., ge=1)
    max_sector_exposure_pct: Decimal = Field(..., gt=Decimal("0"), le=Decimal("1"))


# ---------------------------------------------------------------------------
# Strategy (D-01, D-05, STRAT-06)
# ---------------------------------------------------------------------------


def _validate_schedule_time(v: str | None) -> str | None:
    """Validate ``schedule_time`` of the form ``"HH:MM IANA/Timezone"``.

    Returns the input unchanged on success; raises ``ValueError`` if either
    the time part is out of range or the IANA timezone is unknown.

    Per CONTEXT D-08: the schedule_time format is ``"10:00 America/New_York"``
    or similar. Validation uses ``zoneinfo.ZoneInfo`` which depends on the
    ``tzdata`` package on Windows (declared in pyproject per Pitfall 5).
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if v is None:
        return v
    try:
        time_part, tz_part = v.rsplit(" ", 1)
    except ValueError as exc:
        msg = f"schedule_time must be 'HH:MM IANA/Tz', got: {v!r}"
        raise ValueError(msg) from exc
    try:
        hh_str, mm_str = time_part.split(":")
        hh, mm = int(hh_str), int(mm_str)
    except ValueError as exc:
        msg = f"schedule_time has malformed HH:MM: {v!r}"
        raise ValueError(msg) from exc
    if not (0 <= hh < 24) or not (0 <= mm < 60):
        msg = f"schedule_time hour/minute out of range: {v!r}"
        raise ValueError(msg)
    try:
        ZoneInfo(tz_part)
    except ZoneInfoNotFoundError as exc:
        msg = f"schedule_time tz not found: {tz_part!r}"
        raise ValueError(msg) from exc
    return v


class Strategy(BaseModel):
    """Strategy snapshot row (D-01, D-05).

    Each save of a strategy inserts a new row keyed by ``(user_id,
    strategy_name, version)`` in the ``strategies`` table (D-05). This Pydantic
    model is the canonical in-memory shape; ``payload_json`` on the DB row is
    the result of ``model_dump_json()``.

    Forward-compatibility note: future phases may add optional fields
    (exclude_list, per_position_risk, ...). DO NOT remove or rename existing
    fields without a coordinated migration — the snapshot rows on disk encode
    this exact shape.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    strategy_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=64)
    version: int = Field(..., ge=1)
    thesis: str = Field(..., min_length=1, max_length=2000)
    watchlist: list[str] = Field(..., min_length=1, max_length=50)
    hard_caps: HardCaps
    schedule_time: str | None = None
    # STRAT-06: paper-only in P1 per D-24; live flip requires UI confirmation
    # enforced by Plan 01-09 dashboard. The schema accepts both.
    mode: Literal["paper", "live"] = "paper"
    # Provenance flag — STRAT-01 NL chat vs STRAT-02 form authoring.
    created_by_chat: bool = False
    created_at: str

    @field_validator("watchlist")
    @classmethod
    def _normalize_watchlist(cls, v: list[str]) -> list[str]:
        """Uppercase + strip + deduplicate (preserve first-seen order)."""
        seen: set[str] = set()
        out: list[str] = []
        for raw in v:
            ticker = raw.upper().strip()
            if not ticker:
                msg = "watchlist contains empty ticker"
                raise ValueError(msg)
            if ticker in seen:
                continue
            seen.add(ticker)
            out.append(ticker)
        return out

    @field_validator("schedule_time")
    @classmethod
    def _validate_schedule_time(cls, v: str | None) -> str | None:
        return _validate_schedule_time(v)


# ---------------------------------------------------------------------------
# Guidance (RES-08, STRAT-03)
# ---------------------------------------------------------------------------


class Guidance(BaseModel):
    """Ad-hoc user guidance — STRAT-03 / RES-08.

    Captured via the dashboard or CLI; the Researcher subagent's prompt
    injects active (non-expired) Guidance rows so the agent honors short-lived
    user direction without modifying the underlying Strategy.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    guidance_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    strategy_id: str | None = None
    text: str = Field(..., min_length=1, max_length=2000)
    scope: Literal["strategy", "global"]
    created_at: str
    expires_at: str | None = None


# ---------------------------------------------------------------------------
# Snapshot versioning helper (D-05)
# ---------------------------------------------------------------------------


async def next_version(
    session: AsyncSession,
    *,
    user_id: str,
    strategy_name: str,
) -> int:
    """Return the next snapshot version for ``(user_id, strategy_name)``.

    Queries ``SELECT MAX(version) FROM strategies WHERE user_id=:uid AND
    strategy_name=:sn``. Returns ``1`` if no row exists, ``max+1`` otherwise.

    This is the deterministic snapshot-row versioning helper (D-05). Plan
    01-09's strategy create/save endpoint calls this BEFORE inserting the new
    row; the inserted row carries the returned version.

    The function is a pure read — callers are responsible for the subsequent
    INSERT and for the ``BEGIN IMMEDIATE`` that serializes concurrent writers
    if any future plan introduces parallel strategy editors.
    """
    from sqlalchemy import func, select

    from gekko.db.models import Strategy as StrategyRow

    stmt = select(func.max(StrategyRow.version)).where(
        StrategyRow.user_id == user_id,
        StrategyRow.strategy_name == strategy_name,
    )
    current = (await session.execute(stmt)).scalar_one_or_none()
    return (current or 0) + 1


__all__: tuple[str, ...] = (
    "Guidance",
    "HardCaps",
    "Strategy",
    "next_version",
)
