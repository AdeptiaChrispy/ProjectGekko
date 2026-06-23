"""Real-SQLite integration test for dashboard HITL action endpoints (Plan 03-15).

Coverage matrix — 8 test cases:

  Test 1 — approve first_write happy path
  Test 2 — approve duplicate (second call, same proposal)
  Test 3 — approve on terminal proposal (Bug C gate — FILLED → 200, no ValueError 500)
  Test 4 — edit-submit first_write happy path
  Test 5 — edit-submit duplicate (Bug B regression gate — second call → 200, no 500)
  Test 6 — edit-submit on terminal proposal (Bug C gate for edit path)
  Test 7 — reject first_write happy path
  Test 8 — edit-size GET: HX-Request fragment vs direct-nav redirect (Bug A gate)

Design contract:
  - claim_action, transition_status, and append_event are NOT mocked.
    They run against the real SQLite engine so seam failures are visible.
  - _get_session_factory on routes + dedup is monkeypatched to the shared test SF.
  - execute_proposal and asyncio.create_task are mocked so no broker calls fire.
  - AlpacaBroker.get_account is mocked for the equity fetch in edit_size_get/submit.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.db.models import (
    Proposal as ProposalRow,
    SlackActionDedup,
    Strategy as StrategyRow,
    User,
)
from gekko.db.session import make_session_factory
from gekko.schemas.proposal import AlternativeConsidered, TradeProposal
from gekko.schemas.research import EvidenceSnippet

pytestmark = pytest.mark.integration

_TEST_PASSPHRASE = "test-passphrase"  # must match conftest._TEST_PASSPHRASE
_TEST_USER = "hitl-test-user"


# ---------------------------------------------------------------------------
# Trade-proposal builder (mirrors test_dedup_race._make_trade_proposal)
# ---------------------------------------------------------------------------


def _make_trade_proposal(user_id: str, decision_id: str) -> TradeProposal:
    return TradeProposal(
        user_id=user_id,
        strategy_name="ai-infra-bull",
        decision_id=decision_id,
        ticker="NVDA",
        side="buy",
        qty=Decimal("100"),
        target_notional_usd=Decimal("10000"),
        order_type="market",
        limit_price=None,
        stop_price=None,
        rationale="AI infrastructure demand strong.",
        confidence=Decimal("0.78"),
        evidence=[
            EvidenceSnippet(
                source_type="alpaca_quote",
                source_url="https://alpaca.markets/quotes/NVDA",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="last $100.00",
            ),
            EvidenceSnippet(
                source_type="finnhub_news",
                source_url="https://finnhub.io/news/nvda",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="beat by 12%",
            ),
            EvidenceSnippet(
                source_type="edgar_filing",
                source_url="https://sec.gov/edgar/data/nvda",
                fetched_at="2026-06-08T11:30:00+00:00",
                summary="10-Q filed",
            ),
        ],
        alternatives_considered=[
            AlternativeConsidered(
                description="AMD",
                why_rejected="Lower data-center exposure",
            ),
        ],
        client_order_id="d" * 32,
        account_mode="PAPER",
    )


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------


async def _seed_pending_proposal(
    sf: Any,
    user_id: str,
    proposal_id: str,
    *,
    status: str = "PENDING",
) -> tuple[str, TradeProposal]:
    """Seed User + Strategy + Proposal rows. Returns (strategy_id, tp)."""
    strategy_id = "strat-hitl-" + uuid4().hex[:8]
    tp = _make_trade_proposal(user_id=user_id, decision_id=proposal_id)
    now = datetime.now(UTC).isoformat()

    # Use a fresh client_order_id per call to avoid UNIQUE constraint clash
    # across tests (all tests use the same "d"*32 template but unique proposal_ids).
    # The client_order_id UNIQUE constraint is per proposals row — it's fine as
    # long as tests don't share the same proposal_id.

    async with sf() as session, session.begin():
        session.add(User(user_id=user_id, created_at=now))
        await session.flush()
        # Strategy payload must parse as a valid Strategy for edit_size_submit cap check.
        # Use json.dumps to build a minimal but valid Strategy JSON with all required fields.
        strategy_payload_json = json.dumps({
            "strategy_id": strategy_id,
            "user_id": user_id,
            "name": "ai-infra-bull",
            "thesis": "AI infrastructure strategy for testing.",
            "watchlist": ["NVDA"],
            "version": 1,
            "created_at": now,
            "hard_caps": {
                "max_position_pct": "0.20",
                "max_daily_loss_usd": "5000",
                "max_trades_per_day": 10,
                "max_sector_exposure_pct": "0.50",
            },
        })
        session.add(
            StrategyRow(
                strategy_id=strategy_id,
                user_id=user_id,
                strategy_name="ai-infra-bull",
                version=1,
                payload_json=strategy_payload_json,
                created_at=now,
            )
        )
        await session.flush()
        session.add(
            ProposalRow(
                proposal_id=proposal_id,
                user_id=user_id,
                strategy_id=strategy_id,
                status=status,
                payload_json=tp.model_dump_json(),
                client_order_id=tp.client_order_id + proposal_id[:8],
                broker_order_id=None,
                created_at=now,
                updated_at=now,
                account_mode="PAPER",
            )
        )
        await append_event(
            session,
            user_id=user_id,
            strategy_id=strategy_id,
            event_type="proposal",
            payload=normalize_decimals(tp.model_dump(mode="python")),
        )

    return strategy_id, tp


# ---------------------------------------------------------------------------
# App + client helper
# ---------------------------------------------------------------------------


async def _get_authenticated_client(
    sf: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> tuple[Any, Any]:
    """Set up patches, create app, login. Returns (app, client).

    The caller is responsible for closing the client:
        async with httpx.AsyncClient(...) as client: ...
    """
    import gekko.vault.passphrase as _vault
    from gekko.approval import dedup as _dedup_mod
    from gekko.audit import log as _audit_log
    from gekko.config import get_settings
    from gekko.dashboard import routes as _dash_routes
    from gekko.dashboard.app import create_app

    # Clear stale async locks from prior tests.
    _audit_log._append_locks.clear()

    # Set env for the test user.
    monkeypatch.setenv("GEKKO_USER_ID", _TEST_USER)
    monkeypatch.setenv("SLACK_USER_ID", _TEST_USER)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "test-alpaca-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test-alpaca-secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-bot")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-signing")
    # Redirect lifespan's DB open to a temp dir (not production data dir).
    monkeypatch.setenv("GEKKO_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()

    # Passphrase vault.
    _vault.set_passphrase(_TEST_PASSPHRASE)

    # Patch session factories so routes + dedup use the shared test engine.
    monkeypatch.setattr(_dash_routes, "_get_session_factory", lambda _u: (sf, None))
    monkeypatch.setattr(_dedup_mod, "_get_session_factory", lambda _u: (sf, None))

    # Mock broker equity fetch (edit_size_get + edit_size_submit).
    from gekko.brokers.alpaca import AlpacaBroker as _AlpacaBroker
    monkeypatch.setattr(
        _AlpacaBroker,
        "get_account",
        AsyncMock(return_value={"equity": "10000"}),
    )

    # Mock executor dispatch + asyncio.create_task so no real broker fires.
    from gekko.execution import executor
    _mock_execute = AsyncMock()
    monkeypatch.setattr(executor, "execute_proposal", _mock_execute)
    monkeypatch.setattr(executor, "_get_session_factory", lambda _u: (sf, None))

    # create_task must accept a coroutine and return a Task-like object.
    # We use a real asyncio.create_task but with a no-op coroutine replacement.
    async def _noop_coro(*args: Any, **kwargs: Any) -> None:
        pass

    real_create_task = asyncio.create_task

    def _safe_create_task(coro: Any, **kwargs: Any) -> Any:
        # If the coro was spawned by execute_proposal, replace with noop.
        # Otherwise pass through (e.g. for lifespan background tasks).
        try:
            if hasattr(coro, "cr_qualname") and "execute_proposal" in coro.cr_qualname:
                coro.close()
                return real_create_task(_noop_coro(), **kwargs)
            return real_create_task(coro, **kwargs)
        except Exception:
            return real_create_task(_noop_coro(), **kwargs)

    monkeypatch.setattr(asyncio, "create_task", _safe_create_task)

    # Create the app. The lifespan will open its own engine in tmp_path; that
    # engine is only used for the banner_mode middleware (not the route handlers
    # which use the monkeypatched _get_session_factory).
    app = create_app()

    return app, _vault


# ---------------------------------------------------------------------------
# Test 1 — approve first_write happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_first_write_happy(
    temp_sqlcipher_db: Any,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /approve on a PENDING proposal: HTTP 200, card returned, APPROVED in DB."""
    import gekko.vault.passphrase as _vault
    from gekko.config import get_settings

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id = "hitl-t1-" + uuid4().hex[:8]

    await _seed_pending_proposal(sf, _TEST_USER, proposal_id)

    app, _vault_ref = await _get_authenticated_client(sf, monkeypatch, tmp_path)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            login_resp = await client.post(
                "/login", data={"passphrase": _TEST_PASSPHRASE, "next": "/approvals"}
            )
            assert login_resp.status_code == 303

            resp = await client.post(f"/approvals/{proposal_id}/approve")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        assert "proposal-card" in resp.text, "Expected proposal-card in response"

        # DB: proposal should be APPROVED (or EXECUTING if executor fired)
        async with sf() as session:
            row = (
                await session.execute(
                    select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
                )
            ).scalar_one()
        assert row.status in ("APPROVED", "EXECUTING", "FILLED"), (
            f"Expected APPROVED+, got {row.status}"
        )

        # Dedup row should exist.
        async with sf() as session:
            dedup_rows = (
                await session.execute(
                    select(SlackActionDedup).where(
                        SlackActionDedup.proposal_id == proposal_id,
                        SlackActionDedup.action_id == "approve_proposal",
                    )
                )
            ).scalars().all()
        assert len(dedup_rows) >= 1, "Expected dedup row for approve_proposal"
        assert any(r.source == "dashboard" for r in dedup_rows), (
            f"Expected source='dashboard'; got {[r.source for r in dedup_rows]}"
        )
    finally:
        _vault_ref.clear()
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Test 2 — approve duplicate (second call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_duplicate_returns_200(
    temp_sqlcipher_db: Any,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /approve twice on the same proposal: both return HTTP 200, no 500."""
    import gekko.vault.passphrase as _vault
    from gekko.config import get_settings

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id = "hitl-t2-" + uuid4().hex[:8]

    await _seed_pending_proposal(sf, _TEST_USER, proposal_id)

    app, _vault_ref = await _get_authenticated_client(sf, monkeypatch, tmp_path)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            await client.post("/login", data={"passphrase": _TEST_PASSPHRASE, "next": "/approvals"})

            resp1 = await client.post(f"/approvals/{proposal_id}/approve")
            resp2 = await client.post(f"/approvals/{proposal_id}/approve")

        assert resp1.status_code == 200, f"First approve: expected 200, got {resp1.status_code}"
        assert resp2.status_code == 200, (
            f"Second approve (duplicate): expected 200, got {resp2.status_code}: {resp2.text[:500]}"
        )
        # Both should return the card, not an error page.
        assert "proposal-card" in resp2.text, (
            "Expected proposal-card in second (duplicate) approve response"
        )
        assert "Internal Server Error" not in resp2.text and "ValueError" not in resp2.text, (
            f"Second approve should not return 500/ValueError; got: {resp2.text[:500]}"
        )
    finally:
        _vault_ref.clear()
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Test 3 — approve on terminal proposal (Bug C gate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_terminal_proposal_returns_200(
    temp_sqlcipher_db: Any,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /approve on a FILLED proposal: HTTP 200 with current card, no ValueError 500."""
    import gekko.vault.passphrase as _vault
    from gekko.config import get_settings

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id = "hitl-t3-" + uuid4().hex[:8]

    # Seed with terminal status directly.
    await _seed_pending_proposal(sf, _TEST_USER, proposal_id, status="FILLED")

    app, _vault_ref = await _get_authenticated_client(sf, monkeypatch, tmp_path)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            await client.post("/login", data={"passphrase": _TEST_PASSPHRASE, "next": "/approvals"})
            resp = await client.post(f"/approvals/{proposal_id}/approve")

        assert resp.status_code == 200, (
            f"Approve on FILLED: expected 200, got {resp.status_code}: {resp.text[:500]}"
        )
        assert "Internal Server Error" not in resp.text, f"Should not return 500: {resp.text[:500]}"
        assert "ValueError" not in resp.text, f"Should not return ValueError: {resp.text[:500]}"
        assert "proposal-card" in resp.text, "Expected proposal-card in response"
    finally:
        _vault_ref.clear()
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Test 4 — edit-submit first_write happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_submit_first_write_happy(
    temp_sqlcipher_db: Any,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /edit-submit on a PENDING proposal: HTTP 200, APPROVED in DB, dedup row."""
    import gekko.vault.passphrase as _vault
    from gekko.config import get_settings

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id = "hitl-t4-" + uuid4().hex[:8]

    await _seed_pending_proposal(sf, _TEST_USER, proposal_id)

    app, _vault_ref = await _get_authenticated_client(sf, monkeypatch, tmp_path)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            await client.post("/login", data={"passphrase": _TEST_PASSPHRASE, "next": "/approvals"})
            # qty=50 is within the 20% cap on $10,000 equity (max=20 shares at $100/share=2000, actually
            # with equity $10000 and max_position_pct=0.20, max_notional=$2000, max_shares=floor(2000/100)=20)
            # Let's use qty=5 which is well within caps.
            resp = await client.post(
                f"/approvals/{proposal_id}/edit-submit", data={"qty": "5"}
            )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        assert "proposal-card" in resp.text, "Expected proposal-card in response"

        # DB: proposal should be APPROVED (executor is mocked so no EXECUTING).
        async with sf() as session:
            row = (
                await session.execute(
                    select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
                )
            ).scalar_one()
        assert row.status in ("APPROVED", "EXECUTING", "FILLED"), (
            f"Expected APPROVED+, got {row.status}"
        )

        # Dedup row for edit_size.
        async with sf() as session:
            dedup_rows = (
                await session.execute(
                    select(SlackActionDedup).where(
                        SlackActionDedup.proposal_id == proposal_id,
                        SlackActionDedup.action_id == "edit_size",
                    )
                )
            ).scalars().all()
        assert len(dedup_rows) >= 1, "Expected dedup row for edit_size"
    finally:
        _vault_ref.clear()
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Test 5 — edit-submit duplicate (Bug B regression gate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_submit_duplicate_returns_200(
    temp_sqlcipher_db: Any,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /edit-submit twice on same proposal: BOTH return 200. Second must not 500.

    This is the load-bearing Bug B regression test.
    Before Plan 03-15, the second call crashed with SQLAlchemy InvalidRequestError
    because the duplicate branch re-read on the rolled-back session2 inside
    the sf3/session2.begin() block.
    """
    import gekko.vault.passphrase as _vault
    from gekko.config import get_settings

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id = "hitl-t5-" + uuid4().hex[:8]

    await _seed_pending_proposal(sf, _TEST_USER, proposal_id)

    app, _vault_ref = await _get_authenticated_client(sf, monkeypatch, tmp_path)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            await client.post("/login", data={"passphrase": _TEST_PASSPHRASE, "next": "/approvals"})

            resp1 = await client.post(
                f"/approvals/{proposal_id}/edit-submit", data={"qty": "5"}
            )
            resp2 = await client.post(
                f"/approvals/{proposal_id}/edit-submit", data={"qty": "5"}
            )

        assert resp1.status_code == 200, f"First edit-submit: expected 200, got {resp1.status_code}"

        # The load-bearing regression gate:
        assert resp2.status_code == 200, (
            f"[BUG B REGRESSION] Second edit-submit (duplicate): expected 200, "
            f"got {resp2.status_code}: {resp2.text[:500]}"
        )
        assert "proposal-card" in resp2.text, (
            "Second edit-submit should return proposal-card, not an error page"
        )
        # Check that the response is not an HTTP 500 error page.
        # Avoid simple '500' substring match — the card may contain "$500.00" cost.
        assert "Internal Server Error" not in resp2.text, (
            f"Second edit-submit must not return Internal Server Error: {resp2.text[:500]}"
        )
        assert "InvalidRequestError" not in resp2.text and "ValueError" not in resp2.text, (
            f"Second edit-submit must not return SQLAlchemy/ValueError: {resp2.text[:500]}"
        )
    finally:
        _vault_ref.clear()
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Test 6 — edit-submit on terminal proposal (Bug C gate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_submit_terminal_proposal_returns_200(
    temp_sqlcipher_db: Any,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /edit-submit on a FILLED proposal: HTTP 200 with current card, no 500."""
    import gekko.vault.passphrase as _vault
    from gekko.config import get_settings

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id = "hitl-t6-" + uuid4().hex[:8]

    # Seed with terminal status directly.
    await _seed_pending_proposal(sf, _TEST_USER, proposal_id, status="FILLED")

    app, _vault_ref = await _get_authenticated_client(sf, monkeypatch, tmp_path)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            await client.post("/login", data={"passphrase": _TEST_PASSPHRASE, "next": "/approvals"})
            resp = await client.post(
                f"/approvals/{proposal_id}/edit-submit", data={"qty": "5"}
            )

        assert resp.status_code == 200, (
            f"[BUG C REGRESSION] edit-submit on FILLED: expected 200, "
            f"got {resp.status_code}: {resp.text[:500]}"
        )
        assert "Internal Server Error" not in resp.text, f"Should not return 500: {resp.text[:500]}"
        assert "ValueError" not in resp.text, f"Should not return ValueError: {resp.text[:500]}"
        assert "proposal-card" in resp.text, "Expected proposal-card in response"
    finally:
        _vault_ref.clear()
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Test 7 — reject first_write happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_first_write_happy(
    temp_sqlcipher_db: Any,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /reject on a PENDING proposal: HTTP 200, REJECTED in DB."""
    import gekko.vault.passphrase as _vault
    from gekko.config import get_settings

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id = "hitl-t7-" + uuid4().hex[:8]

    await _seed_pending_proposal(sf, _TEST_USER, proposal_id)

    app, _vault_ref = await _get_authenticated_client(sf, monkeypatch, tmp_path)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            await client.post("/login", data={"passphrase": _TEST_PASSPHRASE, "next": "/approvals"})
            resp = await client.post(f"/approvals/{proposal_id}/reject")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        assert "proposal-card" in resp.text, "Expected proposal-card in response"

        async with sf() as session:
            row = (
                await session.execute(
                    select(ProposalRow).where(ProposalRow.proposal_id == proposal_id)
                )
            ).scalar_one()
        assert row.status == "REJECTED", f"Expected REJECTED, got {row.status}"
    finally:
        _vault_ref.clear()
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Test 8 — edit-size GET: HX-Request fragment vs direct-nav redirect (Bug A gate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_size_get_hx_vs_direct_nav(
    temp_sqlcipher_db: Any,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /edit-size with vs without HX-Request header (Bug A regression gate).

    With HX-Request: true  → 200, bare fragment (no <!DOCTYPE html>)
    Without HX-Request     → 302 redirect to /approvals (full styled page after follow)
    """
    import gekko.vault.passphrase as _vault
    from gekko.config import get_settings

    sf = make_session_factory(temp_sqlcipher_db)
    proposal_id = "hitl-t8-" + uuid4().hex[:8]

    await _seed_pending_proposal(sf, _TEST_USER, proposal_id)

    app, _vault_ref = await _get_authenticated_client(sf, monkeypatch, tmp_path)

    try:
        # Client with follow_redirects=False so we can assert the 302.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            await client.post("/login", data={"passphrase": _TEST_PASSPHRASE, "next": "/approvals"})

            # HX-Request path — should return bare fragment (200, no DOCTYPE).
            htmx_resp = await client.get(
                f"/approvals/{proposal_id}/edit-size",
                headers={"HX-Request": "true"},
            )
            assert htmx_resp.status_code == 200, (
                f"HX path: expected 200, got {htmx_resp.status_code}"
            )
            assert "<!DOCTYPE" not in htmx_resp.text, (
                "HX path must return bare fragment (no DOCTYPE): "
                f"{htmx_resp.text[:200]}"
            )
            assert "edit" in htmx_resp.text.lower() or "qty" in htmx_resp.text.lower(), (
                "HX path should contain edit-size modal content"
            )

            # Direct-nav path (no HX-Request header) — should redirect to /approvals.
            direct_resp = await client.get(f"/approvals/{proposal_id}/edit-size")
            assert direct_resp.status_code in (302, 301), (
                f"[BUG A REGRESSION] Direct-nav should redirect, "
                f"got {direct_resp.status_code}: {direct_resp.text[:200]}"
            )
            # Redirect location should point to /approvals.
            location = direct_resp.headers.get("location", "")
            assert "/approvals" in location, (
                f"Redirect should point to /approvals, got location={location!r}"
            )
    finally:
        _vault_ref.clear()
        get_settings.cache_clear()
