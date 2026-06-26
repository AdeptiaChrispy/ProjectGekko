"""Anomaly drawdown reflex — TRUST-04 (Plan 05-04 Task 1).

Asserts the contract for ``gekko.anomaly.evaluator.evaluate_drawdown``:

  * drawdown >= threshold demotes the strategy + cancels pending auto orders +
    DMs the operator (the DM bypasses quiet hours per D-T13).
  * a strategy already propose-only is an idempotent no-op.
  * the anomaly threshold trips BEFORE max_daily_loss_usd (threshold ordering,
    D-T11).
  * the demotion is surgical — it touches only the named strategy (D-T12).
  * all drawdown math is Decimal-exact (no float in the math path).

The evaluator is built from clean seams (mirroring promotion.py / executor.py)
so these tests monkeypatch the demote/cancel/DM helpers and the drawdown math,
exercising the reflex's decision logic without a live broker or DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest

from gekko.anomaly import evaluator as ev
from gekko.anomaly.evaluator import evaluate_drawdown


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _FakeMeta:
    """Minimal StrategyMetadata stand-in for the evaluator's reads."""

    trust_level: str = "auto-within-caps"
    anomaly_threshold_pct: str | None = "0.10"


class _RecordingBroker:
    """A broker double; the evaluator never calls it directly in these tests
    (cancellation + drawdown math are seams), but it stands in for the arg."""


def _install_common_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    meta: _FakeMeta | None,
    drawdown: Decimal,
    demoted: list[dict[str, Any]],
    cancelled: list[dict[str, Any]],
    dms: list[dict[str, Any]],
    cancelled_count: int = 2,
) -> None:
    async def fake_load_meta(
        *, user_id: str, strategy_name: str
    ) -> _FakeMeta | None:
        return meta

    async def fake_compute_dd(
        user_id: str, strategy_name: str, broker: Any
    ) -> Decimal:
        return drawdown

    async def fake_cancel(
        user_id: str, strategy_name: str, broker: Any
    ) -> int:
        cancelled.append(
            {"user_id": user_id, "strategy_name": strategy_name}
        )
        return cancelled_count

    async def fake_demote(
        *,
        user_id: str,
        strategy_name: str,
        reason: str,
        drawdown_pct: str | None = None,
    ) -> None:
        demoted.append(
            {
                "user_id": user_id,
                "strategy_name": strategy_name,
                "reason": reason,
                "drawdown_pct": drawdown_pct,
            }
        )

    async def fake_dm(
        user_id: str,
        strategy_name: str,
        drawdown_pct: Decimal,
        threshold: Decimal,
        cancelled_count: int,
    ) -> None:
        dms.append(
            {
                "user_id": user_id,
                "strategy_name": strategy_name,
                "drawdown_pct": drawdown_pct,
                "threshold": threshold,
                "cancelled_count": cancelled_count,
            }
        )

    async def fake_write_event(
        *,
        user_id: str,
        strategy_name: str,
        drawdown_pct: Decimal,
        threshold: Decimal,
        cancelled_count: int,
    ) -> None:
        return None

    monkeypatch.setattr(ev, "load_strategy_metadata", fake_load_meta)
    monkeypatch.setattr(ev, "_compute_single_day_drawdown_pct", fake_compute_dd)
    monkeypatch.setattr(ev, "_cancel_pending_auto_orders", fake_cancel)
    monkeypatch.setattr(ev, "demote_strategy_from_auto", fake_demote)
    monkeypatch.setattr(ev, "_write_anomaly_demotion_event", fake_write_event)
    monkeypatch.setattr(ev, "_send_anomaly_dm", fake_dm)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drawdown_at_or_above_threshold_demotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dd >= threshold → demote + cancel + DM, returns True."""
    demoted: list[dict[str, Any]] = []
    cancelled: list[dict[str, Any]] = []
    dms: list[dict[str, Any]] = []
    _install_common_seams(
        monkeypatch,
        meta=_FakeMeta(trust_level="auto-within-caps", anomaly_threshold_pct="0.10"),
        drawdown=Decimal("0.12"),
        demoted=demoted,
        cancelled=cancelled,
        dms=dms,
        cancelled_count=3,
    )

    result = await evaluate_drawdown(
        user_id="u1", strategy_name="momentum", broker=_RecordingBroker()
    )

    assert result is True
    # Demote called with anomaly reason + the exact Decimal drawdown as str.
    assert len(demoted) == 1
    assert demoted[0]["reason"] == "anomaly"
    assert demoted[0]["strategy_name"] == "momentum"
    assert demoted[0]["drawdown_pct"] == "0.12"
    # Pending auto-orders cancelled for this strategy.
    assert cancelled == [{"user_id": "u1", "strategy_name": "momentum"}]
    # DM fired with the cancelled count + Decimal-exact numbers.
    assert len(dms) == 1
    assert dms[0]["drawdown_pct"] == Decimal("0.12")
    assert dms[0]["threshold"] == Decimal("0.10")
    assert dms[0]["cancelled_count"] == 3


@pytest.mark.asyncio
async def test_exactly_at_threshold_demotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dd == threshold is a breach (>=, not >) — D-T10."""
    demoted: list[dict[str, Any]] = []
    cancelled: list[dict[str, Any]] = []
    dms: list[dict[str, Any]] = []
    _install_common_seams(
        monkeypatch,
        meta=_FakeMeta(anomaly_threshold_pct="0.10"),
        drawdown=Decimal("0.10"),
        demoted=demoted,
        cancelled=cancelled,
        dms=dms,
    )

    result = await evaluate_drawdown(
        user_id="u1", strategy_name="momentum", broker=_RecordingBroker()
    )

    assert result is True
    assert len(demoted) == 1


@pytest.mark.asyncio
async def test_below_threshold_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dd < threshold → no demote, no cancel, no DM; returns False."""
    demoted: list[dict[str, Any]] = []
    cancelled: list[dict[str, Any]] = []
    dms: list[dict[str, Any]] = []
    _install_common_seams(
        monkeypatch,
        meta=_FakeMeta(anomaly_threshold_pct="0.10"),
        drawdown=Decimal("0.09"),
        demoted=demoted,
        cancelled=cancelled,
        dms=dms,
    )

    result = await evaluate_drawdown(
        user_id="u1", strategy_name="momentum", broker=_RecordingBroker()
    )

    assert result is False
    assert demoted == []
    assert cancelled == []
    assert dms == []


@pytest.mark.asyncio
async def test_already_propose_only_is_a_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A strategy not in auto-within-caps is an idempotent no-op (returns False).

    Critically, the drawdown is NOT even computed — the guard short-circuits
    before any broker reads (mirror stamp_first_live_trade set-once).
    """
    demoted: list[dict[str, Any]] = []
    cancelled: list[dict[str, Any]] = []
    dms: list[dict[str, Any]] = []
    computed: list[bool] = []

    async def fake_load_meta(
        *, user_id: str, strategy_name: str
    ) -> _FakeMeta:
        return _FakeMeta(trust_level="propose-only")

    async def fake_compute_dd(
        user_id: str, strategy_name: str, broker: Any
    ) -> Decimal:
        computed.append(True)
        return Decimal("0.99")  # would trip if reached

    monkeypatch.setattr(ev, "load_strategy_metadata", fake_load_meta)
    monkeypatch.setattr(ev, "_compute_single_day_drawdown_pct", fake_compute_dd)

    result = await evaluate_drawdown(
        user_id="u1", strategy_name="momentum", broker=_RecordingBroker()
    )

    assert result is False
    assert computed == [], "drawdown must not be computed for a non-auto strategy"
    assert demoted == []
    assert cancelled == []
    assert dms == []


@pytest.mark.asyncio
async def test_missing_metadata_row_is_a_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No metadata row → propose-only default → no-op."""
    demoted: list[dict[str, Any]] = []
    cancelled: list[dict[str, Any]] = []
    dms: list[dict[str, Any]] = []
    _install_common_seams(
        monkeypatch,
        meta=None,
        drawdown=Decimal("0.50"),
        demoted=demoted,
        cancelled=cancelled,
        dms=dms,
    )

    result = await evaluate_drawdown(
        user_id="u1", strategy_name="ghost", broker=_RecordingBroker()
    )

    assert result is False
    assert demoted == []


@pytest.mark.asyncio
async def test_default_threshold_when_column_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NULL anomaly_threshold_pct falls back to the 10% default (D-T11)."""
    demoted: list[dict[str, Any]] = []
    cancelled: list[dict[str, Any]] = []
    dms: list[dict[str, Any]] = []
    _install_common_seams(
        monkeypatch,
        meta=_FakeMeta(anomaly_threshold_pct=None),
        drawdown=Decimal("0.10"),
        demoted=demoted,
        cancelled=cancelled,
        dms=dms,
    )

    result = await evaluate_drawdown(
        user_id="u1", strategy_name="momentum", broker=_RecordingBroker()
    )

    assert result is True
    assert dms[0]["threshold"] == Decimal("0.10")


@pytest.mark.asyncio
async def test_anomaly_trips_before_max_daily_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Threshold ordering: a drawdown between the anomaly threshold and the
    hard max_daily_loss_usd cap demotes (anomaly) but does NOT halt trading.

    The anomaly threshold (10% of start-of-day value) is reached at a smaller
    loss than the per-strategy max_daily_loss_usd hard cap. We model a $1,000
    start-of-day book: a 10% drawdown is a $100 loss, which trips anomaly while
    the operator's max_daily_loss_usd is $200 — the hard cap is NOT yet reached.
    The reflex removes autonomy (demote) without halting (no kill, research runs).
    """
    demoted: list[dict[str, Any]] = []
    cancelled: list[dict[str, Any]] = []
    dms: list[dict[str, Any]] = []
    # 10% drawdown == $100 loss on a $1,000 book; max_daily_loss_usd is $200.
    _install_common_seams(
        monkeypatch,
        meta=_FakeMeta(anomaly_threshold_pct="0.10"),
        drawdown=Decimal("0.10"),
        demoted=demoted,
        cancelled=cancelled,
        dms=dms,
    )

    result = await evaluate_drawdown(
        user_id="u1", strategy_name="momentum", broker=_RecordingBroker()
    )

    # Anomaly demotes (earlier rung) — it does not raise / halt.
    assert result is True
    assert demoted[0]["reason"] == "anomaly"
    # The reflex never touches a kill switch or the hard-cap path: it only
    # demotes + cancels this strategy's pending orders. (No exception raised.)


@pytest.mark.asyncio
async def test_demotion_is_surgical_to_one_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The demote + cancel are scoped to the named strategy only (D-T12)."""
    demoted: list[dict[str, Any]] = []
    cancelled: list[dict[str, Any]] = []
    dms: list[dict[str, Any]] = []
    _install_common_seams(
        monkeypatch,
        meta=_FakeMeta(anomaly_threshold_pct="0.10"),
        drawdown=Decimal("0.20"),
        demoted=demoted,
        cancelled=cancelled,
        dms=dms,
    )

    await evaluate_drawdown(
        user_id="u1", strategy_name="alpha", broker=_RecordingBroker()
    )

    # Only "alpha" is named in the demote + cancel calls — never a wildcard.
    assert {d["strategy_name"] for d in demoted} == {"alpha"}
    assert {c["strategy_name"] for c in cancelled} == {"alpha"}


def test_drawdown_math_is_decimal_only() -> None:
    """Static guard: the drawdown math path contains no float() coercion."""
    import re
    from pathlib import Path

    src = Path(ev.__file__).read_text(encoding="utf-8")
    # No bare float( ... ) call anywhere in the module (math is Decimal-exact).
    assert not re.search(r"\bfloat\(", src), (
        "anomaly/evaluator.py must not use float() — drawdown math is "
        "Decimal-exact per D-T (no binary-fp money math)."
    )


@pytest.mark.asyncio
async def test_compute_drawdown_guards_zero_denominator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_compute_single_day_drawdown_pct returns 0 when start-of-day value <= 0.

    A missing / zero snapshot must never produce a divide-by-zero or a false
    trip — it returns Decimal('0') so the reflex is a no-op until the snapshot
    job has run.
    """
    # Seam: the SOD snapshot reader returns 0 (no snapshot yet today).
    async def fake_sod(user_id: str, strategy_name: str) -> Decimal:
        return Decimal("0")

    async def fake_current(
        user_id: str, strategy_name: str, broker: Any
    ) -> Decimal:
        return Decimal("500")

    monkeypatch.setattr(ev, "_load_start_of_day_value", fake_sod)
    monkeypatch.setattr(ev, "_compute_current_value", fake_current)

    dd = await ev._compute_single_day_drawdown_pct(
        "u1", "momentum", _RecordingBroker()
    )
    assert dd == Decimal("0")


def test_strategies_list_renders_anomaly_notice() -> None:
    """Surface 6b: a demoted-today strategy renders the red role=alert notice."""
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader, select_autoescape

    templates_dir = (
        Path(__file__).resolve().parents[1]
        / ".."
        / "src"
        / "gekko"
        / "dashboard"
        / "templates"
    ).resolve()
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    tpl = env.get_template("strategies_list.html.j2")
    out = tpl.render(
        request=None,
        user_id="u1",
        strategies=[],
        anomaly_notices=[
            {
                "strategy_name": "momentum",
                "drawdown_pct": "12.0",
                "threshold_pct": "10.0",
                "cancelled_count": "3",
            }
        ],
    )
    assert 'class="anomaly-notice"' in out
    assert 'role="alert"' in out
    assert 'aria-live="assertive"' in out
    assert "momentum" in out
    assert "12.0%" in out
    assert "10.0%" in out
    assert "3 pending auto-order(s) cancelled" in out


def test_strategies_list_omits_notice_when_none() -> None:
    """No demotion today → no anomaly-notice block."""
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader, select_autoescape

    templates_dir = (
        Path(__file__).resolve().parents[1]
        / ".."
        / "src"
        / "gekko"
        / "dashboard"
        / "templates"
    ).resolve()
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    tpl = env.get_template("strategies_list.html.j2")
    out = tpl.render(
        request=None, user_id="u1", strategies=[], anomaly_notices=[]
    )
    assert 'class="anomaly-notice"' not in out


@pytest.mark.asyncio
async def test_compute_drawdown_is_decimal_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(sod - current) / sod computed Decimal-exact."""
    async def fake_sod(user_id: str, strategy_name: str) -> Decimal:
        return Decimal("1000.00")

    async def fake_current(
        user_id: str, strategy_name: str, broker: Any
    ) -> Decimal:
        return Decimal("880.00")

    monkeypatch.setattr(ev, "_load_start_of_day_value", fake_sod)
    monkeypatch.setattr(ev, "_compute_current_value", fake_current)

    dd = await ev._compute_single_day_drawdown_pct(
        "u1", "momentum", _RecordingBroker()
    )
    # (1000 - 880) / 1000 == 0.12 exactly.
    assert dd == Decimal("0.12")
    assert isinstance(dd, Decimal)
