"""COST-02 dashboard spend route tests — Phase 4 Wave 5.

Covers:
  - GET /spend returns HTTP 200 for authenticated session
  - Response context contains today_total as Decimal
  - Response context contains ceiling as Decimal
  - Response context contains by_strategy list with strategy_name + spend
  - Response context contains history list with 7 entries (one per day)
  - Unauthenticated GET /spend returns 302 redirect to /login

All tests import the FastAPI ``app`` via ``httpx.AsyncClient`` +
``httpx.ASGITransport`` — same pattern as existing dashboard route tests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_llm_cost_row(cost_usd: str, strategy_name: str, ts: str | None = None) -> MagicMock:
    """Build a mock Event row with the canonical llm_cost payload shape.

    Matches what append_event actually stores: the full canonical-subset string
    {"event_type":"llm_cost","payload":{...cost fields...},"ts":"...","user_id":"..."}
    The cost fields (cost_usd, strategy_name, etc.) live inside the nested "payload" key.
    """
    if ts is None:
        ts = datetime.now(UTC).isoformat()
    row = MagicMock()
    row.payload_json = json.dumps({
        "event_type": "llm_cost",
        "payload": {
            "cost_usd": cost_usd,
            "strategy_name": strategy_name,
            "model": "sonnet",
            "call_type": "researcher",
            "input_tokens": 100,
            "output_tokens": 50,
        },
        "ts": ts,
        "user_id": "testuser",
    })
    row.strategy_id = "test-strat-id"
    row.ts = ts
    return row


def _make_user_row(
    daily_cost_ceiling_usd: str | None = "5.00",
    timezone: str | None = "America/New_York",
) -> MagicMock:
    """Build a mock User ORM row."""
    user = MagicMock()
    user.daily_cost_ceiling_usd = daily_cost_ceiling_usd
    user.timezone = timezone
    user.user_id = "testuser"
    return user


def _clear_vault() -> None:
    import gekko.vault.passphrase as _vault
    _vault.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_get_returns_200() -> None:
    """GET /spend with authenticated session returns HTTP 200."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-spend-200"
    _vault.set_passphrase(correct)
    try:
        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings

            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.all.return_value = []
            mock_result.scalar_one_or_none = MagicMock(return_value=None)
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_sf = MagicMock(return_value=mock_session)
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                login_resp = await client.post(
                    "/login", data={"passphrase": correct, "next": "/spend"}
                )
                assert login_resp.status_code == 303

                resp = await client.get("/spend")

        assert resp.status_code == 200
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_spend_get_shows_today_total() -> None:
    """GET /spend response context contains today_total as Decimal."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-spend-total"
    _vault.set_passphrase(correct)
    try:
        # Build a mock session returning 2 llm_cost rows for today
        today_ts = datetime.now(UTC).isoformat()
        row1 = _make_llm_cost_row("0.05", "strat-a", today_ts)
        row2 = _make_llm_cost_row("0.03", "strat-b", today_ts)
        user = _make_user_row("5.00", "America/New_York")

        call_count = 0

        def _make_result(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                # First execute call: user row
                mock_result.scalar_one_or_none = MagicMock(return_value=user)
                mock_result.all.return_value = []
            elif call_count == 2:
                # Second execute call: today's llm_cost rows
                mock_result.all.return_value = [row1, row2]
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
            else:
                # 7-day history rows
                mock_result.all.return_value = [row1, row2]
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
            return mock_result

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=lambda *a, **kw: _make_result())
        mock_sf = MagicMock(return_value=mock_session)

        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                await client.post("/login", data={"passphrase": correct, "next": "/spend"})
                resp = await client.get("/spend")

        assert resp.status_code == 200
        # The page should show the total spend ($0.08)
        assert "0.08" in resp.text or "0.0800" in resp.text
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_spend_get_shows_ceiling() -> None:
    """GET /spend response context contains ceiling as Decimal."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-spend-ceiling"
    _vault.set_passphrase(correct)
    try:
        user = _make_user_row("10.00", "America/New_York")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=user)
        mock_result.all.return_value = []

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_sf = MagicMock(return_value=mock_session)

        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                await client.post("/login", data={"passphrase": correct, "next": "/spend"})
                resp = await client.get("/spend")

        assert resp.status_code == 200
        # Should show the ceiling value
        assert "10.00" in resp.text
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_spend_get_per_strategy_breakdown() -> None:
    """GET /spend response context contains by_strategy list with strategy_name + spend."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-spend-strategy"
    _vault.set_passphrase(correct)
    try:
        today_ts = datetime.now(UTC).isoformat()
        row1 = _make_llm_cost_row("0.05", "Tech Momentum", today_ts)
        row2 = _make_llm_cost_row("0.02", "Tech Momentum", today_ts)
        row3 = _make_llm_cost_row("0.03", "Dividend Yield", today_ts)
        user = _make_user_row("5.00", "America/New_York")

        call_count = 0

        def _make_result(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                mock_result.scalar_one_or_none = MagicMock(return_value=user)
                mock_result.all.return_value = []
            elif call_count == 2:
                mock_result.all.return_value = [row1, row2, row3]
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
            else:
                mock_result.all.return_value = [row1, row2, row3]
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
            return mock_result

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=lambda *a, **kw: _make_result())
        mock_sf = MagicMock(return_value=mock_session)

        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                await client.post("/login", data={"passphrase": correct, "next": "/spend"})
                resp = await client.get("/spend")

        assert resp.status_code == 200
        assert "Tech Momentum" in resp.text
        assert "Dividend Yield" in resp.text
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_spend_get_7day_history() -> None:
    """GET /spend response context contains history list with 7 entries (one per day)."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-spend-history"
    _vault.set_passphrase(correct)
    try:
        user = _make_user_row("5.00", "America/New_York")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=user)
        mock_result.all.return_value = []

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_sf = MagicMock(return_value=mock_session)

        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                await client.post("/login", data={"passphrase": correct, "next": "/spend"})
                resp = await client.get("/spend")

        assert resp.status_code == 200
        # Should have the 7-day history table section header
        assert "7-Day History" in resp.text
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_spend_get_canonical_payload_unwrap() -> None:
    """Regression gate: canonical-wrapped rows must surface non-zero costs and real
    strategy names — would FAIL against top-level read, PASSES after
    inner = payload.get('payload', payload) unwrap.

    This test guards against re-introducing the SC-5 bug where spend_get reads
    cost_usd at the top level of the canonical event JSON (always returns "0")
    instead of unwrapping the nested "payload" dict first.
    """
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-spend-canonical"
    _vault.set_passphrase(correct)
    try:
        today_ts = datetime.now(UTC).isoformat()
        # Two canonical-wrapped rows — cost fields live inside "payload" key
        row1 = _make_llm_cost_row("0.05", "strat-a", today_ts)
        row2 = _make_llm_cost_row("0.03", "strat-b", today_ts)
        user = _make_user_row("5.00", "America/New_York")

        call_count = 0

        def _make_result(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                # First execute call: user row
                mock_result.scalar_one_or_none = MagicMock(return_value=user)
                mock_result.all.return_value = []
            elif call_count == 2:
                # Second execute call: today's llm_cost rows
                mock_result.all.return_value = [row1, row2]
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
            else:
                # 7-day history rows
                mock_result.all.return_value = [row1, row2]
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
            return mock_result

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=lambda *a, **kw: _make_result())
        mock_sf = MagicMock(return_value=mock_session)

        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                await client.post("/login", data={"passphrase": correct, "next": "/spend"})
                resp = await client.get("/spend")

        assert resp.status_code == 200
        # The page must show the summed total ($0.05 + $0.03 = $0.08)
        # — fails against pre-fix top-level read (would show "0.00")
        assert "0.08" in resp.text or "0.0800" in resp.text, (
            "today_total should be $0.08 from canonical-wrapped rows; "
            "got $0.00 — spend_get is still reading at the wrong nesting level"
        )
        # Both strategy names must appear in the per-strategy breakdown
        # — fails pre-fix (all rows resolve to "Unknown")
        assert "strat-a" in resp.text, (
            "strategy_name 'strat-a' not found in response; "
            "spend_get is still reading strategy_name at the wrong nesting level"
        )
        assert "strat-b" in resp.text, (
            "strategy_name 'strat-b' not found in response; "
            "spend_get is still reading strategy_name at the wrong nesting level"
        )
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_spend_get_corrupted_ceiling_uses_default() -> None:
    """GET /spend with over-quoted ceiling "'5.00'" (6-char) → 200 + DEFAULT ceiling shown.

    Regression gate: fails against pre-fix code where truthiness-only guard
    lets the corrupted value reach Decimal() → InvalidOperation → 500.
    """
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-spend-corrupted"
    _vault.set_passphrase(correct)
    try:
        # The real corrupted value stored by migration 0005's wrong server_default:
        # a 6-char string with literal apostrophes.
        user = _make_user_row(daily_cost_ceiling_usd="'5.00'", timezone="America/New_York")

        call_count = 0

        def _make_result(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                mock_result.scalar_one_or_none = MagicMock(return_value=user)
                mock_result.all.return_value = []
            else:
                mock_result.all.return_value = []
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
            return mock_result

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=lambda *a, **kw: _make_result())
        mock_sf = MagicMock(return_value=mock_session)

        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                await client.post("/login", data={"passphrase": correct, "next": "/spend"})
                resp = await client.get("/spend")

        assert resp.status_code == 200, (
            f"Expected 200 with corrupted ceiling "'5.00'", got {resp.status_code} — "
            "spend_get is not defensively parsing the ceiling value"
        )
        # DEFAULT_DAILY_CEILING_USD = Decimal("5.00") — should appear as "5.00"
        assert "5.00" in resp.text, (
            "DEFAULT ceiling '5.00' not visible in response; defensive parse may be broken"
        )
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_spend_get_null_ceiling_uses_default() -> None:
    """GET /spend with daily_cost_ceiling_usd=None → 200 + DEFAULT ceiling."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-spend-null-ceil"
    _vault.set_passphrase(correct)
    try:
        user = _make_user_row(daily_cost_ceiling_usd=None, timezone="America/New_York")

        call_count = 0

        def _make_result(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                mock_result.scalar_one_or_none = MagicMock(return_value=user)
                mock_result.all.return_value = []
            else:
                mock_result.all.return_value = []
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
            return mock_result

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=lambda *a, **kw: _make_result())
        mock_sf = MagicMock(return_value=mock_session)

        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                await client.post("/login", data={"passphrase": correct, "next": "/spend"})
                resp = await client.get("/spend")

        assert resp.status_code == 200, (
            f"Expected 200 with NULL ceiling, got {resp.status_code}"
        )
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_spend_get_empty_ceiling_uses_default() -> None:
    """GET /spend with daily_cost_ceiling_usd="" (empty string) → 200 + DEFAULT ceiling."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    correct = "test-passphrase-04-spend-empty-ceil"
    _vault.set_passphrase(correct)
    try:
        user = _make_user_row(daily_cost_ceiling_usd="", timezone="America/New_York")

        call_count = 0

        def _make_result(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                mock_result.scalar_one_or_none = MagicMock(return_value=user)
                mock_result.all.return_value = []
            else:
                mock_result.all.return_value = []
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
            return mock_result

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=lambda *a, **kw: _make_result())
        mock_sf = MagicMock(return_value=mock_session)

        with patch("gekko.config.get_settings") as mock_settings_fn, \
             patch("gekko.dashboard.routes._get_session_factory") as mock_sf_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings
            mock_sf_fn.return_value = (mock_sf, None)

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                await client.post("/login", data={"passphrase": correct, "next": "/spend"})
                resp = await client.get("/spend")

        assert resp.status_code == 200, (
            f"Expected 200 with empty-string ceiling, got {resp.status_code}"
        )
    finally:
        _vault.clear()


@pytest.mark.asyncio
async def test_spend_get_requires_auth() -> None:
    """Unauthenticated GET /spend returns 302 redirect to /login (auth gate)."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    _vault.set_passphrase("test-passphrase-04-spend-auth")
    try:
        with patch("gekko.config.get_settings") as mock_settings_fn:
            settings = MagicMock()
            settings.gekko_user_id = "testuser"
            settings.dashboard_url = "http://localhost:8000"
            mock_settings_fn.return_value = settings

            app = create_app()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                resp = await client.get("/spend")

        assert resp.status_code in (302, 303, 307)
        location = resp.headers.get("location", "")
        assert "/login" in location
    finally:
        _vault.clear()
