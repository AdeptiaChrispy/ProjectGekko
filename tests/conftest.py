"""Shared pytest fixtures — Plan 01-02 Task 3.

Per VALIDATION.md §"Wave 0 Requirements" item 3: this conftest provides the
fixtures every downstream Phase 1 plan depends on. Most are SCAFFOLD stubs in
Wave 0 (one-line MagicMock or path stubs) and get deepened by later plans:

| Fixture              | Wave 0 shape                          | Refined by   |
| -------------------- | ------------------------------------- | ------------ |
| temp_sqlcipher_db    | tmp_path / "test.db" path stub        | Plan 01-03   |
| sample_strategy      | dict matching D-01 minimal shape      | Plan 01-06   |
| frozen_time          | freezegun context @ 2026-06-08 15:00Z | (final here) |
| cassette_dir         | tests/fixtures/cassettes path         | (final here) |
| mock_alpaca_client   | bare MagicMock                        | Plan 01-05   |
| mock_slack_client    | bare MagicMock                        | Plan 01-08   |
| mock_claude_sdk      | bare MagicMock                        | Plan 01-07   |
| configured_logging   | calls configure_logging() + yield     | (final here) |
| clean_settings_env   | strips + sets minimal env, clears cache | (final here) |

All fixtures are function-scoped unless otherwise noted. None are `autouse=True`
— tests opt in by name (this prevents surprise side effects in unrelated tests).

References:
  * .planning/phases/01-foundation.../01-VALIDATION.md §Wave 0 Requirements
  * .planning/phases/01-foundation.../01-CONTEXT.md D-01 (Strategy fields)
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_sqlcipher_db(tmp_path: Path) -> Path:
    """Return a per-test SQLCipher DB path.

    **P1 Wave 0 stub.** Plan 01-03 replaces this with a real SQLCipher engine
    + migrated schema (alembic upgrade head). For now, callers receive a
    `Path` that does NOT point at a real DB file — they're expected to be
    in scaffolding tests that only need *a* path, not a working engine.
    """
    return tmp_path / "test.db"


# ---------------------------------------------------------------------------
# Domain stubs
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_strategy() -> dict[str, Any]:
    """Return a minimal Strategy-shaped dict (D-01 fields only).

    **P1 Wave 0 stub.** Plan 01-06 replaces with a real `gekko.schemas.Strategy`
    Pydantic instance. The dict shape mirrors the Pydantic model so tests
    written against this stub can switch with a one-line edit later.
    """
    return {
        "name": "ai-infra-bull",
        "thesis": (
            "Bullish on AI infrastructure providers (GPU compute, accelerators, "
            "networking); avoid Chinese names due to export controls."
        ),
        "watchlist": ["NVDA", "AMD", "AVGO"],
        "hard_caps": {
            "max_position_pct": 5.0,
            "max_daily_loss_usd": 250.0,
            "max_trades_per_day": 3,
            "max_sector_exposure_pct": 40.0,
        },
        "schedule_time": None,
    }


# ---------------------------------------------------------------------------
# Time control
# ---------------------------------------------------------------------------


@pytest.fixture
def frozen_time() -> Iterator[Any]:
    """Yield a freezegun context frozen at 2026-06-08T15:00:00+00:00.

    Tests asserting on timestamps, schedule windows, market-hours checks, etc.
    use this so behavior is deterministic across machines.
    """
    from freezegun import freeze_time

    with freeze_time("2026-06-08T15:00:00+00:00") as frozen:
        yield frozen


# ---------------------------------------------------------------------------
# Cassettes (HTTP replay for Alpaca/Finnhub/etc.)
# ---------------------------------------------------------------------------


@pytest.fixture
def cassette_dir() -> Path:
    """Directory holding recorded HTTP cassettes for integration tests.

    Plan 01-05 records the canonical Alpaca paper round-trip cassette here
    (with `GEKKO_TEST_LIVE_ALPACA=1`). Subsequent runs replay from this dir
    via respx by default; opt into live mode with the same env var.
    """
    return Path(__file__).parent / "fixtures" / "cassettes"


# ---------------------------------------------------------------------------
# External-service mocks
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_alpaca_client(mocker: Any) -> MagicMock:
    """Mocked Alpaca TradingClient.

    **P1 Wave 0 stub.** Plan 01-05 will refine to
    `mocker.MagicMock(spec=alpaca.trading.client.TradingClient)` once alpaca-py
    is imported by the broker module. For Wave 0 a bare MagicMock is enough
    for any test that just needs *a* client instance.
    """
    mock: MagicMock = mocker.MagicMock(name="MockAlpacaTradingClient")
    return mock


@pytest.fixture
def mock_slack_client(mocker: Any) -> MagicMock:
    """Mocked Slack WebClient.

    **P1 Wave 0 stub.** Plan 01-08 refines with `spec=slack_sdk.WebClient`.
    """
    mock: MagicMock = mocker.MagicMock(name="MockSlackWebClient")
    return mock


@pytest.fixture
def mock_claude_sdk(mocker: Any) -> MagicMock:
    """Mocked Claude Agent SDK client.

    **P1 Wave 0 stub.** Plan 01-07 refines with the actual SDK class spec
    once the agent runtime module imports `claude_agent_sdk`.
    """
    mock: MagicMock = mocker.MagicMock(name="MockClaudeAgentSDK")
    return mock


# ---------------------------------------------------------------------------
# Logging + Settings infrastructure
# ---------------------------------------------------------------------------


@pytest.fixture
def configured_logging() -> Iterator[None]:
    """Activate the production structlog chain (with credential redaction).

    Use this in any test that exercises log output — it guarantees the
    AUTH-04 redaction processor is in place. Not autouse: tests that don't
    log can skip it.
    """
    from gekko.logging_config import configure_logging

    configure_logging(level="DEBUG")
    yield
    # No explicit teardown — structlog keeps its config until the next call.


@pytest.fixture
def clean_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Strip the env, seed required vars to test sentinels, clear the cache.

    Any test that does `from gekko.config import get_settings` should depend on
    this fixture — otherwise leftover env from the dev shell (or a prior test)
    can mask a missing-required-var bug.

    Yields the same `monkeypatch` so a test can layer on its own overrides
    (e.g., `clean_settings_env.setenv("GEKKO_LOG_LEVEL", "DEBUG")`).
    """
    # Strip every env var Settings reads — start from a blank slate.
    for name in (
        "ANTHROPIC_API_KEY",
        "ALPACA_PAPER_API_KEY",
        "ALPACA_PAPER_SECRET_KEY",
        "SLACK_BOT_TOKEN",
        "SLACK_SIGNING_SECRET",
        "SLACK_USER_ID",
        "FINNHUB_API_KEY",
        "GEKKO_USER_ID",
        "GEKKO_LOG_LEVEL",
        "GEKKO_DATA_DIR",
        "GEKKO_USER_AGENT",
    ):
        monkeypatch.delenv(name, raising=False)

    # Seed minimal required env so `Settings()` constructs cleanly.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "test-alpaca-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test-alpaca-secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-bot")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-signing")
    monkeypatch.setenv("SLACK_USER_ID", "U_TEST_USER")
    monkeypatch.setenv("GEKKO_USER_ID", "test-user")

    # Reset the lru_cache so we read the seeded env, not a prior singleton.
    from gekko.config import get_settings

    get_settings.cache_clear()

    yield monkeypatch

    # Cleanup — clear again so the next test starts fresh.
    get_settings.cache_clear()
