"""``gekko.research`` — Researcher subagent shared infrastructure.

The Researcher subagent's source-allowlist + prompt-injection minimums
(RES-06 / RES-07 — Phase 2 D-39/D-40) live here as a single source of
truth so Researcher tools (`web_fetch`, `finnhub_news`, ...) import from
one canonical place.

See :mod:`gekko.research.allowlist` for the curated `WEB_ALLOWLIST`
frozenset + `is_host_allowed` helper.
"""

from __future__ import annotations
