"""p3_hitl_ux — Phase 3 Wave-1 schema substrate

Revision ID: 0004_p3_hitl_ux
Revises: 0003_event_types_phase2
Create Date: 2026-06-17 00:00:00

Plan 03-01 Task 2 — adds the schema surface every Phase-3 plan reads from:

  * slack_action_dedup table (D-45) — dedup gate for approve/reject/edit-size
    across Slack and Dashboard surfaces; two UNIQUE indexes enforce the
    at-most-once semantics per (proposal, action, actor) tuple.
  * users.quiet_hours_start + users.quiet_hours_end + users.timezone (D-47/D-49)
    — per-user quiet-hours window for suppressing routine DMs.
  * proposals.expires_at (D-51/D-61) — stamped at proposal-build time by
    ProposalWriter; grandfathered NULL for pre-migration rows.
  * proposals.slack_message_ts + proposals.slack_message_channel (D-53)
    — captured by post_run_result() so the sweep's chat.update has the
    ts+channel to target.
  * proposals.status CHECK extended with EXPIRED (A6) — accepted by the
    state machine via the new (PENDING, EXPIRED) + (AWAITING_2ND_CHANNEL,
    EXPIRED) edges.
  * events.event_type CHECK extended with expiration, dedup_click, edit_size,
    daily_pnl — the four new Phase-3 audit event types.

CheckConstraint vocabularies are duplicated locally because Alembic migrations
are frozen historical artifacts (Plan 01-03 convention). The migration body
never references the SQLCipher passphrase — that lives in alembic env.py +
the connect-event closure.

SQLite requires batch_alter_table for CHECK constraint changes; every ALTER
goes through ``op.batch_alter_table`` per the Phase-2 0002/0003 pattern.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_p3_hitl_ux"
down_revision: str | None = "0003_event_types_phase2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Frozen vocabularies (kept LOCAL to the migration — Plan 01-03 convention)
# ---------------------------------------------------------------------------

# --- proposals.status ---

# Post-0004: includes EXPIRED (A6).
_FROZEN_PROPOSAL_STATUSES_POST = (
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

# Pre-0004: matches 0002_orderguard.py's _FROZEN_PROPOSAL_STATUSES exactly.
_FROZEN_PROPOSAL_STATUSES_PRE = (
    "PENDING",
    "APPROVED",
    "REJECTED",
    "EXECUTING",
    "FILLED",
    "FAILED",
    "AWAITING_2ND_CHANNEL",
    "APPROVED_LIVE",
)

# --- events.event_type ---

# Post-0004: includes the four Phase-3 audit event types.
_FROZEN_EVENT_TYPES_POST = (
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
)

# Pre-0004: matches 0003_event_types_phase2.py's _FROZEN_EVENT_TYPES exactly.
_FROZEN_EVENT_TYPES_PRE = (
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
)

# --- slack_action_dedup CHECK vocabularies ---

_FROZEN_DEDUP_SOURCES = ("slack", "dashboard", "cli")
_FROZEN_DEDUP_RESULTS = ("first_write", "duplicate")


def _in_check(column: str, allowed: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in allowed)})"


# ---------------------------------------------------------------------------
# Upgrade — forward
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # 1. slack_action_dedup — new table (D-45).
    op.create_table(
        "slack_action_dedup",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("proposal_id", sa.String(), nullable=False),
        sa.Column("action_id", sa.String(), nullable=False),
        sa.Column("actor_slack_user_id", sa.String(), nullable=True),
        sa.Column("actor_gekko_user_id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("slack_trigger_id", sa.String(), nullable=True),
        sa.Column("inserted_at", sa.String(), nullable=False),
        sa.Column("result", sa.String(), nullable=False),
        sa.CheckConstraint(
            _in_check("source", _FROZEN_DEDUP_SOURCES),
            name="ck_dedup_source",
        ),
        sa.CheckConstraint(
            _in_check("result", _FROZEN_DEDUP_RESULTS),
            name="ck_dedup_result",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["proposals.proposal_id"],
            name="fk_dedup_proposal_id",
        ),
        sa.ForeignKeyConstraint(
            ["actor_gekko_user_id"],
            ["users.user_id"],
            name="fk_dedup_actor_user_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_slack_action_dedup"),
    )

    # 1a. Unique indexes on slack_action_dedup (D-42 Slack / D-56 dashboard).
    op.create_index(
        "uq_dedup_slack",
        "slack_action_dedup",
        ["proposal_id", "action_id", "actor_slack_user_id"],
        unique=True,
    )
    op.create_index(
        "uq_dedup_dashboard",
        "slack_action_dedup",
        ["proposal_id", "action_id", "actor_gekko_user_id", "source"],
        unique=True,
    )

    # 2. users — add quiet_hours_* + timezone columns (D-47 / D-49).
    with op.batch_alter_table("users") as bop:
        bop.add_column(sa.Column("quiet_hours_start", sa.String(), nullable=True))
        bop.add_column(sa.Column("quiet_hours_end", sa.String(), nullable=True))
        bop.add_column(sa.Column("timezone", sa.String(), nullable=True))

    # 3. proposals — add expires_at + slack_message_ts + slack_message_channel (D-51/D-53).
    #    Also drop + recreate ck_proposal_status to accept EXPIRED (A6).
    with op.batch_alter_table("proposals") as bop:
        # 3a. Drop old proposal status CHECK before adding new columns.
        bop.drop_constraint("ck_proposal_status", type_="check")
        # 3b. Add new nullable columns (no backfill needed — NULL is the
        #     grandfathered value for pre-migration rows per D-61).
        bop.add_column(sa.Column("expires_at", sa.String(), nullable=True))
        bop.add_column(sa.Column("slack_message_ts", sa.String(), nullable=True))
        bop.add_column(sa.Column("slack_message_channel", sa.String(), nullable=True))
        # 3c. Recreate status CHECK with EXPIRED added.
        bop.create_check_constraint(
            "ck_proposal_status",
            _in_check("status", _FROZEN_PROPOSAL_STATUSES_POST),
        )

    # 4. events — extend ck_event_type with the four Phase-3 event types.
    with op.batch_alter_table("events") as bop:
        bop.drop_constraint("ck_event_type", type_="check")
        bop.create_check_constraint(
            "ck_event_type",
            _in_check("event_type", _FROZEN_EVENT_TYPES_POST),
        )


# ---------------------------------------------------------------------------
# Downgrade — FK-dependency-reversed order (table first per T-03-01-01)
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # Drop slack_action_dedup FIRST (it references proposals.proposal_id
    # which we narrow the CHECK on in the next step — no FK violation).
    op.drop_index("uq_dedup_dashboard", table_name="slack_action_dedup")
    op.drop_index("uq_dedup_slack", table_name="slack_action_dedup")
    op.drop_table("slack_action_dedup")

    # 4. Reverse events CHECK.
    with op.batch_alter_table("events") as bop:
        bop.drop_constraint("ck_event_type", type_="check")
        bop.create_check_constraint(
            "ck_event_type",
            _in_check("event_type", _FROZEN_EVENT_TYPES_PRE),
        )

    # 3. Reverse proposals columns + restore pre-0004 status CHECK.
    with op.batch_alter_table("proposals") as bop:
        bop.drop_constraint("ck_proposal_status", type_="check")
        bop.drop_column("slack_message_channel")
        bop.drop_column("slack_message_ts")
        bop.drop_column("expires_at")
        bop.create_check_constraint(
            "ck_proposal_status",
            _in_check("status", _FROZEN_PROPOSAL_STATUSES_PRE),
        )

    # 2. Reverse users quiet_hours_* + timezone columns.
    with op.batch_alter_table("users") as bop:
        bop.drop_column("timezone")
        bop.drop_column("quiet_hours_end")
        bop.drop_column("quiet_hours_start")
