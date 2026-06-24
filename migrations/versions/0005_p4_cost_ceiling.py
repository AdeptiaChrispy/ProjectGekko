"""p4_cost_ceiling — Phase 4 Wave-2 schema substrate

Revision ID: 0005_p4_cost_ceiling
Revises: 0004_p3_hitl_ux
Create Date: 2026-06-23 00:00:00

Plan 04-02 Task 1 — adds the schema surface Phase 4 cost-ceiling plans require:

  * users.daily_cost_ceiling_usd (TEXT, server_default '5.00') — configurable
    per-user daily LLM cost ceiling (D-02). NULL is treated as DEFAULT_DAILY_CEILING_USD
    at read time; server_default ensures the column is non-NULL for new rows.
  * users.cost_alert_80_sent_date (TEXT nullable) — ISO date string (YYYY-MM-DD
    in the user's timezone) when the 80% threshold DM was last sent (D-06).
    NULL means never sent. Guard compares against today's local date to enforce
    the "one DM per day" rule.
  * users.cost_alert_100_sent_date (TEXT nullable) — same as above for the 100%
    halt DM (D-08).
  * events.ck_event_type CHECK extended with two Phase-4 event types:
    - ``llm_cost``          — per-query() cost ledger entry (COST-05)
    - ``suspicious_content``— SC-2 prompt-injection pattern detected in research
      evidence (logged at brief-parse time by _run_researcher())

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
revision: str = "0005_p4_cost_ceiling"
down_revision: str | None = "0004_p3_hitl_ux"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Frozen vocabularies (kept LOCAL to the migration — Plan 01-03 convention)
# ---------------------------------------------------------------------------

# --- events.event_type ---

# Pre-0005: matches 0004_p3_hitl_ux.py's _FROZEN_EVENT_TYPES_POST exactly.
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
    "expiration",
    "dedup_click",
    "edit_size",
    "daily_pnl",
)

# Post-0005: adds the two Phase-4 audit event types.
_FROZEN_EVENT_TYPES_POST = _FROZEN_EVENT_TYPES_PRE + (
    "llm_cost",
    "suspicious_content",
)


def _in_check(column: str, allowed: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in allowed)})"


# ---------------------------------------------------------------------------
# Upgrade — forward
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # 1. users — add daily cost ceiling + alert-sent-date columns (D-02 / D-12).
    with op.batch_alter_table("users") as bop:
        bop.add_column(
            sa.Column(
                "daily_cost_ceiling_usd",
                sa.String(),
                nullable=True,
                server_default="5.00",
            )
        )
        bop.add_column(
            sa.Column("cost_alert_80_sent_date", sa.String(), nullable=True)
        )
        bop.add_column(
            sa.Column("cost_alert_100_sent_date", sa.String(), nullable=True)
        )

    # 2. events — extend ck_event_type with llm_cost + suspicious_content.
    with op.batch_alter_table("events") as bop:
        bop.drop_constraint("ck_event_type", type_="check")
        bop.create_check_constraint(
            "ck_event_type",
            _in_check("event_type", _FROZEN_EVENT_TYPES_POST),
        )


# ---------------------------------------------------------------------------
# Downgrade — reverse order (events CHECK first, then users columns)
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # 2. Reverse events CHECK.
    with op.batch_alter_table("events") as bop:
        bop.drop_constraint("ck_event_type", type_="check")
        bop.create_check_constraint(
            "ck_event_type",
            _in_check("event_type", _FROZEN_EVENT_TYPES_PRE),
        )

    # 1. Reverse users cost-ceiling + alert-sent-date columns.
    with op.batch_alter_table("users") as bop:
        bop.drop_column("cost_alert_100_sent_date")
        bop.drop_column("cost_alert_80_sent_date")
        bop.drop_column("daily_cost_ceiling_usd")
