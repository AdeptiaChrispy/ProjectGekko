"""Append-only audit log with SHA-256 hash chain — Plan 01-04 (AUDT-01 / AUDT-02).

Public surface for callers (Plans 01-07 / 01-08 / 01-09 wire concrete
``event_type`` writes):

* :data:`GENESIS_PREV_HASH` — the ``"0" * 64`` seed for any user's first event.
* :func:`canonical_json` — byte-stable JSON for hash inputs.
* :func:`normalize_decimals` — caller-side Decimal normalization helper.
* :func:`append_event` — the single-source-of-truth audit writer.
* :func:`walk_chain` — integrity verifier (returns ids of broken rows).

References:
  * CONTEXT.md D-14 / D-15 / D-16
  * CONTEXT.md Claude's Discretion A11 (genesis ``prev_hash``)
  * RESEARCH.md §"Audit log SHA-256 hash chain" — Pattern 3 lock-in
  * RESEARCH.md §"Pitfall 6" — Decimal normalization
"""

from __future__ import annotations

from gekko.audit.canonical import (
    GENESIS_PREV_HASH,
    canonical_json,
    normalize_decimals,
)
from gekko.audit.log import append_event

__all__: tuple[str, ...] = (
    "GENESIS_PREV_HASH",
    "append_event",
    "canonical_json",
    "normalize_decimals",
)
