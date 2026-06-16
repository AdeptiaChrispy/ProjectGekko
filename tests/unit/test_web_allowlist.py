"""``gekko.research.allowlist`` tests — RES-07 / D-39 — Plan 02-04 Task 1.

Covers the single source of truth for the web-source allowlist:
:data:`WEB_ALLOWLIST` (16-entry seed) +
:data:`WEB_ALLOWLIST_PARENT_SUFFIXES` (``.gov`` / ``.edu`` wildcards) +
:func:`is_host_allowed` (right-to-left parent walk).

Defense surfaces verified:
* Exact host match (case insensitive)
* Subdomain match via parent walk (``research.sec.gov`` → ``sec.gov``)
* Wildcard ``.gov`` / ``.edu`` match (``treasury.gov`` → ``.gov``)
* T-02-04-P-04 spoofing: ``sec.gov.evil.example.com`` correctly REJECTED
* Phase-1 backward compatibility: ``ALLOWED_DOMAINS`` re-export is a
  Pydantic-style alias, not a copy.
"""

from __future__ import annotations


def test_module_surface_exports() -> None:
    """Behavior 1: the three public names import cleanly."""
    from gekko.research.allowlist import (
        WEB_ALLOWLIST,
        WEB_ALLOWLIST_PARENT_SUFFIXES,
        is_host_allowed,
    )

    assert WEB_ALLOWLIST is not None
    assert WEB_ALLOWLIST_PARENT_SUFFIXES is not None
    assert callable(is_host_allowed)


def test_web_allowlist_is_frozenset() -> None:
    """Behavior 2: WEB_ALLOWLIST is immutable (defensive against caller mutation)."""
    from gekko.research.allowlist import WEB_ALLOWLIST

    assert isinstance(WEB_ALLOWLIST, frozenset)
    # frozenset has no .add — attribute access raises.
    import pytest

    with pytest.raises(AttributeError):
        WEB_ALLOWLIST.add("attacker.example.com")  # type: ignore[attr-defined]


def test_web_allowlist_contains_research_seed() -> None:
    """Behavior 3: WEB_ALLOWLIST contains the RESEARCH §8 16-entry initial seed."""
    from gekko.research.allowlist import WEB_ALLOWLIST

    expected_seed = {
        "sec.gov",
        "finra.org",
        "reuters.com",
        "bloomberg.com",
        "ft.com",
        "wsj.com",
        "marketwatch.com",
        "barrons.com",
        "investors.com",
        "finance.yahoo.com",
        "seekingalpha.com",
        "alpaca.markets",
        "finnhub.io",
        "alphavantage.co",
        "businesswire.com",
        "alphaquery.com",
    }
    assert expected_seed.issubset(WEB_ALLOWLIST), (
        f"Missing seed entries: {expected_seed - WEB_ALLOWLIST}"
    )
    # Lock the exact size to catch accidental adds during P2.
    assert len(WEB_ALLOWLIST) == 16, (
        f"WEB_ALLOWLIST should be exactly 16 entries per RESEARCH §8; "
        f"got {len(WEB_ALLOWLIST)}. Extra: {WEB_ALLOWLIST - expected_seed}"
    )


def test_parent_suffixes_set_exact() -> None:
    """Behavior 4: WEB_ALLOWLIST_PARENT_SUFFIXES == frozenset({.gov, .edu})."""
    from gekko.research.allowlist import WEB_ALLOWLIST_PARENT_SUFFIXES

    assert WEB_ALLOWLIST_PARENT_SUFFIXES == frozenset({".gov", ".edu"})


def test_is_host_allowed_exact_match() -> None:
    """Behavior 5: exact match in WEB_ALLOWLIST → True."""
    from gekko.research.allowlist import is_host_allowed

    assert is_host_allowed("sec.gov") is True
    assert is_host_allowed("reuters.com") is True
    assert is_host_allowed("finnhub.io") is True


def test_is_host_allowed_case_insensitive() -> None:
    """Behavior 6: host is lowercased internally."""
    from gekko.research.allowlist import is_host_allowed

    assert is_host_allowed("SEC.GOV") is True
    assert is_host_allowed("Reuters.com") is True


def test_is_host_allowed_subdomain_parent_walk() -> None:
    """Behavior 7: subdomain walks back to allowlisted parent."""
    from gekko.research.allowlist import is_host_allowed

    # research.sec.gov → parent sec.gov is in WEB_ALLOWLIST → True
    assert is_host_allowed("research.sec.gov") is True
    # www.reuters.com → parent reuters.com is in WEB_ALLOWLIST → True
    assert is_host_allowed("www.reuters.com") is True


def test_is_host_allowed_deeper_subdomain() -> None:
    """Behavior 8: multi-level subdomain still walks to allowlisted parent."""
    from gekko.research.allowlist import is_host_allowed

    assert is_host_allowed("data.research.sec.gov") is True


def test_is_host_allowed_wildcard_gov() -> None:
    """Behavior 9: parent-suffix .gov matches arbitrary government hosts."""
    from gekko.research.allowlist import is_host_allowed

    # treasury.gov NOT in exact WEB_ALLOWLIST but .gov is a parent suffix
    assert is_host_allowed("treasury.gov") is True
    assert is_host_allowed("federalreserve.gov") is True


def test_is_host_allowed_wildcard_edu() -> None:
    """Behavior 10: parent-suffix .edu matches arbitrary university hosts."""
    from gekko.research.allowlist import is_host_allowed

    assert is_host_allowed("mit.edu") is True
    assert is_host_allowed("research.mit.edu") is True


def test_is_host_allowed_off_allowlist_exact() -> None:
    """Behavior 11: off-allowlist host → False."""
    from gekko.research.allowlist import is_host_allowed

    assert is_host_allowed("evil.example.com") is False
    assert is_host_allowed("malicious.example.com") is False


def test_is_host_allowed_spoofed_subdomain_rejected() -> None:
    """Behavior 12 (T-02-04-P-04): crafted subdomain like 'sec.gov.evil.example.com' rejected.

    The right-to-left parent walk traverses to "evil.example.com" then
    "example.com" then "com" — NEVER to "sec.gov". Closes the
    parent-walk spoofing surface called out in the Phase-2 threat
    register.
    """
    from gekko.research.allowlist import is_host_allowed

    assert is_host_allowed("sec.gov.evil.example.com") is False
    assert is_host_allowed("reuters.com.attacker.example") is False


def test_is_host_allowed_empty_and_none() -> None:
    """Behavior 13: None / empty / whitespace → False (defensive)."""
    from gekko.research.allowlist import is_host_allowed

    assert is_host_allowed(None) is False
    assert is_host_allowed("") is False
    assert is_host_allowed("   ") is False


def test_is_host_allowed_bare_single_label() -> None:
    """Behavior 14: bare 'gov' (single label) is NOT allowlisted.

    Single-label 'gov' is not in WEB_ALLOWLIST, and the parent-suffix
    set requires an actual parent (".gov" matches when "gov" appears as
    a parent of something, not when "gov" IS the entire host).
    """
    from gekko.research.allowlist import is_host_allowed

    assert is_host_allowed("gov") is False
    assert is_host_allowed("edu") is False


def test_phase1_web_fetch_alias_is_same_object() -> None:
    """Behavior 15: web_fetch.ALLOWED_DOMAINS re-exports WEB_ALLOWLIST as alias.

    No duplicate hardcoded list — single source of truth at
    ``gekko.research.allowlist``. Phase-1 callers that import
    ``gekko.agent.tools.web_fetch.ALLOWED_DOMAINS`` continue to work
    because the symbol now aliases ``WEB_ALLOWLIST``.
    """
    import gekko.agent.tools.web_fetch as web_fetch_mod
    import gekko.research.allowlist as allowlist_mod

    # `is` identity — they reference the SAME frozenset object.
    assert web_fetch_mod.ALLOWED_DOMAINS is allowlist_mod.WEB_ALLOWLIST
