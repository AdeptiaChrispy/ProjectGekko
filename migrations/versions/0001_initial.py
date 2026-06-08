"""initial — create the 6 Phase 1 tables

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-08 18:00:00

Creates the six Phase 1 tables (per Plan 01-03 / models.py):

    users, strategies, guidance, proposals, events, broker_credentials.

The ``apscheduler_jobs`` table is intentionally NOT created here —
APScheduler 3.x's ``SQLAlchemyJobStore`` creates it itself at runtime in
Plan 01-09 when ``scheduler.start()`` runs against the same encrypted DB.

Schema matches ``gekko.db.models`` exactly — CheckConstraints on
``guidance.scope``, ``proposals.status``, ``events.event_type`` use the
single-source-of-truth vocabularies defined in that module.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# CheckConstraint vocabularies (kept in sync with gekko.db.models)
# ---------------------------------------------------------------------------

_GUIDANCE_SCOPES = ("strategy", "global")
_PROPOSAL_STATUSES = (
    "PENDING",
    "APPROVED",
    "REJECTED",
    "EXECUTING",
    "FILLED",
    "FAILED",
)
_EVENT_TYPES = (
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
    return f"{column} IN ({', '.join(repr(v) for v in allowed)})"


# ---------------------------------------------------------------------------
# Upgrade — forward
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # users ----------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("agreement_acknowledged_at", sa.String(), nullable=True),
    )

    # strategies (D-05 snapshot-row versioning) ----------------------------
    op.create_table(
        "strategies",
        sa.Column("strategy_id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("strategy_name", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint(
            "user_id",
            "strategy_name",
            "version",
            name="uq_strategy_name_version",
        ),
    )
    op.create_index(
        "ix_strategies_user_id", "strategies", ["user_id"], unique=False
    )
    op.create_index(
        "ix_strategy_name_lookup",
        "strategies",
        ["user_id", "strategy_name"],
        unique=False,
    )

    # guidance (STRAT-03 / RES-08) -----------------------------------------
    op.create_table(
        "guidance",
        sa.Column("guidance_id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column(
            "strategy_id",
            sa.String(),
            sa.ForeignKey("strategies.strategy_id"),
            nullable=True,
        ),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=True),
        sa.CheckConstraint(
            _in_check("scope", _GUIDANCE_SCOPES),
            name="ck_guidance_scope",
        ),
    )
    op.create_index(
        "ix_guidance_user_id", "guidance", ["user_id"], unique=False
    )
    op.create_index(
        "ix_guidance_strategy_id", "guidance", ["strategy_id"], unique=False
    )

    # proposals (D-11) -----------------------------------------------------
    op.create_table(
        "proposals",
        sa.Column("proposal_id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column(
            "strategy_id",
            sa.String(),
            sa.ForeignKey("strategies.strategy_id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.Column("client_order_id", sa.String(), nullable=True),
        sa.Column("broker_order_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            _in_check("status", _PROPOSAL_STATUSES),
            name="ck_proposal_status",
        ),
    )
    op.create_index(
        "ix_proposals_user_id", "proposals", ["user_id"], unique=False
    )
    op.create_index(
        "ix_proposals_strategy_id",
        "proposals",
        ["strategy_id"],
        unique=False,
    )

    # events (D-14 audit log) ----------------------------------------------
    op.create_table(
        "events",
        sa.Column(
            "id", sa.Integer(), primary_key=True, autoincrement=True
        ),
        sa.Column("ts", sa.String(), nullable=False),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column(
            "strategy_id",
            sa.String(),
            sa.ForeignKey("strategies.strategy_id"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("row_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            _in_check("event_type", _EVENT_TYPES),
            name="ck_event_type",
        ),
    )
    op.create_index(
        "ix_events_user_id", "events", ["user_id"], unique=False
    )
    op.create_index(
        "ix_events_strategy_id", "events", ["strategy_id"], unique=False
    )
    op.create_index(
        "ix_events_user_id_id", "events", ["user_id", "id"], unique=False
    )

    # broker_credentials (AUTH-03 storage row) -----------------------------
    op.create_table(
        "broker_credentials",
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.user_id"),
            primary_key=True,
        ),
        sa.Column("broker", sa.String(), primary_key=True),
        sa.Column("key_blob", sa.String(), nullable=False),
        sa.Column("secret_blob", sa.String(), nullable=False),
        sa.Column(
            "paper",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("created_at", sa.String(), nullable=False),
    )


# ---------------------------------------------------------------------------
# Downgrade — FK-dependency-reversed
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # events references strategies (via strategy_id FK) and users — drop first.
    op.drop_index("ix_events_user_id_id", table_name="events")
    op.drop_index("ix_events_strategy_id", table_name="events")
    op.drop_index("ix_events_user_id", table_name="events")
    op.drop_table("events")

    # proposals references strategies + users.
    op.drop_index("ix_proposals_strategy_id", table_name="proposals")
    op.drop_index("ix_proposals_user_id", table_name="proposals")
    op.drop_table("proposals")

    # guidance references strategies + users.
    op.drop_index("ix_guidance_strategy_id", table_name="guidance")
    op.drop_index("ix_guidance_user_id", table_name="guidance")
    op.drop_table("guidance")

    # broker_credentials references users.
    op.drop_table("broker_credentials")

    # strategies references users.
    op.drop_index("ix_strategy_name_lookup", table_name="strategies")
    op.drop_index("ix_strategies_user_id", table_name="strategies")
    op.drop_table("strategies")

    # users is referenced by everything above.
    op.drop_table("users")
