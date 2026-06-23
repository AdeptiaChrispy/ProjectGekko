"""COST-03 settings route stubs — Phase 4 Wave 0.

Covers:
  - POST /settings with daily_cost_ceiling_usd="10.00" → user row updated;
    GET /settings returns "10.00"
  - Fresh user row → GET /settings shows "5.00" as default placeholder value

These tests extend the existing settings route (Plan 03-05) with Phase-4
ceiling config field. The ceiling field (daily_cost_ceiling_usd) is added
to the User model in migration 0005 (Wave 2) — tests stub with
NotImplementedError until the field and route extension ship.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app() -> object:
    """Create the FastAPI app with mocked settings (no real DB / Slack)."""
    from gekko.dashboard.app import create_app
    import gekko.vault.passphrase as _vault

    _vault.set_passphrase("test-passphrase-04-settings")

    with patch("gekko.config.get_settings") as mock_fn:
        settings = MagicMock()
        settings.gekko_user_id = "testuser"
        settings.dashboard_url = "http://localhost:8000"
        mock_fn.return_value = settings

        app = create_app()
    return app


def _clear_vault() -> None:
    import gekko.vault.passphrase as _vault
    _vault.clear()


# ---------------------------------------------------------------------------
# COST-03 stubs — ceiling field save + default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ceiling_saved() -> None:
    """POST /settings with daily_cost_ceiling_usd='10.00' → user row updated.

    After a successful POST, a subsequent GET /settings must render
    the saved value '10.00' in the form field.
    """
    raise NotImplementedError(
        "stub — implement after daily_cost_ceiling_usd column ships in "
        "migration 0005 and settings_post is extended in Wave 3"
    )


@pytest.mark.asyncio
async def test_ceiling_defaults_to_5() -> None:
    """Fresh user row (no daily_cost_ceiling_usd set) → GET /settings shows '5.00'.

    The settings form renders '5.00' as the placeholder / default value
    when the column is NULL (per D-02: DEFAULT_DAILY_CEILING_USD = Decimal('5.00')).
    """
    raise NotImplementedError(
        "stub — implement after migration 0005 + settings_get extension ship in Wave 3"
    )
