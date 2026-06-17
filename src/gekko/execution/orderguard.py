"""``OrderGuard`` decorator — Plan 02-02 Task 1 (D-26 / EXEC-04 / EXEC-05).

The deterministic Python firewall that sits between every approved
:class:`gekko.schemas.proposal.TradeProposal` and the underlying broker's
``place_order`` HTTP POST. Phase-1's :mod:`gekko.brokers.base` docstring
already pre-declares the pattern at lines 6-10:

    "Phase 2 hook: P2 OrderGuard wraps :meth:`Brokerage.place_order` with the
    universe-whitelist + hard-cap + paper/live env-pairing checks before
    delegating here."

Per CONTEXT.md D-26: OrderGuard is ITSELF a :class:`Brokerage` subclass that
wraps a concrete broker. Same ``place_order(req) -> OrderResult`` signature;
Phase 8/9 brokers compose identically. The executor's ``_build_broker``
swaps in :class:`OrderGuard` so every paper trade routes through it.

The 6 BLOCK checks shipped by this plan (PDT/T+1/wash-sale land in 02-03):

  1. :func:`gekko.execution.checks.check_kill_switch`
  2. :func:`gekko.execution.checks.check_paper_live_pairing`
  3. :func:`gekko.execution.checks.check_universe`
  4. :func:`gekko.execution.checks.check_hard_caps` (4 sub-checks)
  5. :func:`gekko.execution.checks.check_qty_price_sanity` (D-27 / 2% drift)
  6. :func:`gekko.execution.checks.check_market_hours` (defense in depth)

On any check failure: :class:`gekko.core.errors.OrderGuardRejected` is
raised — :func:`gekko.execution.executor.execute_proposal` catches it via
the ``cap_rejection`` sibling branch (mirrors ``executor.market_closed``)
and transitions ``APPROVED → FAILED`` + writes a ``cap_rejection`` audit
event.

**Architectural invariants (EXEC-03 / Pitfall 4):**

  * ``OrderGuard.place_order`` carries NO ``@retry`` / tenacity decorator.
    Knight-Capital prevention — order POSTs NEVER auto-retry. The grep gate
    in ``tests/unit/test_orderguard.py`` asserts
    ``not hasattr(OrderGuard.place_order, "__wrapped__")``.
  * The Claude Agent SDK MUST NOT be imported here. Extends Plan
    01-08's anti-pattern 1 grep gate to this module (and to every
    ``src/gekko/execution/checks/*.py``).
  * GET methods (``get_account``, ``get_positions``, ``get_quote``,
    ``get_order_by_client_order_id``, ``cancel_order``, ``health_check``)
    pass through unchanged to ``self._wrapped`` — they are NOT decorated
    here (plan 02-03 adds the tenacity GET decorator on the underlying
    :class:`AlpacaBroker`, NOT on the OrderGuard delegate).
"""

from __future__ import annotations

from typing import Any, Literal

from gekko.brokers.base import Brokerage, OrderRequest, OrderResult
from gekko.execution.checks import (
    check_hard_caps,
    check_kill_switch,
    check_market_hours,
    check_paper_live_pairing,
    check_pdt,
    check_qty_price_sanity,
    check_t1_settlement,
    check_universe,
)
from gekko.schemas.proposal import TradeProposal
from gekko.schemas.strategy import Strategy

AccountMode = Literal["PAPER", "LIVE"]


class OrderGuard(Brokerage):
    """Deterministic firewall wrapping a concrete :class:`Brokerage`.

    The wrapped broker handles I/O; OrderGuard handles policy. The class
    attributes (``name``, ``supports_fractional``, ``is_paper``) are mirrored
    from the wrapped instance so callers introspecting the OrderGuard see
    the same surface as the underlying broker (the ``check_paper_live_pairing``
    callsite passes ``self._wrapped`` explicitly so the check sees the
    truthful underlying ``is_paper``, not the mirrored attribute).

    NEVER touched by LLM bytes. The Anti-Pattern 1 grep-gate test in
    :mod:`tests.unit.test_orderguard` asserts the module source contains
    no agent-SDK substring (extends Plan 01-08's executor gate).
    """

    def __init__(
        self,
        wrapped: Brokerage,
        *,
        strategy: Strategy,
        account_mode: AccountMode,
        user_id: str,
        proposal: TradeProposal | None = None,
    ) -> None:
        """Wrap a concrete broker with the OrderGuard policy layer.

        :param wrapped: The concrete :class:`Brokerage` to delegate I/O to
            (Phase 2 paper path = :class:`AlpacaBroker`; Phase 8 / 9 wire
            other brokers).
        :param strategy: The :class:`Strategy` the proposal was authored
            against. ``watchlist`` + ``hard_caps`` + ``mode`` feed the
            checks.
        :param account_mode: ``"PAPER"`` or ``"LIVE"`` — stamped on the
            proposal at build time (BLOCKER #5 / plan 02-01 Task 3) and
            passed through here. Plan 02-06 extends with credential-kind
            cross-check.
        :param user_id: Per-user SQLCipher DB scope (for ``check_kill_switch``
            and the hard-caps daily-loss / trades-per-day audit-log walks).
        :param proposal: Optional :class:`TradeProposal` carrying the
            ``target_notional_usd`` for the qty×price 2% drift check.
            When ``None`` the qty_price sanity check is skipped (used by
            tests that exercise only a subset of checks). In production
            the executor ALWAYS passes the proposal.
        """
        self._wrapped = wrapped
        self._strategy = strategy
        self._account_mode = account_mode
        self._user_id = user_id
        self._proposal = proposal
        # Mirror the wrapped broker's class-attr surface.
        self.name = wrapped.name
        self.supports_fractional = wrapped.supports_fractional
        self.is_paper = wrapped.is_paper

    # ------------------------------------------------------------------
    # Brokerage ABC delegation (passthrough on GETs + cancel)
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        return await self._wrapped.health_check()

    async def get_account(self) -> dict[str, Any]:
        return await self._wrapped.get_account()

    async def get_positions(self) -> list[dict[str, Any]]:
        return await self._wrapped.get_positions()

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        return await self._wrapped.get_quote(symbol)

    async def get_order_by_client_order_id(
        self, client_order_id: str
    ) -> OrderResult | None:
        return await self._wrapped.get_order_by_client_order_id(client_order_id)

    async def cancel_order(self, broker_order_id: str) -> bool:
        return await self._wrapped.cancel_order(broker_order_id)

    async def get_orders_open(self) -> list[dict[str, Any]]:
        # Phase-2 plan 02-05: kill switch reads through OrderGuard transparently
        # — passthrough to the wrapped broker, no policy decision applies here.
        return await self._wrapped.get_orders_open()

    async def cancel_all_open_orders(self) -> list[dict[str, Any]]:
        # Phase-2 plan 02-05: pure passthrough to the wrapped broker. The
        # kill switch's `asyncio.gather` + 4s timeout owns failure tolerance;
        # OrderGuard does NOT add retry / policy here per RESEARCH §6.
        return await self._wrapped.cancel_all_open_orders()

    # ------------------------------------------------------------------
    # The load-bearing override
    # ------------------------------------------------------------------

    async def place_order(self, req: OrderRequest) -> OrderResult:
        """Run every BLOCK check; on first failure raise, else delegate.

        Check order (RESEARCH §1 code shape; plan 02-03 inserts PDT + T+1
        AFTER qty_price_sanity and BEFORE market_hours):

          1. ``check_kill_switch`` — cheapest possible (single SELECT)
          2. ``check_paper_live_pairing`` — pure in-memory invariant
          3. ``check_universe`` — pure in-memory invariant
          4. ``check_hard_caps`` — broker GETs + audit-log walk
          5. ``check_qty_price_sanity`` — broker GET for MARKET orders
          6. ``check_pdt`` — broker get_account + local 5-day round-trip walk
          7. ``check_t1_settlement`` — broker get_account + ref_price math
          8. ``check_market_hours`` — defense in depth (executor's
             check fires first; this only catches edge-case crossings)

        ``check_pdt`` + ``check_t1_settlement`` share a single
        ``broker.get_account()`` call (RESEARCH §1) to avoid duplicate
        broker HTTP traffic. The ``@retry_on_rate_limit`` decorator on
        the underlying :class:`AlpacaBroker.get_account` provides the
        429-handling layer.

        :raises gekko.core.errors.OrderGuardRejected: On any check failure.
        :returns: The underlying broker's :class:`OrderResult`.
        """
        await check_kill_switch(self._user_id)

        check_paper_live_pairing(
            broker=self._wrapped,
            strategy_mode=self._strategy.mode,
            account_mode=self._account_mode,
            user_id=self._user_id,
        )

        await check_universe(req, strategy=self._strategy)

        await check_hard_caps(
            req=req,
            strategy=self._strategy,
            broker=self._wrapped,
            user_id=self._user_id,
        )

        if self._proposal is not None:
            await check_qty_price_sanity(
                req=req,
                target_notional_usd=self._proposal.target_notional_usd,
                broker=self._wrapped,
            )

        # Plan 02-03: PDT + T+1 share one broker.get_account() call.
        account = await self._wrapped.get_account()
        await check_pdt(req=req, account=account, user_id=self._user_id)
        await check_t1_settlement(
            req=req, account=account, broker=self._wrapped
        )

        await check_market_hours(req)

        return await self._wrapped.place_order(req)


__all__: tuple[str, ...] = ("AccountMode", "OrderGuard")
