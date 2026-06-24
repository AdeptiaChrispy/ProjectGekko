"""p4_cost_ceiling_repair — Phase 4 data-repair migration

Revision ID: 0006_p4_cost_ceiling_repair
Revises: 0005_p4_cost_ceiling
Create Date: 2026-06-24 00:00:00

Plan 04-07 Task 1 — closes the /spend HTTP 500 gap caused by migration 0005
storing daily_cost_ceiling_usd with a malformed server_default.

Root cause: 0005 declared the column with ``server_default="'5.00'"``
(already-quoted string). SQLAlchemy rendered that as DDL ``DEFAULT '''5.00'''``,
which SQLite stored as the 6-char string ``'5.00'`` INCLUDING the literal
single-quote characters. ``Decimal("'5.00'")`` raises InvalidOperation.

Two operations:

  1. Idempotent repair UPDATE: converts the over-quoted ``'5.00'`` (6-char,
     with literal apostrophes) to clean ``5.00`` (4-char). The WHERE clause
     targets ONLY the exact over-quoted form — rows with already-clean values
     are untouched. Safe to run multiple times.

  2. batch_alter_table alter_column: sets ``server_default="5.00"`` (un-quoted)
     so NEW rows created after this migration get a clean Decimal-parseable
     default. SQLAlchemy renders ``server_default="5.00"`` as DDL ``DEFAULT
     '5.00'``, which SQLite stores as the 4-char clean string.

This migration does NOT change event types (0005 already added llm_cost and
suspicious_content). The frozen vocabulary is carried forward unchanged.

Downgrade reverses only the column default — the data repair is intentionally
not reversed (downgrading to 0005 does not re-corrupt the data).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_p4_cost_ceiling_repair"
down_revision: str | None = "0005_p4_cost_ceiling"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Frozen vocabularies (kept LOCAL to the migration — Plan 01-03 convention)
# ---------------------------------------------------------------------------

# 0006 does NOT change event types.  Carry 0005 POST forward unchanged.
# This is the FULL set as of 0005_p4_cost_ceiling; named _FROZEN_EVENT_TYPES
# (not PRE/POST) since no extension occurs here.
_FROZEN_EVENT_TYPES = (
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
    "llm_cost",
    "suspicious_content",
)


# ---------------------------------------------------------------------------
# Upgrade — forward
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # OPERATION 1 — Repair existing corrupted rows.
    # The over-quoted value stored by migration 0005's wrong server_default is
    # the 6-char string: apostrophe + "5.00" + apostrophe (i.e. "'5.00'").
    # In SQLite SQL, to match a string containing single quotes we double the
    # inner quotes: '''5.00''' means: outer quotes delimit the string, inner
    # doubled quotes are literal apostrophes — yielding the string '5.00'.
    # The SET target is the clean 4-char string "5.00".
    # This UPDATE is idempotent: on a DB where the value is already clean "5.00"
    # the WHERE never matches, so no rows are touched.
    op.execute(
        "UPDATE users SET daily_cost_ceiling_usd = '5.00' "
        "WHERE daily_cost_ceiling_usd = '''5.00'''"
    )

    # OPERATION 2 — Correct the column server_default going forward.
    # server_default="5.00" (no surrounding single quotes): SQLAlchemy renders
    # this as DDL DEFAULT '5.00', which SQLite stores as the 4-char clean string.
    # Compare with 0005's server_default="'5.00'" which rendered as
    # DEFAULT '''5.00''' and stored the 6-char over-quoted string.
    with op.batch_alter_table("users") as bop:
        bop.alter_column(
            "daily_cost_ceiling_usd",
            existing_type=sa.String(),
            server_default="5.00",
            nullable=True,
        )


# ---------------------------------------------------------------------------
# Downgrade — reverse ONLY the column default change
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # Reverse the column default back to the (buggy) 0005 form.
    # Data repair is intentionally NOT reversed — downgrading to 0005 does
    # not re-corrupt the data.  The repaired rows keep their clean "5.00" values.
    with op.batch_alter_table("users") as bop:
        bop.alter_column(
            "daily_cost_ceiling_usd",
            existing_type=sa.String(),
            server_default="'5.00'",
            nullable=True,
        )
