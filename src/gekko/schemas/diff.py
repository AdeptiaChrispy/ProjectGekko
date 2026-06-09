"""Plain-English strategy diff — Plan 01-06 Task 1 (D-02).

When the user edits a Strategy, the dashboard shows a human-readable summary
("You changed max-position from 5% to 7% and added MSFT, GOOGL to the
watchlist.") rather than a JSON diff (D-02).

Two public helpers:

* :func:`compute_field_changes` — pure-data dict of changed fields with
  ``(old, new)`` tuples or, for ``watchlist``, separate ``watchlist_added`` /
  ``watchlist_removed`` lists.
* :func:`generate_strategy_diff` — formats the changes into a plain-English
  sentence suitable for direct rendering in the dashboard or Slack.

The implementation is deterministic Python — NOT LLM-generated — per RESEARCH
§"Don't Hand-Roll": the LLM-generated diff is also acceptable but the
deterministic path is simpler for P1 and the LLM path can replace it in P6
if the user requests prettier prose.

References:
  * .planning/phases/01-foundation.../01-CONTEXT.md  D-02 — plain-English diff
  * .planning/phases/01-foundation.../01-RESEARCH.md  §"Don't Hand-Roll"
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gekko.schemas.strategy import Strategy


# Display labels for each HardCaps field — keeps the prose readable.
_HARD_CAP_LABELS: dict[str, str] = {
    "max_position_pct": "max-position",
    "max_daily_loss_usd": "max-daily-loss",
    "max_trades_per_day": "max-trades-per-day",
    "max_sector_exposure_pct": "max-sector-exposure",
}

# Subset of HardCaps fields that should render as percentages.
_HARD_CAP_PCT_FIELDS: frozenset[str] = frozenset(
    {"max_position_pct", "max_sector_exposure_pct"}
)


def compute_field_changes(before: Strategy, after: Strategy) -> dict[str, Any]:
    """Compute a dict of changed fields between two Strategy snapshots.

    Returns ``{}`` if the two strategies are equal. Otherwise:

    * For each HardCaps field that changed: key is the cap field name (e.g.,
      ``"max_position_pct"``), value is ``(old, new)``.
    * For ``watchlist``: ``"watchlist_added"`` (list[str]) and
      ``"watchlist_removed"`` (list[str]) keys, each present only if non-empty.
    * For ``thesis``, ``schedule_time``, ``mode``, ``name``: standard
      ``(old, new)`` tuple under the field name.

    ``strategy_id``, ``user_id``, ``version``, ``created_at``, and
    ``created_by_chat`` are intentionally NOT included — they're metadata that
    always changes on a new version and would just be noise in the diff.
    """
    changes: dict[str, Any] = {}

    # HardCaps — walk each field individually so the diff is granular.
    for cap_field in _HARD_CAP_LABELS:
        old_val = getattr(before.hard_caps, cap_field)
        new_val = getattr(after.hard_caps, cap_field)
        if old_val != new_val:
            changes[cap_field] = (old_val, new_val)

    # Watchlist — add/remove sets.
    old_watch = set(before.watchlist)
    new_watch = set(after.watchlist)
    added = sorted(new_watch - old_watch)
    removed = sorted(old_watch - new_watch)
    if added:
        changes["watchlist_added"] = added
    if removed:
        changes["watchlist_removed"] = removed

    # Scalar fields.
    for field in ("thesis", "schedule_time", "mode", "name"):
        old_val = getattr(before, field)
        new_val = getattr(after, field)
        if old_val != new_val:
            changes[field] = (old_val, new_val)

    return changes


def _format_pct(value: Decimal) -> str:
    """Format a Decimal fraction (0..1) as a percent string.

    ``Decimal("0.05")`` → ``"5%"`` (integral case)
    ``Decimal("0.075")`` → ``"7.5%"`` (fractional case)
    """
    pct = value * Decimal("100")
    # Strip trailing zeros for prettier integer percentages.
    normalized = pct.normalize()
    # ``Decimal("5.00").normalize()`` → ``Decimal("5")``; ``Decimal("7.50")`` → ``Decimal("7.5")``.
    text = format(normalized, "f")
    return f"{text}%"


def _format_usd(value: Decimal) -> str:
    """Format a Decimal dollar amount as ``"$NNN"`` (integer USD where possible)."""
    normalized = value.normalize()
    text = format(normalized, "f")
    return f"${text}"


def _format_cap_value(field: str, value: Any) -> str:
    if field in _HARD_CAP_PCT_FIELDS and isinstance(value, Decimal):
        return _format_pct(value)
    if field == "max_daily_loss_usd" and isinstance(value, Decimal):
        return _format_usd(value)
    return str(value)


def generate_strategy_diff(before: Strategy, after: Strategy) -> str:
    """Produce a plain-English description of changes between two strategies.

    Returns ``"No changes."`` if the two strategies are equal.

    Examples:

    * ``"You changed max-position from 5% to 7%."``
    * ``"You added MSFT, GOOGL to the watchlist."``
    * ``"You edited the thesis."``
    * ``"You changed max-position from 5% to 7% and added MSFT to the watchlist."``

    Per D-02 this is the user-facing summary; the deterministic Python
    implementation is fine for P1. P6 may replace with an LLM-generated diff
    if the user wants richer prose.
    """
    changes = compute_field_changes(before, after)
    if not changes:
        return "No changes."

    sentences: list[str] = []

    # HardCaps changes — render in label order for stable output.
    for cap_field, label in _HARD_CAP_LABELS.items():
        if cap_field in changes:
            old_v, new_v = changes[cap_field]
            sentences.append(
                f"changed {label} from {_format_cap_value(cap_field, old_v)} "
                f"to {_format_cap_value(cap_field, new_v)}"
            )

    # Watchlist add/remove.
    if "watchlist_added" in changes:
        added = ", ".join(changes["watchlist_added"])
        sentences.append(f"added {added} to the watchlist")
    if "watchlist_removed" in changes:
        removed = ", ".join(changes["watchlist_removed"])
        sentences.append(f"removed {removed} from the watchlist")

    # Thesis — keep it short, "edited the thesis".
    if "thesis" in changes:
        sentences.append("edited the thesis")

    # Schedule time — handle add / remove / change.
    if "schedule_time" in changes:
        old_st, new_st = changes["schedule_time"]
        if old_st is None and new_st is not None:
            sentences.append(f"added a daily schedule at {new_st}")
        elif old_st is not None and new_st is None:
            sentences.append(f"removed the daily schedule (was {old_st})")
        else:
            sentences.append(f"changed the daily schedule from {old_st} to {new_st}")

    # Mode — STRAT-06 paper/live flip (UI confirmation enforced elsewhere).
    if "mode" in changes:
        old_m, new_m = changes["mode"]
        sentences.append(f"changed mode from {old_m} to {new_m}")

    # Name (rare but possible during rename).
    if "name" in changes:
        old_n, new_n = changes["name"]
        sentences.append(f"renamed from '{old_n}' to '{new_n}'")

    if not sentences:
        return "No changes."

    # Join with commas + final "and" for readability.
    if len(sentences) == 1:
        body = sentences[0]
    elif len(sentences) == 2:
        body = " and ".join(sentences)
    else:
        body = ", ".join(sentences[:-1]) + ", and " + sentences[-1]

    return f"You {body}."


__all__: tuple[str, ...] = (
    "compute_field_changes",
    "generate_strategy_diff",
)
