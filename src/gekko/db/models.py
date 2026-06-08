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
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ---------------------------------------------------------------------------
# CheckConstraint vocabulary — single source of truth
# ---------------------------------------------------------------------------

#: Allowed values for ``Guidance.scope`` (D-15 / RES-08).
_GUIDANCE_SCOPES: tuple[str, ...] = ("strategy", "global")

#: Allowed values for ``Proposal.status`` (D-11 lifecycle).
_PROPOSAL_STATUSES: tuple[str, ...] = (
    "PENDING",
    "APPROVED",
    "REJECTED",
    "EXECUTING",
    "FILLED",
    "FAILED",
)

#: Allowed values for ``Event.event_type`` (D-14 vocabulary).
_EVENT_TYPES: tuple[str, ...] = (
    "decision",
    "proposal",
    "approval",
    "rejection",
    "order_submitted",
    "fill",
    "kill_switch",
    "cap_rejection",
    "error",
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
    """

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    agreement_acknowledged_at: Mapped[str | None] = mapped_column(
        String, nullable=True
    )

    def __repr__(self) -> str:
        return f"User(user_id={self.user_id!r})"


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

    __table_args__ = (
        CheckConstraint(
            _in_check("status", _PROPOSAL_STATUSES),
            name="ck_proposal_status",
        ),
    )

    def __repr__(self) -> str:
        # NB: payload_json deliberately excluded — D-15 / AUTH-04 defense.
        return (
            f"Proposal(proposal_id={self.proposal_id!r}, "
            f"user_id={self.user_id!r}, "
            f"strategy_id={self.strategy_id!r}, "
            f"status={self.status!r})"
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
    is deferred. The composite primary key ``(user_id, broker)`` enforces
    at most one credential row per (user, broker) pair.

    ``paper`` MUST be ``True`` for ``alpaca`` rows in Phase 1 — the
    ``AlpacaBroker`` constructor (Plan 01-05) refuses live keys.
    """

    __tablename__ = "broker_credentials"

    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.user_id"), primary_key=True
    )
    broker: Mapped[str] = mapped_column(String, primary_key=True)
    key_blob: Mapped[str] = mapped_column(String, nullable=False)
    secret_blob: Mapped[str] = mapped_column(String, nullable=False)
    paper: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    def __repr__(self) -> str:
        # NB: key_blob and secret_blob deliberately excluded — AUTH-04 defense.
        return (
            f"BrokerCredential(user_id={self.user_id!r}, "
            f"broker={self.broker!r}, paper={self.paper!r})"
        )


__all__: tuple[str, ...] = (
    "Base",
    "User",
    "Strategy",
    "Guidance",
    "Proposal",
    "Event",
    "BrokerCredential",
)
