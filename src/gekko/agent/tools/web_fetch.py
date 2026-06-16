"""``web_fetch`` Researcher tool — RES-04 — Plan 01-07 Task 3.

Fetches a single web URL from an allowlisted finance domain. The P1
allowlist is a curated dozen of finance-news/data sources (per RESEARCH
§Open Question 3); P4 hardens with a full domain-validation + content-
sanitization pass.

The allowlist is enforced BEFORE any network call: the URL is parsed,
the host is lowercased, and we check that the host (or a parent domain)
is in :data:`ALLOWED_DOMAINS`. Off-allowlist domains raise ``ValueError``.

Per docs/sdk-shape.md deltas #1 and #2.

References:
  * .planning/.../01-RESEARCH.md  §Open Question 3 (minimal allowlist)
  * .planning/.../01-SKELETON.md  §"What's Real vs Minimal" — httpx + allowlist (not browser-use)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from claude_agent_sdk import tool

from gekko.agent.tools.context import get_tool_context
from gekko.config import get_settings
from gekko.logging_config import get_logger
from gekko.research.allowlist import WEB_ALLOWLIST, is_host_allowed
from gekko.schemas.research import EvidenceSnippet

log = get_logger(__name__)

#: Token-cost estimate per ``web_fetch`` invocation.
_TOKEN_COST: int = 500

#: Backward-compat alias for Phase-1 callers. The canonical source of
#: truth is :data:`gekko.research.allowlist.WEB_ALLOWLIST` (Plan 02-04
#: Task 1 / D-39). Existing Phase-1 code paths that imported
#: ``ALLOWED_DOMAINS`` continue to resolve to the same frozenset object
#: — :data:`gekko.research.allowlist.WEB_ALLOWLIST` is.
ALLOWED_DOMAINS: frozenset[str] = WEB_ALLOWLIST

#: Max body chars to include in the returned EvidenceSnippet.quote_text.
_QUOTE_CHARS: int = 2000


def _host_is_allowed(host: str | None) -> bool:
    """Backward-compat shim — delegates to :func:`gekko.research.allowlist.is_host_allowed`.

    Phase-2 D-39 consolidated the host-allowlist check at
    ``gekko.research.allowlist``. This name is kept so any monkey-patch
    test seam in Phase-1 still resolves. New code should import
    :func:`is_host_allowed` from :mod:`gekko.research.allowlist`.
    """
    return is_host_allowed(host)


def _one_line_summary(body: str) -> str:
    """Build a 1-line summary from the first heading or sentence of the body.

    Strips simple HTML tags so we don't ship ``<h1>`` markup. P4 will swap
    this for a real BeautifulSoup pass + readability extractor.
    """
    import re

    # Strip tags & collapse whitespace.
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "(empty body)"
    # First period-or-newline-delimited chunk, capped at 300 chars.
    parts = re.split(r"(?<=[.!?])\s|\n", text, maxsplit=1)
    candidate = parts[0] if parts else text
    return candidate[:300]


@tool(
    "web_fetch",
    (
        "Fetch a single web URL from a P1-allowlisted finance domain. "
        "Off-allowlist URLs are rejected. Returns a JSON EvidenceSnippet "
        "with the page text excerpt."
    ),
    {"url": str},
)
async def web_fetch(args: dict[str, Any]) -> dict[str, Any]:
    """Researcher tool — RES-04 — allowlisted httpx fetch."""
    ctx = get_tool_context()
    url = str(args["url"]).strip()

    parsed = urlparse(url)
    if not is_host_allowed(parsed.hostname):
        log.warning(
            "research.web_fetch.off_allowlist",
            url=url,
            host=parsed.hostname,
        )
        msg = (
            f"Domain not in P1 allowlist: {parsed.hostname!r}. "
            "P4 will add full source-allowlist enforcement."
        )
        raise ValueError(msg)

    settings = get_settings()
    headers = {"User-Agent": settings.gekko_user_agent}

    async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    body = resp.text
    quote_text_raw = body[:_QUOTE_CHARS]
    summary = _one_line_summary(body)

    # D-39 / RES-07 Site 1 (Web tier): wrap content in <untrusted_content>
    # markers BEFORE it crosses into the Researcher's tool result. The
    # `source="web:{host}"` carries the parsed-URL hostname (lowercased)
    # so the Decision agent can see provenance at the wrap boundary.
    # Phase-2 D-40 warning text in DECISION_SYSTEM_PROMPT tells the LLM
    # to treat anything inside these markers as DATA, not instructions.
    host = (parsed.hostname or "").lower()
    quote_text_wrapped = (
        f'<untrusted_content source="web:{host}">\n'
        f"{quote_text_raw}\n"
        f"</untrusted_content>"
    )

    snippet = EvidenceSnippet(
        source_type="web_fetch",
        source_url=url,
        fetched_at=datetime.now(UTC).isoformat(),
        summary=summary,
        quote_text=quote_text_wrapped,
    )

    ctx.budget.record_call(tokens=_TOKEN_COST)
    return {
        "content": [{"type": "text", "text": snippet.model_dump_json()}],
        "is_error": False,
    }


__all__: tuple[str, ...] = ("ALLOWED_DOMAINS", "web_fetch")
