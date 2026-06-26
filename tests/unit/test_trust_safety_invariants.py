"""Trust safety AST invariants — TRUST-02 / TRUST-03 (Wave-0, Plan 05-01).

Mirrors the directory-wide ``ast.parse`` + ``ast.walk`` gate pattern from
``test_orderguard.py::test_orderguard_place_order_ast_zero_decorators``.

Two invariants are locked here (RESEARCH Validation Architecture):

  1. No module OUTSIDE ``strategy/trust.py`` assigns the literal
     ``trust_level = "auto-within-caps"``. The promotion to auto MUST flow
     through the single ``trust.py`` state-transition helper — any other
     assignment site is a backdoor past the streak gate.
  2. (RED until Plan 05) the auto-branch in ``runtime.py`` is guarded by a
     trust-level check — there is no ``execute_proposal`` call in the auto
     path that isn't preceded by a ``trust == "auto-within-caps"`` guard.

Invariant 1 is runnable NOW (zero assignment sites pre-trust.py = pass).
Invariant 2 is gated behind the auto-branch landing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import gekko

_AUTO_LITERAL = "auto-within-caps"
_SRC_ROOT = Path(gekko.__file__).parent
_TRUST_MODULE_REL = Path("strategy") / "trust.py"


def _assigns_auto_literal(tree: ast.AST) -> bool:
    """True if any assignment in *tree* targets a constant == _AUTO_LITERAL."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if (
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and value.value == _AUTO_LITERAL
            ):
                return True
    return False


def test_no_module_outside_trust_assigns_auto_within_caps() -> None:
    """Only ``strategy/trust.py`` may assign trust_level = 'auto-within-caps'."""
    offenders: list[str] = []
    for py_file in _SRC_ROOT.rglob("*.py"):
        rel = py_file.relative_to(_SRC_ROOT)
        if rel == _TRUST_MODULE_REL:
            continue  # trust.py is the SANCTIONED assignment site
        src = py_file.read_text(encoding="utf-8")
        if _AUTO_LITERAL not in src:
            continue
        tree = ast.parse(src)
        if _assigns_auto_literal(tree):
            offenders.append(str(rel))
    assert offenders == [], (
        "trust_level = 'auto-within-caps' assigned outside strategy/trust.py: "
        f"{offenders!r}"
    )


@pytest.mark.skipif(
    not (_SRC_ROOT / "agent" / "runtime.py").exists()
    or "load_trust_level"
    not in (_SRC_ROOT / "agent" / "runtime.py").read_text(encoding="utf-8"),
    reason="auto-branch (load_trust_level) not yet wired into runtime (Plan 05)",
)
def test_auto_branch_is_guarded_by_trust_check() -> None:
    """The auto execute_proposal path is gated by a trust == 'auto-within-caps' check.

    Invariant 2 (RESEARCH Validation Architecture): the auto-branch in
    runtime.py reaches ``execute_proposal`` only after a
    ``trust == TRUST_AUTO`` guard. We assert structurally that:

      * the auto literal appears (the guard exists), AND
      * runtime.py never calls ``broker.place_order`` directly (the auto path
        is the single OrderGuard-protected path — D-T08).
    """
    runtime_src = (_SRC_ROOT / "agent" / "runtime.py").read_text(encoding="utf-8")
    # The trust guard (TRUST_AUTO constant equals the auto literal) is present.
    assert "TRUST_AUTO" in runtime_src or _AUTO_LITERAL in runtime_src
    # No direct broker path — the auto trade must traverse execute_proposal.
    assert "broker.place_order" not in runtime_src, (
        "runtime.py must not call broker.place_order directly — route through "
        "execute_proposal so OrderGuard re-checks all caps (D-T08)."
    )
    # The auto-branch dispatches through execute_proposal (the single path).
    assert "execute_proposal" in runtime_src


@pytest.mark.skipif(
    not (_SRC_ROOT / "agent" / "runtime.py").exists()
    or "_run_auto_branch"
    not in (_SRC_ROOT / "agent" / "runtime.py").read_text(encoding="utf-8"),
    reason="auto-branch not yet wired into runtime (Plan 05)",
)
def test_auto_branch_stacks_live_first_trade_gate() -> None:
    """The auto-branch routes LIVE first trades to the dual-channel gate (D-T03).

    AST/source gate: the auto-branch must reference both the first-live stamp
    column (``first_live_trade_confirmed_at``) and the dual-channel target
    status (``AWAITING_2ND_CHANNEL``) so a LIVE auto strategy whose first live
    trade is unconfirmed cannot direct-execute. The behavioral lock is in
    ``tests/unit/test_auto_execute.py::test_live_first_trade_routes_to_dual_channel_not_execute``.
    """
    runtime_src = (_SRC_ROOT / "agent" / "runtime.py").read_text(encoding="utf-8")
    assert "first_live_trade_confirmed_at" in runtime_src, (
        "auto-branch must check first_live_trade_confirmed_at so LIVE+auto "
        "stacks the Phase-2 dual-channel gate (D-T03)."
    )
    assert "AWAITING_2ND_CHANNEL" in runtime_src, (
        "auto-branch must route the first LIVE auto trade to "
        "AWAITING_2ND_CHANNEL, not direct execute (D-T03)."
    )
