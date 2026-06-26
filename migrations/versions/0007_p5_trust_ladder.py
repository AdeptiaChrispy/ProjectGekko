"""p5_trust_ladder — Phase 5 trust-ladder / portfolio-caps schema substrate

Revision ID: 0007_p5_trust_ladder
Revises: 0006_p4_cost_ceiling_repair
Create Date: 2026-06-26 00:00:00

Plan 05-01 Task 2 — adds the schema surface every Phase-5 slice (trust helpers,
portfolio caps, capital ceiling, anomaly reflex, auto-execute) reads:

  * strategy_metadata.trust_level (TEXT NOT NULL, server_default 'propose-only')
    — the per-strategy ladder rung (D-T16). Existing rows backfill to the safe
    'propose-only' rung.
  * strategy_metadata.trust_promoted_at (TEXT nullable) — ISO timestamp of the
    last promotion to auto-within-caps.
  * strategy_metadata.capital_ceiling_usd (TEXT nullable, server_default
    '1000.00') — caps total deployed capital for the strategy (D-T16).
  * strategy_metadata.anomaly_threshold_pct (TEXT nullable, server_default
    '0.10') — single-day drawdown fraction that trips the anomaly reflex (D-T11).
  * users.max_total_exposure_pct (TEXT nullable, server_default '0.50')
  * users.max_sector_concentration_pct (TEXT nullable, server_default '0.30')
  * users.max_correlated_ticker_pct (TEXT nullable, server_default '0.15')
  * users.max_total_daily_loss_usd (TEXT nullable, server_default '200.00')
    — the four account-wide portfolio caps (TRUST-04 / 05-UI-SPEC defaults).
  * events.ck_event_type CHECK extended with the five Phase-5 event types:
    trust_promoted, trust_demoted, anomaly_demotion, capital_scaled,
    auto_execution.

Money/percent columns are stored as TEXT (money-as-TEXT / percent-as-fraction-
TEXT convention); percentages are FRACTION strings ("0.50" == 50%).

server_default values are passed UN-QUOTED (e.g. "propose-only", not
"'propose-only'") — SQLAlchemy renders ``server_default="propose-only"`` as DDL
``DEFAULT 'propose-only'`` which SQLite stores as the clean string. This is the
0006_p4_cost_ceiling_repair lesson: an already-quoted server_default
("'5.00'") rendered as ``DEFAULT '''5.00'''`` and stored the over-quoted
6-char form, which broke ``Decimal(...)`` parsing. Do NOT re-introduce that bug.

CheckConstraint vocabularies are duplicated locally because Alembic migrations
are frozen historical artifacts (Plan 01-03 convention). The migration body
never references the SQLCipher passphrase — that lives in alembic env.py + the
connect-event closure.

SQLite requires batch_alter_table for CHECK constraint changes; every ALTER
goes through ``op.batch_alter_table`` per the Phase-2/3/4 pattern.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_p5_trust_ladder"
# CRITICAL: 0006 is the current head (a repair of 0005), NOT 0005 itself.
# Pinning to 0005 would skip the 0006 data-repair and corrupt the chain.
down_revision: str | None = "0006_p4_cost_ceiling_repair"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Frozen vocabularies (kept LOCAL to the migration — Plan 01-03 convention)
# ---------------------------------------------------------------------------

# --- events.event_type ---

# Pre-0007: matches the CURRENT head's vocabulary exactly — i.e.
# 0006_p4_cost_ceiling_repair._FROZEN_EVENT_TYPES (which carried 0005's POST
# forward unchanged). Equality is asserted by
# test_migration_0007.test_0007_frozen_vocab_pre_matches_0006.
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
    "llm_cost",
    "suspicious_content",
)

# Post-0007: adds the five Phase-5 audit event types.
_FROZEN_EVENT_TYPES_POST = _FROZEN_EVENT_TYPES_PRE + (
    "trust_promoted",
    "trust_demoted",
    "anomaly_demotion",
    "capital_scaled",
    "auto_execution",
)


def _in_check(column: str, allowed: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in allowed)})"


# ---------------------------------------------------------------------------
# Upgrade — forward
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # 1. strategy_metadata — add trust ladder + capital + anomaly columns.
    with op.batch_alter_table("strategy_metadata") as bop:
        bop.add_column(
            sa.Column(
                "trust_level",
                sa.String(),
                nullable=False,
                server_default="propose-only",
            )
        )
        bop.add_column(
            sa.Column("trust_promoted_at", sa.String(), nullable=True)
        )
        bop.add_column(
            sa.Column(
                "capital_ceiling_usd",
                sa.String(),
                nullable=True,
                server_default="1000.00",
            )
        )
        bop.add_column(
            sa.Column(
                "anomaly_threshold_pct",
                sa.String(),
                nullable=True,
                server_default="0.10",
            )
        )

    # 2. users — add the four account-wide portfolio caps (TRUST-04).
    with op.batch_alter_table("users") as bop:
        bop.add_column(
            sa.Column(
                "max_total_exposure_pct",
                sa.String(),
                nullable=True,
                server_default="0.50",
            )
        )
        bop.add_column(
            sa.Column(
                "max_sector_concentration_pct",
                sa.String(),
                nullable=True,
                server_default="0.30",
            )
        )
        bop.add_column(
            sa.Column(
                "max_correlated_ticker_pct",
                sa.String(),
                nullable=True,
                server_default="0.15",
            )
        )
        bop.add_column(
            sa.Column(
                "max_total_daily_loss_usd",
                sa.String(),
                nullable=True,
                server_default="200.00",
            )
        )

    # 3. events — extend ck_event_type with the five Phase-5 event types.
    with op.batch_alter_table("events") as bop:
        bop.drop_constraint("ck_event_type", type_="check")
        bop.create_check_constraint(
            "ck_event_type",
            _in_check("event_type", _FROZEN_EVENT_TYPES_POST),
        )


# ---------------------------------------------------------------------------
# Downgrade — reverse order (events CHECK first, then drop columns)
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # 3. Reverse events CHECK.
    with op.batch_alter_table("events") as bop:
        bop.drop_constraint("ck_event_type", type_="check")
        bop.create_check_constraint(
            "ck_event_type",
            _in_check("event_type", _FROZEN_EVENT_TYPES_PRE),
        )

    # 2. Reverse users portfolio-cap columns.
    with op.batch_alter_table("users") as bop:
        bop.drop_column("max_total_daily_loss_usd")
        bop.drop_column("max_correlated_ticker_pct")
        bop.drop_column("max_sector_concentration_pct")
        bop.drop_column("max_total_exposure_pct")

    # 1. Reverse strategy_metadata trust/capital/anomaly columns.
    with op.batch_alter_table("strategy_metadata") as bop:
        bop.drop_column("anomaly_threshold_pct")
        bop.drop_column("capital_ceiling_usd")
        bop.drop_column("trust_promoted_at")
        bop.drop_column("trust_level")
