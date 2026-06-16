"""Gekko error hierarchy — Plan 01-03 Task 1.

Centralized error types referenced across the codebase. Plans 01-04, 01-05,
and 01-07 import from this module; every Gekko-specific exception inherits
from ``GekkoError`` so callers can catch the entire family with one ``except``.

References:
  * RESEARCH §"Code Examples — AlpacaBroker errors"
  * VALIDATION row AUTH-03/04 — BrokerConfigError on wrong passphrase
  * D-19 (passphrase-on-start) — wrong passphrase raises WrongPassphraseError
"""

from __future__ import annotations


class GekkoError(Exception):
    """Root exception for every Gekko-specific failure.

    Catch this in top-level handlers (the CLI, ``gekko serve`` lifespan) to
    distinguish expected, recoverable Gekko errors from genuinely unexpected
    crashes.
    """


class WrongPassphraseError(GekkoError):
    """Raised when the SQLCipher passphrase does not match the encrypted DB.

    Per RESEARCH §Pitfall 2: SQLCipher accepts ``PRAGMA key`` regardless of
    correctness — the mismatch surfaces on the first real SQL statement as
    ``OperationalError("file is encrypted or is not a database")``. The
    engine layer converts that into this typed exception with a clear,
    user-friendly message.
    """


class BrokerConfigError(GekkoError):
    """Raised on broker misconfiguration (e.g., live keys passed in Phase 1).

    P1 paper-only invariant lives in ``AlpacaBroker.__init__``; the
    constructor raises this when ``paper=False`` is requested (Plan 01-05).
    """


class BrokerOrderError(GekkoError):
    """Raised when a broker rejects an order or the order state goes invalid."""


class BudgetExceeded(GekkoError):
    """Raised when a per-cycle research budget hits its hard (2x) limit.

    Per D-13: the soft per-cycle limit is logged-only; the 2x hard limit
    halts the Researcher subagent via this exception (Plan 01-07).
    """


class AuditChainBroken(GekkoError):
    """Raised when ``walk_chain`` detects a tampered or missing event row.

    The audit hash chain (D-16) is verified at startup and on demand;
    integrity failures surface as this typed error so operators can
    distinguish chain corruption from generic DB errors (Plan 01-04).
    """


class ProposalRejected(GekkoError):
    """Raised when ``ProposalWriter`` rejects an LLM-emitted proposal.

    Phase 1 hallucinated-ticker mitigation: if the Decision agent calls
    ``propose_trade`` with a ticker outside the strategy's watchlist, the
    writer rejects the proposal, emits an ``error`` audit event, and
    raises this typed exception. The writer never persists a hallucinated
    ticker (Plan 01-07 Task 5; RESEARCH §Security Domain).
    """


class OrderGuardRejected(GekkoError):
    """Raised when OrderGuard rejects a proposal at the pre-broker gate.

    Plan 02-01 Task 5 (foundational error class — plans 02-02 + 02-03 raise
    this from check_universe / check_hard_caps / check_qty_price_sanity /
    check_paper_live_pairing / check_kill_active / check_pdt_t1 /
    check_wash_sale).

    Carries:

    * ``reject_code`` — a stable machine-readable code from D-29 / D-30
      (e.g., ``"universe"``, ``"hard_cap_position_pct"``, ``"qty_price_drift"``,
      ``"paper_live_mismatch_broker"``, ``"kill_active"``, ``"pdt_rule_local"``,
      ``"t1_settlement"``). The full vocabulary is enumerated in plan 02-01
      Task 2's stub action body — those literal strings are locked so the
      Slack card + audit log + dashboard layers can interpret them.
    * ``reject_reason`` — a human-readable explanation surfaced in the
      Slack card and the audit ``cap_rejection`` event payload.
    * ``extra`` — optional dict of structured context (ticker, attempted
      qty, cap value, etc.) for the audit log + retrospective dashboard.

    Catch as ``GekkoError`` to handle the entire family in the
    runtime / executor / Slack-handler top-level except blocks.
    """

    def __init__(
        self,
        reject_code: str,
        reject_reason: str,
        *,
        extra: dict[str, object] | None = None,
    ) -> None:
        super().__init__(f"{reject_code}: {reject_reason}")
        self.reject_code = reject_code
        self.reject_reason = reject_reason
        self.extra: dict[str, object] = dict(extra) if extra is not None else {}


__all__: tuple[str, ...] = (
    "GekkoError",
    "WrongPassphraseError",
    "BrokerConfigError",
    "BrokerOrderError",
    "BudgetExceeded",
    "AuditChainBroken",
    "ProposalRejected",
    "OrderGuardRejected",
)
