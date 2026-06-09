"""``get_edgar_filing`` Researcher tool — RES-03 — Plan 01-07 Task 3.

Fetches the most recent 10-K or 10-Q for a US ticker from SEC EDGAR via
the public REST API. No SDK — we use ``httpx`` directly, sending the
``User-Agent: settings.gekko_user_agent`` header per RESEARCH §Pitfall 12
(SEC fair-use policy requires it).

Two-stage call:

1. ``https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=...&type=...&dateb=&owner=include&count=10&output=atom``
   — but the canonical machine-readable path is the **tickers JSON** at
   ``https://www.sec.gov/files/company_tickers.json`` followed by the
   submissions JSON at ``https://data.sec.gov/submissions/CIK{cik}.json``.
2. The submissions JSON contains a recent-filings array; we pick the most
   recent 10-K/10-Q entry and build the archive URL.

Per docs/sdk-shape.md deltas #1 and #2: positional ``@tool`` decorator,
``async def fn(args: dict) -> dict`` signature, MCP content-shape return.

References:
  * .planning/.../01-RESEARCH.md  §Pitfall 12 (SEC fair-use User-Agent)
  * https://www.sec.gov/os/accessing-edgar-data
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
from claude_agent_sdk import tool

from gekko.agent.tools.context import get_tool_context
from gekko.config import get_settings
from gekko.logging_config import get_logger
from gekko.schemas.research import EvidenceSnippet

log = get_logger(__name__)

#: Token-cost estimate per ``get_edgar_filing`` invocation.
_TOKEN_COST: int = 300

#: SEC EDGAR fair-use rate limit: max 10 req/sec.
_RATE_LIMIT_SLEEP_S: float = 0.1

#: SEC tickers JSON — maps ticker symbols to CIK numbers.
_TICKERS_JSON_URL: str = "https://www.sec.gov/files/company_tickers.json"

#: SEC submissions JSON template (CIK is left-padded to 10 digits).
_SUBMISSIONS_URL: str = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

#: Filing types we accept as "the most recent 10-K or 10-Q".
_ACCEPTED_FORMS: frozenset[str] = frozenset({"10-K", "10-Q"})


def _form_one_liner(form: str) -> str:
    """One-line human description for the brief's ``summary`` field."""
    if form == "10-K":
        return "Annual report — full audited financials, MD&A, risks."
    if form == "10-Q":
        return "Quarterly report — unaudited financials, MD&A updates."
    return f"SEC filing ({form})."


def _lookup_cik(tickers_payload: dict[str, Any], ticker: str) -> int | None:
    """Find the CIK for ``ticker`` in the SEC's tickers JSON payload.

    The payload is a dict-of-dicts keyed by integer-as-string. Each row has
    keys ``cik_str``, ``ticker``, ``title``.
    """
    target = ticker.upper().strip()
    for row in tickers_payload.values():
        if not isinstance(row, dict):
            continue
        if str(row.get("ticker", "")).upper() == target:
            try:
                return int(row["cik_str"])
            except (KeyError, ValueError, TypeError):
                return None
    return None


@tool(
    "get_edgar_filing",
    (
        "Fetch the most recent 10-K or 10-Q for a US equity ticker from SEC "
        "EDGAR. Returns a JSON EvidenceSnippet with filing type + date + "
        "accession + summary + source_url."
    ),
    {"ticker": str},
)
async def get_edgar_filing(args: dict[str, Any]) -> dict[str, Any]:
    """Researcher tool — RES-03 — SEC EDGAR most-recent 10-K/10-Q.

    Sends the load-bearing ``User-Agent`` header per RESEARCH §Pitfall 12.
    """
    ctx = get_tool_context()
    ticker = args["ticker"].upper().strip()
    settings = get_settings()
    headers = {"User-Agent": settings.gekko_user_agent}

    async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
        # 1. Resolve ticker -> CIK.
        try:
            r1 = await client.get(_TICKERS_JSON_URL)
            r1.raise_for_status()
        except httpx.HTTPError as exc:
            log.error(
                "research.get_edgar.tickers_json_failed",
                ticker=ticker,
                error_message=str(exc),
            )
            ctx.budget.record_call(tokens=_TOKEN_COST)
            msg = f"SEC tickers JSON fetch failed for {ticker}: {exc}"
            raise

        tickers_payload = r1.json()
        cik = _lookup_cik(tickers_payload, ticker)
        if cik is None:
            log.warning("research.get_edgar.cik_not_found", ticker=ticker)
            ctx.budget.record_call(tokens=_TOKEN_COST)
            msg = f"CIK not found in SEC tickers JSON for {ticker}"
            raise ValueError(msg)

        await asyncio.sleep(_RATE_LIMIT_SLEEP_S)

        # 2. Pull submissions JSON.
        try:
            r2 = await client.get(_SUBMISSIONS_URL.format(cik=cik))
            r2.raise_for_status()
        except httpx.HTTPError as exc:
            log.error(
                "research.get_edgar.submissions_failed",
                ticker=ticker,
                cik=cik,
                error_message=str(exc),
            )
            ctx.budget.record_call(tokens=_TOKEN_COST)
            raise

        submissions = r2.json()

    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    # Find the first index whose form is in the accepted set.
    chosen_idx: int | None = None
    for idx, form in enumerate(forms):
        if form in _ACCEPTED_FORMS:
            chosen_idx = idx
            break

    if chosen_idx is None:
        log.warning(
            "research.get_edgar.no_recent_10k_or_10q",
            ticker=ticker,
            cik=cik,
            recent_forms=forms[:5],
        )
        ctx.budget.record_call(tokens=_TOKEN_COST)
        msg = f"no recent 10-K or 10-Q found for {ticker} (CIK={cik})"
        raise ValueError(msg)

    form = forms[chosen_idx]
    date = dates[chosen_idx] if chosen_idx < len(dates) else "unknown"
    accession_raw = accessions[chosen_idx] if chosen_idx < len(accessions) else ""
    accession_clean = accession_raw.replace("-", "")
    primary_doc = primary_docs[chosen_idx] if chosen_idx < len(primary_docs) else ""

    # Standard EDGAR archive URL: ".../Archives/edgar/data/{cik}/{accession_clean}/{primary_doc}"
    archive_url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/"
        f"{accession_clean}/{primary_doc}"
        if primary_doc
        else (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}"
        )
    )

    summary = (
        f"{form} filed {date} (accession {accession_raw}). "
        f"{_form_one_liner(form)}"
    )

    snippet = EvidenceSnippet(
        source_type="edgar_filing",
        source_url=archive_url,
        fetched_at=datetime.now(UTC).isoformat(),
        summary=summary,
    )

    ctx.budget.record_call(tokens=_TOKEN_COST)
    return {
        "content": [{"type": "text", "text": snippet.model_dump_json()}],
        "is_error": False,
    }


__all__: tuple[str, ...] = ("get_edgar_filing",)
