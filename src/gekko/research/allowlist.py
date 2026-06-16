"""Source allowlist — RES-07 / D-39 — Plan 02-04 Task 1.

Single source of truth for the Researcher subagent's web-tier source
allowlist. Phase-1's hardcoded ``ALLOWED_DOMAINS`` frozenset at
``gekko.agent.tools.web_fetch`` re-imports from here so we do not
duplicate the curated list across modules.

Per Phase-2 D-39 the source-tier framework has three trust tiers:

* **Structured-API** (Alpaca quotes, EDGAR XBRL filings) — trusted; no
  delimiters needed; data flows through as parsed Python dicts.
* **News APIs** (Finnhub, Alpha Vantage) — semi-trusted; the API call
  itself is trusted but article body is third-party. Wrapped in
  ``<untrusted_content source="finnhub_news">...</untrusted_content>``.
* **Web** (browser-use / ``web_fetch``) — untrusted. Host allowlist
  filters BEFORE inclusion in the brief; allowed hosts wrap content in
  ``<untrusted_content source="web:{host}">...</untrusted_content>``;
  non-allowed hosts are dropped + logged.

This module owns the **Web tier** allowlist (the host-filter side); the
delimiter-wrap side lives at the tool boundary (see
``gekko.agent.tools.web_fetch`` + ``gekko.agent.tools.finnhub_news``).

The seed list below is locked from Phase-2 RESEARCH §8 lines 1454-1477
(16 entries). Adding / removing entries beyond what RESEARCH specifies
is a P4 operator-extensible per-user-override surface — out of scope
for Plan 02-04 per D-39.

Parent-suffix wildcards (``.gov``, ``.edu``) are intentionally inclusive
per D-39 "plus wildcard `*.gov`, `*.edu`" — any government / education
host counts as allowlisted by the right-to-left parent walk in
:func:`is_host_allowed`.

References:
  * .planning/.../02-CONTEXT.md     D-39 (source-tier framework + wildcard parents)
  * .planning/.../02-RESEARCH.md    §8 lines 1448-1498 (seed + body verbatim)
  * .planning/.../02-PATTERNS.md    §1a row 14 (allowlist.py EXACT-match Phase-1 migration)
"""

from __future__ import annotations

#: Curated web-source allowlist — per Phase-2 D-39 + RESEARCH §8 seed.
#:
#: Matched against the parsed URL hostname by :func:`is_host_allowed`
#: either as an EXACT host (``sec.gov``) or via a right-to-left parent
#: walk (``research.sec.gov`` → parent ``sec.gov`` is in the set →
#: True).
#:
#: Phase-2 list is a SUPERSET of Phase-1's 12-domain
#: ``gekko.agent.tools.web_fetch.ALLOWED_DOMAINS`` — adds ``finra.org``,
#: ``finnhub.io``, ``alphavantage.co``, ``alpaca.markets`` per D-39
#: trust-tier framework. The Phase-1 entries (sec.gov, reuters.com,
#: bloomberg.com, ft.com, wsj.com, marketwatch.com, barrons.com,
#: investors.com, finance.yahoo.com, seekingalpha.com, businesswire.com,
#: alphaquery.com) are preserved verbatim so the Phase-1 host-allowlist
#: gate keeps rejecting the same off-list hosts.
WEB_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Government / regulatory (high trust)
        "sec.gov",
        "finra.org",
        # Financial news (high-quality editorial)
        "reuters.com",
        "bloomberg.com",
        "ft.com",
        "wsj.com",
        "marketwatch.com",
        "barrons.com",
        "investors.com",
        # Yahoo + Seeking Alpha (high-volume, mixed quality)
        "finance.yahoo.com",
        "seekingalpha.com",
        # Data vendors (high trust — structured)
        "alpaca.markets",
        "finnhub.io",
        "alphavantage.co",
        # Issuer-direct (high trust)
        "businesswire.com",
        # Specialty options/equity data
        "alphaquery.com",
    }
)

#: Parent-suffix wildcards — any host whose right-side parent matches an
#: entry here is allowlisted. ``.gov`` allows ``treasury.gov``,
#: ``federalreserve.gov``, etc.; ``.edu`` allows any university research
#: subdomain. Each entry MUST start with a leading dot — the parent-walk
#: in :func:`is_host_allowed` concatenates a dot before the matched
#: parent to compare against this set.
WEB_ALLOWLIST_PARENT_SUFFIXES: frozenset[str] = frozenset({".gov", ".edu"})


def is_host_allowed(host: str | None) -> bool:
    """Return True if ``host`` (lowercased) is in the Phase-2 allowlist.

    Match rules (right-to-left, defense-in-depth against crafted
    subdomains):

    1. Empty / None host → False.
    2. Exact match in :data:`WEB_ALLOWLIST` → True
       (e.g., ``"sec.gov"``).
    3. Right-to-left parent walk — split the host on dots and for each
       parent suffix (longest first → shortest), check if that parent
       is in :data:`WEB_ALLOWLIST` (e.g., ``"research.sec.gov"`` walks
       to parent ``"sec.gov"`` → True). The same parent walk checks
       :data:`WEB_ALLOWLIST_PARENT_SUFFIXES` (so ``"treasury.gov"``
       walks to parent ``"gov"`` → ``".gov"`` is in the suffix set →
       True).
    4. Otherwise → False.

    The parent walk closes the T-02-04-P-04 spoofing surface: a
    crafted subdomain like ``"sec.gov.evil.example.com"`` walks
    right-to-left so the actual parent is ``"com"`` — NOT ``"sec.gov"``
    — and returns False.

    :param host: The parsed-URL hostname (already lowercased by the
        caller is fine; this function lowercases defensively).
    :returns: True iff ``host`` is allowlisted.
    """
    if not host:
        return False
    h = host.lower().strip()
    if not h:
        return False
    if h in WEB_ALLOWLIST:
        return True
    # Right-to-left parent walk: "data.research.sec.gov" walks through
    # "research.sec.gov", "sec.gov", "gov" — checking each against the
    # exact allowlist + the parent-suffix wildcards.
    parts = h.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[i:])
        if parent in WEB_ALLOWLIST:
            return True
        if "." + parent in WEB_ALLOWLIST_PARENT_SUFFIXES:
            return True
    return False


__all__: tuple[str, ...] = (
    "WEB_ALLOWLIST",
    "WEB_ALLOWLIST_PARENT_SUFFIXES",
    "is_host_allowed",
)
