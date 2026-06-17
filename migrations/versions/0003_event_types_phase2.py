"""event_types_phase2 — extend ck_event_type for Phase-2 credential/promotion events

Revision ID: 0003_event_types_phase2
Revises: 0002_orderguard
Create Date: 2026-06-17 00:00:00

Phase-2 code-review BL-01: the Phase-2 plan promised new audit
``event_type`` values for the credential + live-promotion + first-live
audit chain:

  * ``credentials_added``        — vault/credentials.py
  * ``live_mode_promoted``       — strategy/promotion.py
  * ``live_mode_demoted``        — strategy/promotion.py
  * ``first_live_trade_confirmed`` — strategy/promotion.py

These never made it into ``_EVENT_TYPES`` and the writers were forced to
emit them as ``event_type="error"`` with a ``context`` discriminator in
the payload, polluting the error bucket and breaking the
"filter on event_type" forensic story.

This migration drops + recreates ``ck_event_type`` so the CHECK accepts
the four new values. SQLite CHECK constraints aren't ALTERable in
place — every constraint change goes through ``op.batch_alter_table``.

The frozen vocabularies are duplicated locally because Alembic migrations
are frozen historical artifacts (Plan 01-03 convention).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_event_types_phase2"
down_revision: str | None = "0002_orderguard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Frozen vocabularies (kept LOCAL to the migration — Plan 01-03 convention)
# ---------------------------------------------------------------------------

# Post-0003: includes the four Phase-2 credential / promotion event types.
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
)

# Pre-0003: matches 0001_initial.py's _EVENT_TYPES exactly.
_FROZEN_EVENT_TYPES_PRE = (
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
    with op.batch_alter_table("events") as bop:
        bop.drop_constraint("ck_event_type", type_="check")
        bop.create_check_constraint(
            "ck_event_type",
            _in_check("event_type", _FROZEN_EVENT_TYPES),
        )


# ---------------------------------------------------------------------------
# Downgrade — restore pre-Phase-2 event vocabulary
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # NOTE: any rows written with the new event_type values must be
    # cleaned up BEFORE downgrading, otherwise the recreated CHECK will
    # reject them at insert time. This downgrade does NOT touch existing
    # rows — the operator is responsible for the data migration.
    with op.batch_alter_table("events") as bop:
        bop.drop_constraint("ck_event_type", type_="check")
        bop.create_check_constraint(
            "ck_event_type",
            _in_check("event_type", _FROZEN_EVENT_TYPES_PRE),
        )
