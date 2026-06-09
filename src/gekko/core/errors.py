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


__all__: tuple[str, ...] = (
    "GekkoError",
    "WrongPassphraseError",
    "BrokerConfigError",
    "BrokerOrderError",
    "BudgetExceeded",
    "AuditChainBroken",
    "ProposalRejected",
)
