"""SQLAlchemy 2.x ORM models — Plan 01-03 Task 2.

The six Phase 1 tables (per RESEARCH §System Architecture Diagram + CONTEXT
D-05, D-14, D-21) are declared here:

    users               — per-user identity rows
    strategies          — snapshot-row versioning (D-05)
    guidance            — ad-hoc user guidance with scope (strategy|global)
    proposals           — TradeProposal / NoActionProposal payloads
    events              — append-only audit log with SHA-256 hash chain (D-14)
    broker_credentials  — per-user broker keys (composite PK user_id+broker)

The ``apscheduler_jobs`` table is intentionally NOT defined here — APScheduler
3.x's ``SQLAlchemyJobStore`` creates it itself when ``scheduler.start()``
runs in Plan 01-09. The Alembic ``0001_initial`` migration likewise skips it.

D-21 invariant: every table has a ``user_id`` column (foreign-keyed to
``users.user_id`` where appropriate). This is the load-bearing multi-user
contract — every downstream query filters by it.

D-15 + AUTH-04 defense-in-depth: every model's ``__repr__`` excludes
``payload_json``, ``key_blob``, and ``secret_blob`` so accidental logging of a
model object cannot leak rationale or credentials.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ---------------------------------------------------------------------------
# CheckConstraint vocabulary — single source of truth
# ---------------------------------------------------------------------------

#: Allowed values for ``Guidance.scope`` (D-15 / RES-08).
_GUIDANCE_SCOPES: tuple[str, ...] = ("strategy", "global")

#: Allowed values for ``Proposal.status`` (D-11 lifecycle + Phase-2 dual-channel).
#:
#: Phase-2 additions (plan 02-01 Task 4): ``AWAITING_2ND_CHANNEL`` is the
#: holding state between Slack approve and dashboard confirm for the first
#: live trade per HITL-06; ``APPROVED_LIVE`` is the post-dual-channel
#: approved state that executes on the live broker per BLOCKER #1.
#:
#: Phase-3 addition (plan 03-01 Task 2): ``EXPIRED`` is the terminal state
#: reached by the sweep when ``expires_at`` has passed without HITL approval
#: (A6 / D-50). The state-machine edge (PENDING, EXPIRED) is added by
#: plan 03-01 Task 3 in ``src/gekko/approval/proposals.py``.
_PROPOSAL_STATUSES: tuple[str, ...] = (
    "PENDING",
    "APPROVED",
    "REJECTED",
    "EXECUTING",
    "FILLED",
    "FAILED",
    "AWAITING_2ND_CHANNEL",
    "APPROVED_LIVE",
    "EXPIRED",
)

#: Allowed values for ``Proposal.account_mode`` (BLOCKER #5 / plan 02-01 Task 4).
_ACCOUNT_MODES: tuple[str, ...] = ("PAPER", "LIVE")

#: Allowed values for ``BrokerCredential.kind`` (D-34 / plan 02-01 Task 4).
_BROKER_CREDENTIAL_KINDS: tuple[str, ...] = ("alpaca_paper", "alpaca_live")

#: Allowed values for ``Event.event_type`` (D-14 vocabulary).
#:
#: BL-01 (Phase-2 review fix): extends D-14 with the four credential /
#: promotion event types promised by the Phase-2 plan but missing from
#: the original Phase-1 tuple. Prior to this fix, ``vault/credentials``
#: and ``strategy/promotion`` were forced to write these as
#: ``event_type="error"`` with a ``context`` discriminator in the
#: payload — polluting the error bucket and breaking the
#: "filter on event_type" forensic story. The Alembic migration that
#: drops + recreates ``ck_event_type`` to accept these values is
#: tracked separately.
#:
#: Phase-3 additions (plan 03-01 Task 2): four new event types for the
#: P3 HITL UX feature set:
#:   - ``expiration``   — sweep fired; proposal timed out without HITL approval
#:   - ``dedup_click``  — duplicate Slack/dashboard action detected + logged
#:   - ``edit_size``    — operator edited the proposed quantity before approval
#:   - ``daily_pnl``    — daily P&L digest sent to the operator
#:
#: Phase-4 additions (plan 04-02 Task 1): two new event types for cost
#: ceiling (COST-05) and prompt-injection audit (SC-2 gap closure):
#:   - ``llm_cost``           — per-query() cost ledger entry; payload carries
#:                              input_tokens, output_tokens, cost_usd (Decimal)
#:   - ``suspicious_content`` — SC-2 gap: prompt-injection pattern detected in
#:                              EvidenceSnippet.quote_text at brief-parse time
#:
#: Phase-5 additions (plan 05-01 Task 2): five new event types for the trust
#: ladder / portfolio caps / anomaly reflex (TRUST-01..06). Like the BL-01 fix
#: above, these are FIRST-CLASS event types — never ``event_type="error"`` with
#: a ``context`` discriminator (BL-01 anti-pattern). Trust events key on
#: ``strategy_name`` in the payload with ``strategy_id=None`` (mirrors
#: ``live_mode_promoted`` in promotion.py):
#:   - ``trust_promoted``   — strategy promoted to auto-within-caps (TRUST-01)
#:   - ``trust_demoted``    — strategy demoted to propose-only (TRUST-01)
#:   - ``anomaly_demotion`` — drawdown reflex demoted + cancelled (TRUST-03)
#:   - ``capital_scaled``   — per-strategy capital_ceiling_usd changed (TRUST-05)
#:   - ``auto_execution``   — auto-within-caps proposal executed (TRUST-02)
_EVENT_TYPES: tuple[str, ...] = (
    "decision",
    "proposal",
    "approval",
    "rejection",
    "order_submitted",
    "fill",
    "kill_switch",
    "cap_rejection",
    "credentials_added",
    "live_mode_promoted",
    "live_mode_demoted",
    "first_live_trade_confirmed",
    "error",
    "expiration",
    "dedup_click",
    "edit_size",
    "daily_pnl",
    # Phase-4 additions:
    "llm_cost",
    "suspicious_content",
    # Phase-5 additions (plan 05-01 Task 2 / TRUST-01..06):
    "trust_promoted",
    "trust_demoted",
    "anomaly_demotion",
    "capital_scaled",
    "auto_execution",
)


def _in_check(column: str, allowed: tuple[str, ...]) -> str:
    """Build a SQL ``column IN (...)`` expression for a CheckConstraint."""
    return f"{column} IN ({', '.join(repr(v) for v in allowed)})"


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Common declarative base for every Gekko ORM model.

    Subclasses must set ``__tablename__``. SQLAlchemy 2.x typed ``Mapped[]``
    columns are required so mypy sees the column types end-to-end.
    """


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------


class User(Base):
    """Per-user identity row (D-21).

    ``user_id`` is the string identity (e.g., Slack ``U...`` or installation
    id); it's the primary key referenced by every other table's FK column.

    ``agreement_acknowledged_at`` carries the timestamp the user clicked "I
    agree" during ``gekko init`` (REG-02). NULL means the agreement is
    pending — Plan 01-09 will surface a re-prompt.

    Phase-2 columns (plan 02-01 Task 4 / D-35 + D-36):

    * ``kill_active`` — when True, OrderGuard refuses all new place_order
      calls and the runtime DMs the operator that kill is still active
      (EXEC-06). Set to True by ``/gekko kill CONFIRM`` (plan 02-05); flipped
      back to False ONLY by an explicit operator action — never auto-cleared.
    * ``kill_active_since`` — ISO timestamp when the kill state was first
      latched. NULL when kill is inactive.
    * ``kill_active_reason`` — human-readable note from the operator's
      ``/gekko kill CONFIRM <reason>`` slash command. NULL when inactive.

    All three are visible in ``__repr__`` (operator-debugging useful; not
    credential-sensitive).
    """

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    agreement_acknowledged_at: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    # Phase-2 / D-35 + D-36 kill-switch columns.
    kill_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0")
    )
    kill_active_since: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    kill_active_reason: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    # Phase-3 / D-47 + D-49 quiet-hours + timezone columns.
    #
    # ``quiet_hours_start`` and ``quiet_hours_end`` store HH:MM:SS strings
    # (e.g. "22:00:00" for 10 PM). NULL means quiet hours are disabled.
    # ``timezone`` is an IANA timezone name (e.g. "America/New_York");
    # NULL defaults to "America/New_York" at read time per D-49.
    #
    # Per D-47: Strategy.timezone is NOT added — the strategy inherits the
    # user's timezone. Strategy.quiet_hours_* CAN override the user's window
    # (the resolver picks the narrower of the two per D-47 precedence rules).
    quiet_hours_start: Mapped[str | None] = mapped_column(String, nullable=True)
    quiet_hours_end: Mapped[str | None] = mapped_column(String, nullable=True)
    timezone: Mapped[str | None] = mapped_column(String, nullable=True)
    # Phase-4 / D-02 + D-12 daily cost ceiling + alert-sent-date columns.
    #
    # ``daily_cost_ceiling_usd`` stores the configurable per-day USD ceiling as
    # a TEXT string (consistent with money-as-TEXT pattern). NULL defaults to
    # the DEFAULT_DAILY_CEILING_USD constant at read time.
    # ``cost_alert_80_sent_date`` and ``cost_alert_100_sent_date`` store the
    # ISO date (YYYY-MM-DD, in the user's timezone) when the 80%/100% DM was
    # last sent. Guard compares this against today's local date to enforce
    # the "one DM per day" rule (D-06/D-08). NULL = never sent.
    daily_cost_ceiling_usd: Mapped[str | None] = mapped_column(String, nullable=True)
    cost_alert_80_sent_date: Mapped[str | None] = mapped_column(String, nullable=True)
    cost_alert_100_sent_date: Mapped[str | None] = mapped_column(String, nullable=True)
    # Phase-5 / TRUST-04 account-wide portfolio caps (plan 05-01 Task 2).
    #
    # All four are stored as TEXT (money-as-TEXT / percent-as-fraction-TEXT
    # convention). Percentages are FRACTION strings ("0.50" == 50%); USD is a
    # plain decimal string ("200.00"). NULL/blank = the cap is DISABLED (the
    # check returns early). server_defaults supply conservative starting caps
    # from 05-UI-SPEC; existing rows backfill via migration 0007.
    #   - max_total_exposure_pct        — aggregate deployed-equity ceiling (50%)
    #   - max_sector_concentration_pct  — single-sector ceiling (30%)
    #   - max_correlated_ticker_pct     — single net per-ticker ceiling (15%)
    #   - max_total_daily_loss_usd      — account-wide realized-loss halt ($200)
    max_total_exposure_pct: Mapped[str | None] = mapped_column(String, nullable=True)
    max_sector_concentration_pct: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    max_correlated_ticker_pct: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    max_total_daily_loss_usd: Mapped[str | None] = mapped_column(String, nullable=True)

    def __repr__(self) -> str:
        return (
            f"User(user_id={self.user_id!r}, "
            f"kill_active={self.kill_active!r})"
        )


# ---------------------------------------------------------------------------
# strategies (D-05 snapshot-row versioning)
# ---------------------------------------------------------------------------


class Strategy(Base):
    """Strategy snapshot row (D-05).

    Each edit inserts a new row keyed by ``(user_id, strategy_name, version)``.
    Queries use ``ORDER BY version DESC LIMIT 1`` to find the latest version.
    ``payload_json`` carries the canonical JSON serialization of the
    ``gekko.schemas.Strategy`` Pydantic model (Plan 01-06).
    """

    __tablename__ = "strategies"

    strategy_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.user_id"), nullable=False, index=True
    )
    strategy_name: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "strategy_name",
            "version",
            name="uq_strategy_name_version",
        ),
        Index("ix_strategy_name_lookup", "user_id", "strategy_name"),
    )

    def __repr__(self) -> str:
        return (
            f"Strategy(strategy_id={self.strategy_id!r}, "
            f"user_id={self.user_id!r}, "
            f"strategy_name={self.strategy_name!r}, "
            f"version={self.version!r})"
        )


# ---------------------------------------------------------------------------
# strategy_metadata (Phase 2 — plan 02-01 Task 4 / D-31, D-32)
# ---------------------------------------------------------------------------


class StrategyMetadata(Base):
    """Per-(user, strategy_name) metadata for the live promotion ladder.

    Lives alongside the Strategy snapshot rows (D-05) so the per-strategy
    eligibility + first-live-trade timestamps don't fan out into every
    snapshot row. Plan 02-01 Task 4 adds the columns; plans 02-06 + 02-07
    wire the runtime behavior:

    * ``live_mode_eligible`` (D-31) — gate that plan 02-06's
      check_paper_live_pairing reads alongside ``strategy.mode == 'live'``.
      Flipping a strategy to mode='live' without also flipping this column
      keeps the strategy paper-bound.
    * ``live_promoted_at`` — ISO timestamp the operator clicked
      "Promote to live" in the dashboard.
    * ``first_live_trade_confirmed_at`` (D-32) — ISO timestamp of the first
      dashboard dual-channel confirm. Used by HITL-06 to switch off the
      first-live-trade dual-channel requirement; subsequent live trades
      revert to single-channel approval.
    """

    __tablename__ = "strategy_metadata"

    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.user_id"), primary_key=True
    )
    strategy_name: Mapped[str] = mapped_column(String, primary_key=True)
    live_mode_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0")
    )
    live_promoted_at: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    first_live_trade_confirmed_at: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    # Phase-5 / TRUST-01..05 trust-ladder + capital + anomaly columns
    # (plan 05-01 Task 2). Money/percent stored as TEXT (money-as-TEXT /
    # percent-as-fraction-TEXT convention).
    #
    # ``trust_level`` is the per-strategy ladder rung (D-T16). The only two
    # values in v1 are ``'propose-only'`` (default — every proposal goes to
    # HITL) and ``'auto-within-caps'`` (auto-executes within OrderGuard caps,
    # set ONLY by strategy/trust.py per the AST safety gate). NOT NULL with a
    # server_default so existing rows backfill to the safe rung.
    # ``trust_promoted_at`` is the ISO timestamp of the last promotion.
    # ``capital_ceiling_usd`` caps total deployed capital for the strategy
    # (server_default '1000.00' per D-T16); NULL is read at the default.
    # ``anomaly_threshold_pct`` is the single-day drawdown fraction that trips
    # the anomaly demotion reflex (server_default '0.10' == 10% per D-T11).
    trust_level: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'propose-only'")
    )
    trust_promoted_at: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    capital_ceiling_usd: Mapped[str | None] = mapped_column(
        String, nullable=True, server_default=text("'1000.00'")
    )
    anomaly_threshold_pct: Mapped[str | None] = mapped_column(
        String, nullable=True, server_default=text("'0.10'")
    )

    def __repr__(self) -> str:
        return (
            f"StrategyMetadata(user_id={self.user_id!r}, "
            f"strategy_name={self.strategy_name!r}, "
            f"live_mode_eligible={self.live_mode_eligible!r}, "
            f"trust_level={self.trust_level!r})"
        )


# ---------------------------------------------------------------------------
# slack_action_dedup (Phase 3 — plan 03-01 Task 2 / D-45)
# ---------------------------------------------------------------------------


class SlackActionDedup(Base):
    """Dedup gate for approve / reject / edit-size actions (D-45).

    Records every claimed (proposal_id, action_id, actor) tuple so the
    approval handlers can distinguish first-write from duplicate clicks
    across Slack, Dashboard, and CLI surfaces.

    Two UNIQUE indexes enforce at-most-once semantics:

    * ``uq_dedup_slack`` on ``(proposal_id, action_id, actor_slack_user_id)``
      — prevents the same Slack user from double-approving or double-rejecting
      the same proposal (D-42).
    * ``uq_dedup_dashboard`` on ``(proposal_id, action_id, actor_gekko_user_id,
      source)`` — prevents the same dashboard/CLI session from double-approving
      across the cross-surface gate (D-56).

    ``result`` records whether this write was a first-write or a duplicate
    (detected via ``IntegrityError`` + rollback + re-query per PATTERNS §2b).
    ``slack_trigger_id`` is excluded from ``__repr__`` per T-03-01-03 (mildly
    sensitive — used for retry-debugging only; structlog ``_REDACT_KEYS`` also
    covers ``trigger_id`` substring as a safety net).
    """

    __tablename__ = "slack_action_dedup"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[str] = mapped_column(
        String, ForeignKey("proposals.proposal_id"), nullable=False
    )
    action_id: Mapped[str] = mapped_column(String, nullable=False)
    actor_slack_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    actor_gekko_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.user_id"), nullable=False
    )
    source: Mapped[str] = mapped_column(String, nullable=False)
    slack_trigger_id: Mapped[str | None] = mapped_column(String, nullable=True)
    inserted_at: Mapped[str] = mapped_column(String, nullable=False)
    result: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        CheckConstraint(
            _in_check("source", ("slack", "dashboard", "cli")),
            name="ck_dedup_source",
        ),
        CheckConstraint(
            _in_check("result", ("first_write", "duplicate")),
            name="ck_dedup_result",
        ),
        Index(
            "uq_dedup_slack",
            "proposal_id",
            "action_id",
            "actor_slack_user_id",
            unique=True,
        ),
        Index(
            "uq_dedup_dashboard",
            "proposal_id",
            "action_id",
            "actor_gekko_user_id",
            "source",
            unique=True,
        ),
    )

    def __repr__(self) -> str:
        # slack_trigger_id excluded per T-03-01-03 (mildly sensitive).
        return (
            f"SlackActionDedup(id={self.id!r}, "
            f"proposal_id={self.proposal_id!r}, "
            f"action_id={self.action_id!r}, "
            f"source={self.source!r}, "
            f"result={self.result!r})"
        )


# ---------------------------------------------------------------------------
# guidance (RES-08, STRAT-03)
# ---------------------------------------------------------------------------


class Guidance(Base):
    """User-supplied ad-hoc guidance row (RES-08, STRAT-03).

    Scope is either ``strategy`` (applies to one strategy_id) or ``global``
    (applies to every active strategy). ``expires_at`` lets the user pin
    short-lived guidance that the Researcher prompt automatically drops.
    """

    __tablename__ = "guidance"

    guidance_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.user_id"), nullable=False, index=True
    )
    strategy_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("strategies.strategy_id"),
        nullable=True,
        index=True,
    )
    text: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            _in_check("scope", _GUIDANCE_SCOPES),
            name="ck_guidance_scope",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"Guidance(guidance_id={self.guidance_id!r}, "
            f"user_id={self.user_id!r}, "
            f"scope={self.scope!r})"
        )


# ---------------------------------------------------------------------------
# proposals (D-11 lifecycle; D-15 structured rationale lives in payload_json)
# ---------------------------------------------------------------------------


class Proposal(Base):
    """Trade or no-action proposal (D-11).

    ``status`` walks the PENDING → APPROVED → EXECUTING → FILLED state
    machine (with REJECTED / FAILED terminal branches). ``payload_json``
    is the canonical JSON of the discriminated-union Pydantic model from
    Plan 01-06 — D-15 says it MUST include the structured rationale
    (evidence, confidence, alternatives) when ``event_type`` would be
    ``proposal``.

    The deterministic ``client_order_id`` (D-20 / EXEC-02) is persisted on
    the row when the Proposal Writer (Plan 01-07) builds it.
    """

    __tablename__ = "proposals"

    proposal_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.user_id"), nullable=False, index=True
    )
    strategy_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("strategies.strategy_id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    payload_json: Mapped[str] = mapped_column(String, nullable=False)
    client_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    # BLOCKER #5 / plan 02-01 Task 4: account_mode locked at proposal-build
    # time; closes TOCTOU window between proposal-gen and approve-click.
    # Backfilled to 'PAPER' for all pre-migration rows (Phase-1 was
    # paper-only per D-24).
    account_mode: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'PAPER'")
    )
    # Phase-3 / D-51 + D-61: expires_at is stamped by ProposalWriter at
    # insertion time using strategy.proposal_timeout_minutes or the
    # PROPOSAL_TIMEOUT_DEFAULT_MIN=30 fallback (Plan 03-01 Task 3).
    # NULL is the grandfathered value for pre-migration rows — the sweep
    # treats NULL as "never expires" per D-61.
    expires_at: Mapped[str | None] = mapped_column(String, nullable=True)
    # Phase-3 / D-53: captured by post_run_result() after chat_postMessage
    # succeeds so the sweep's chat.update of the expired card has the
    # ts+channel to target (Plan 03-01 Task 4 / BLOCKER #1 closure).
    slack_message_ts: Mapped[str | None] = mapped_column(String, nullable=True)
    slack_message_channel: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            _in_check("status", _PROPOSAL_STATUSES),
            name="ck_proposal_status",
        ),
        CheckConstraint(
            _in_check("account_mode", _ACCOUNT_MODES),
            name="ck_proposals_account_mode",
        ),
    )

    def __repr__(self) -> str:
        # NB: payload_json deliberately excluded — D-15 / AUTH-04 defense.
        return (
            f"Proposal(proposal_id={self.proposal_id!r}, "
            f"user_id={self.user_id!r}, "
            f"strategy_id={self.strategy_id!r}, "
            f"status={self.status!r}, "
            f"account_mode={self.account_mode!r})"
        )


# ---------------------------------------------------------------------------
# events (D-14 + D-16 hash chain)
# ---------------------------------------------------------------------------


class Event(Base):
    """Append-only audit log row (D-14).

    Columns match D-14 exactly:

        id, ts, user_id, strategy_id, event_type, payload_json,
        prev_hash, row_hash

    ``strategy_id`` is nullable because ``kill_switch`` and other global
    events are not strategy-scoped. The SHA-256 chain (D-16) is computed
    in application code (``gekko.audit.log.append_event`` in Plan 01-04)
    over canonical JSON, not via a SQLite trigger.

    Per D-16 the chain canonical-subset is ``{event_type, payload, ts,
    user_id}`` and ``payload_json`` is stored as the canonicalized output
    of ``canonical_json({...})``. We index ``(user_id, id)`` for fast
    per-user chain walks.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    ts: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.user_id"), nullable=False, index=True
    )
    strategy_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("strategies.strategy_id"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload_json: Mapped[str] = mapped_column(String, nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        CheckConstraint(
            _in_check("event_type", _EVENT_TYPES),
            name="ck_event_type",
        ),
        Index("ix_events_user_id_id", "user_id", "id"),
    )

    def __repr__(self) -> str:
        # NB: payload_json deliberately excluded — D-15 / AUTH-04 defense.
        return (
            f"Event(id={self.id!r}, user_id={self.user_id!r}, "
            f"event_type={self.event_type!r})"
        )


# ---------------------------------------------------------------------------
# broker_credentials (AUTH-03 storage layer; whole-DB encryption protects it)
# ---------------------------------------------------------------------------


class BrokerCredential(Base):
    """Per-user broker key storage (AUTH-03).

    Per-row encryption is NOT applied here — D-19 says whole-DB SQLCipher
    encryption is the only at-rest layer in Phase 1; a per-row Fernet layer
    is deferred. The composite primary key ``(user_id, broker, kind)``
    (extended in plan 02-01 Task 4 / D-34) enforces at most one credential
    row per (user, broker, kind) pair — so a user can store BOTH an
    ``alpaca_paper`` and ``alpaca_live`` row.

    ``paper`` is preserved as a denormalized convenience column for backward
    compatibility with Phase-1 code paths; new code should read ``kind``.
    ``kind`` is the authoritative discriminator and is constrained to
    ``alpaca_paper`` or ``alpaca_live`` by a CHECK constraint.

    ``__repr__`` excludes ``key_blob`` + ``secret_blob`` (AUTH-04 defense
    preserved through the schema extension); ``kind`` IS visible because it
    is a non-sensitive discriminator.
    """

    __tablename__ = "broker_credentials"

    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.user_id"), primary_key=True
    )
    broker: Mapped[str] = mapped_column(String, primary_key=True)
    # D-34 / plan 02-01 Task 4: kind discriminates paper-vs-live credentials.
    # Backfilled at migration time from the existing ``paper`` column.
    kind: Mapped[str] = mapped_column(
        String, primary_key=True, server_default=text("'alpaca_paper'")
    )
    key_blob: Mapped[str] = mapped_column(String, nullable=False)
    secret_blob: Mapped[str] = mapped_column(String, nullable=False)
    paper: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        CheckConstraint(
            _in_check("kind", _BROKER_CREDENTIAL_KINDS),
            name="ck_broker_credentials_kind",
        ),
    )

    def __repr__(self) -> str:
        # NB: key_blob and secret_blob deliberately excluded — AUTH-04 defense.
        return (
            f"BrokerCredential(user_id={self.user_id!r}, "
            f"broker={self.broker!r}, kind={self.kind!r}, "
            f"paper={self.paper!r})"
        )


__all__: tuple[str, ...] = (
    "Base",
    "User",
    "Strategy",
    "StrategyMetadata",
    "SlackActionDedup",
    "Guidance",
    "Proposal",
    "Event",
    "BrokerCredential",
)
