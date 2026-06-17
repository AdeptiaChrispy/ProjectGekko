"""Reusable text-template constants for the Slack reporter — Plan 01-08 Task 1.

This module is deliberately tiny: the Block Kit payloads in
:mod:`gekko.reporter.slack` are constructed function-side rather than from
pre-rendered JSON blobs because the input is a dynamic ``TradeProposal`` /
``NoActionProposal`` shape. The only stable strings worth lifting are the
REG-01 compliance disclosure and the placeholder shown when best-effort
fields (``company_name`` / ``sector``) are unavailable.

References:
  * .planning/phases/01-foundation.../01-CONTEXT.md  REG-01 (UI framing)
  * .planning/phases/01-foundation.../01-RESEARCH.md §"Code Examples —
    proposal card builder" — context block carrying REG-01 disclosure
"""

from __future__ import annotations

#: REG-01 compliance disclosure rendered as the trailing ``context`` block in
#: every proposal card and at the bottom of any user-visible Slack message
#: that surfaces a trade decision. Wording sourced from the plan's
#: ``<behavior>`` block verbatim so the validation grep ("Not investment
#: advice") never drifts.
REG_01_DISCLOSURE: str = (
    "Gekko is personal trade-execution tooling acting on your authored "
    "strategy. _Not investment advice._"
)


#: Placeholder rendered when a best-effort field (``company_name`` or
#: ``sector``) is unavailable on the proposal card. Italicized so the user
#: sees a clear "this data was not available" signal rather than thinking
#: we forgot the field (HITL-01 field completeness).
UNKNOWN_FIELD_PLACEHOLDER: str = "_unknown_"


#: Banner shown at the top of the proposal card when ``account_mode ==
#: "PAPER"`` (P1 always passes "PAPER" per D-24).
PAPER_BANNER: str = "🟢 PAPER"


#: Banner shown when ``account_mode == "LIVE"``. P1 never emits this — the
#: Executor rejects live keys via :class:`AlpacaBroker`'s constructor guard
#: (Plan 01-05). Kept here for forward-compatibility with the P2 OrderGuard.
LIVE_BANNER: str = "🔴 LIVE"

# ---------------------------------------------------------------------------
# Phase 2 — UI-SPEC §"Banner & Section Headers" + §4a constants (plan 02-05)
# ---------------------------------------------------------------------------

#: Strong-form live banner (UI-SPEC §"Banner & Section Headers"). Replaces
#: the placeholder LIVE_BANNER for the dashboard's banner-live-strong CSS
#: class. Plan 02-06 wires the conditional in base.html.j2.
LIVE_BANNER_STRONG: str = "[LIVE — REAL MONEY] Alpaca live trading is armed."

#: Sticky red banner copy shown when kill_active=True (UI-SPEC §2c).
#: Format-string for in-process kill; the boot-restored variant has its
#: own constant below.
KILL_ACTIVE_BANNER: str = (
    "🚫 KILL ACTIVE — no orders will fire. "
    "Cancelled {n_cancelled}/{n_total} open orders."
)

#: Boot-restored variant shown when the lifespan check finds kill_active=True
#: on startup (D-36).
KILL_ACTIVE_BANNER_BOOT_RESTORED: str = (
    "🚫 KILL ACTIVE — restored from previous session. UNKILL to resume."
)

#: Slack Block Kit header for the OrderGuard rejection card (UI-SPEC §4a).
ORDERGUARD_REJECTION_HEADER: str = "🔴 [REJECTED BY ORDERGUARD]"

#: Slack Block Kit explainer block for the rejection card (UI-SPEC §4a).
ORDERGUARD_REJECTION_EXPLAINER: str = (
    "This proposal will not be executed. OrderGuard is a deterministic "
    "Python firewall — it ran BEFORE any broker call. No order was sent."
)


__all__: tuple[str, ...] = (
    "KILL_ACTIVE_BANNER",
    "KILL_ACTIVE_BANNER_BOOT_RESTORED",
    "LIVE_BANNER",
    "LIVE_BANNER_STRONG",
    "ORDERGUARD_REJECTION_EXPLAINER",
    "ORDERGUARD_REJECTION_HEADER",
    "PAPER_BANNER",
    "REG_01_DISCLOSURE",
    "UNKNOWN_FIELD_PLACEHOLDER",
)
