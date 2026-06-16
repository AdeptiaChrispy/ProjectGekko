"""orderguard — Phase 2 Wave-1 additive schema changes

Revision ID: 0002_orderguard
Revises: 0001_initial
Create Date: 2026-06-16 12:00:00

Plan 02-01 Task 4 — adds the schema surface every Phase-2 plan reads from:

  * users.kill_active + kill_active_since + kill_active_reason (D-35, D-36 / EXEC-06)
  * strategy_metadata table (D-31, D-32 — live promotion ladder state)
  * broker_credentials.kind column with CHECK + composite PK extension (D-34)
  * proposals.status CHECK extended with AWAITING_2ND_CHANNEL + APPROVED_LIVE (BLOCKER #1)
  * proposals.account_mode column with CHECK + PAPER backfill (BLOCKER #5)

CheckConstraint vocabularies are duplicated locally because Alembic migrations
are frozen historical artifacts (Plan 01-03 convention).

SQLite requires batch_alter_table for CHECK constraint changes + PK changes;
every ALTER goes through ``op.batch_alter_table`` per the SQLAlchemy SQLite
migration pattern. The migration body never references the SQLCipher
passphrase — that lives in alembic env.py + the connect-event closure.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_orderguard"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Frozen vocabularies (kept LOCAL to the migration — Plan 01-03 convention)
# ---------------------------------------------------------------------------

# Phase 1 + Phase 2 statuses. The CHECK constraint is rebuilt with this set.
_FROZEN_PROPOSAL_STATUSES = (
    "PENDING",
    "APPROVED",
    "REJECTED",
    "EXECUTING",
    "FILLED",
    "FAILED",
    "AWAITING_2ND_CHANNEL",
    "APPROVED_LIVE",
)

_FROZEN_PROPOSAL_STATUSES_P1 = (
    "PENDING",
    "APPROVED",
    "REJECTED",
    "EXECUTING",
    "FILLED",
    "FAILED",
)

_FROZEN_ACCOUNT_MODES = ("PAPER", "LIVE")
_FROZEN_BROKER_CREDENTIAL_KINDS = ("alpaca_paper", "alpaca_live")


def _in_check(column: str, allowed: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in allowed)})"


# ---------------------------------------------------------------------------
# Upgrade — forward
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # 1. users — add kill_active columns (D-35 / D-36).
    with op.batch_alter_table("users") as bop:
        bop.add_column(
            sa.Column(
                "kill_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        bop.add_column(
            sa.Column("kill_active_since", sa.String(), nullable=True)
        )
        bop.add_column(
            sa.Column("kill_active_reason", sa.String(), nullable=True)
        )

    # 2. strategy_metadata — new table (D-31 / D-32).
    op.create_table(
        "strategy_metadata",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("strategy_name", sa.String(), nullable=False),
        sa.Column(
            "live_mode_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("live_promoted_at", sa.String(), nullable=True),
        sa.Column(
            "first_live_trade_confirmed_at", sa.String(), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"], name="fk_strategy_metadata_user_id"
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "strategy_name", name="pk_strategy_metadata"
        ),
    )

    # 3. broker_credentials — add ``kind`` column (D-34).
    #    Add as NULLABLE first so backfill SQL can populate from ``paper``.
    with op.batch_alter_table("broker_credentials") as bop:
        bop.add_column(sa.Column("kind", sa.String(), nullable=True))

    # 3a. Backfill kind from the existing ``paper`` column for any pre-existing
    #     rows. Phase-1 was paper-only, so all existing rows become alpaca_paper.
    op.execute(
        "UPDATE broker_credentials SET kind = 'alpaca_paper' WHERE paper = 1"
    )
    op.execute(
        "UPDATE broker_credentials SET kind = 'alpaca_live' WHERE paper = 0"
    )
    # Catch the (no-row) case where backfill doesn't fire — set a default
    # for any future row created via the ORM where kind is unset.
    op.execute(
        "UPDATE broker_credentials SET kind = 'alpaca_paper' WHERE kind IS NULL"
    )

    # 3b. Make kind NOT NULL + add CHECK + extend PK to include kind.
    with op.batch_alter_table("broker_credentials") as bop:
        bop.alter_column(
            "kind",
            existing_type=sa.String(),
            nullable=False,
            server_default=sa.text("'alpaca_paper'"),
        )
        bop.create_check_constraint(
            "ck_broker_credentials_kind",
            _in_check("kind", _FROZEN_BROKER_CREDENTIAL_KINDS),
        )

    # 4. proposals — extend status CHECK (BLOCKER #1) + add account_mode (BLOCKER #5).
    #    Drop old CHECK first, then add account_mode as NULLABLE, backfill,
    #    then add NEW status CHECK + account_mode NOT NULL + account_mode CHECK.
    with op.batch_alter_table("proposals") as bop:
        bop.drop_constraint("ck_proposal_status", type_="check")
        bop.add_column(
            sa.Column("account_mode", sa.String(), nullable=True)
        )

    # 4a. Backfill ALL pre-migration proposals to 'PAPER' (Phase-1 was paper-only
    #     per D-24). BLOCKER #5 requires this run BEFORE the NOT NULL alter.
    op.execute(
        "UPDATE proposals SET account_mode='PAPER' WHERE account_mode IS NULL"
    )

    # 4b. account_mode -> NOT NULL with server_default; recreate status CHECK
    #     with the extended Phase-2 vocab; add account_mode CHECK.
    with op.batch_alter_table("proposals") as bop:
        bop.alter_column(
            "account_mode",
            existing_type=sa.String(),
            nullable=False,
            server_default=sa.text("'PAPER'"),
        )
        bop.create_check_constraint(
            "ck_proposal_status",
            _in_check("status", _FROZEN_PROPOSAL_STATUSES),
        )
        bop.create_check_constraint(
            "ck_proposals_account_mode",
            _in_check("account_mode", _FROZEN_ACCOUNT_MODES),
        )


# ---------------------------------------------------------------------------
# Downgrade — FK-dependency-reversed
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # 4. Reverse proposals — drop CHECKs + account_mode + restore Phase-1 CHECK.
    with op.batch_alter_table("proposals") as bop:
        bop.drop_constraint("ck_proposals_account_mode", type_="check")
        bop.drop_constraint("ck_proposal_status", type_="check")
        bop.drop_column("account_mode")
        bop.create_check_constraint(
            "ck_proposal_status",
            _in_check("status", _FROZEN_PROPOSAL_STATUSES_P1),
        )

    # 3. Reverse broker_credentials — drop CHECK + kind.
    with op.batch_alter_table("broker_credentials") as bop:
        bop.drop_constraint("ck_broker_credentials_kind", type_="check")
        bop.drop_column("kind")

    # 2. Reverse strategy_metadata.
    op.drop_table("strategy_metadata")

    # 1. Reverse users kill_active columns.
    with op.batch_alter_table("users") as bop:
        bop.drop_column("kill_active_reason")
        bop.drop_column("kill_active_since")
        bop.drop_column("kill_active")
