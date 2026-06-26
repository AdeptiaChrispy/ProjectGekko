"""Shared pytest fixtures — Plan 01-02 Task 3 (deepened by Plan 01-03 Task 3).

Per VALIDATION.md §"Wave 0 Requirements" item 3: this conftest provides the
fixtures every downstream Phase 1 plan depends on. Most are SCAFFOLD stubs in
Wave 0 (one-line MagicMock or path stubs) and get deepened by later plans:

| Fixture              | Wave 0 shape                          | Refined by   |
| -------------------- | ------------------------------------- | ------------ |
| temp_sqlcipher_db    | real AsyncEngine + Base.metadata schema | Plan 01-03 ✓ |
| migrated_sqlcipher_db | tuple[AsyncEngine, Path] via alembic | Plan 01-03 ✓ |
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

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Database — Plan 01-03 Task 3 refinement
# ---------------------------------------------------------------------------

#: Test passphrase used by ``temp_sqlcipher_db`` and ``migrated_sqlcipher_db``.
#: Tests can override via ``monkeypatch.setenv("GEKKO_DB_PASSPHRASE", ...)``
#: before invoking the migrated fixture.
_TEST_PASSPHRASE = "test-passphrase"  # nosec: test-only literal


@pytest_asyncio.fixture
async def temp_sqlcipher_db(tmp_path: Path) -> AsyncIterator[Any]:
    """Yield a SQLCipher-encrypted ``AsyncEngine`` with the 6 P1 tables.

    Uses ``Base.metadata.create_all`` (NOT Alembic) because unit tests want
    fast setup. Tests that need real Alembic migration history use
    ``migrated_sqlcipher_db`` instead.
    """
    from gekko.db.engine import get_async_engine
    from gekko.db.models import Base

    engine = get_async_engine(tmp_path / "test.db", _TEST_PASSPHRASE)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def migrated_sqlcipher_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Any, Path]]:
    """Yield ``(engine, db_path)`` after running ``alembic upgrade head``.

    Used by integration tests that need real Alembic migration history (vs.
    ``Base.metadata.create_all`` which bypasses Alembic). The migration is
    run via subprocess so it sees the same env (including
    ``GEKKO_DB_PASSPHRASE`` and the per-user DB location) the operator
    would.
    """
    import subprocess
    import sys

    from gekko.db.engine import get_async_engine

    db_dir = tmp_path / "gekko-data"
    db_dir.mkdir(parents=True, exist_ok=True)
    user = "test-user"
    db_path = db_dir / f"{user}.db"

    env_overrides = {
        "GEKKO_DB_PASSPHRASE": _TEST_PASSPHRASE,
        "GEKKO_USER_ID": user,
        "GEKKO_DATA_DIR": str(db_dir),
        # Minimal Settings env so get_settings() constructs cleanly.
        "ANTHROPIC_API_KEY": "test-anthropic",
        "ALPACA_PAPER_API_KEY": "test-alpaca-key",
        "ALPACA_PAPER_SECRET_KEY": "test-alpaca-secret",
        "SLACK_BOT_TOKEN": "xoxb-test-bot",
        "SLACK_SIGNING_SECRET": "test-signing",
        "SLACK_USER_ID": "U_TEST_USER",
    }
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)

    # Run alembic upgrade head in a subprocess — emulates `gekko init` flow.
    result = subprocess.run(  # nosec
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**__import__("os").environ},
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed (exit {result.returncode}):\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    engine = get_async_engine(db_path, _TEST_PASSPHRASE)
    try:
        yield engine, db_path
    finally:
        await engine.dispose()


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
# Configurable Claude Agent SDK query() mock — Plan 01-07 Task 6
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_sdk_query(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Patch ``gekko.agent.runtime.query`` with a configurable fake.

    The Claude Agent SDK's ``query()`` is an async generator that yields a
    stream of messages. For integration tests we need to:

      * Replace it entirely so the `claude` CLI binary isn't required
        (docs/sdk-shape.md delta #8 — CI mocks the SDK).
      * Return DIFFERENT canned message streams for the Researcher call
        and the Decision call (different system_prompts identify them).

    The fixture returns a ``set_responses(researcher=..., decision=...)``
    callable plus a ``calls`` list capturing every invocation's
    ``(prompt, options_signature)`` for assertion. The runtime is
    expected to call ``query()`` exactly twice per ``trigger_strategy_run``
    (Researcher then Decision) plus once for ``compile_strategy_from_chat``.
    """
    from claude_agent_sdk.types import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
    )

    calls: list[dict[str, Any]] = []
    responses: dict[str, list[Any]] = {}

    def make_text_message(text: str) -> Any:
        return AssistantMessage(
            content=[TextBlock(text=text)],
            model="sonnet",
            parent_tool_use_id=None,
            error=None,
            usage=None,
        )

    def make_tool_use_message(tool_name: str, tool_input: dict[str, Any]) -> Any:
        return AssistantMessage(
            content=[
                ToolUseBlock(
                    id="t1",
                    name=tool_name,
                    input=tool_input,
                )
            ],
            model="sonnet",
            parent_tool_use_id=None,
            error=None,
            usage=None,
        )

    def make_result_message() -> Any:
        return ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="test-session",
            stop_reason="end_turn",
            total_cost_usd=0.0,
            usage=None,
            result=None,
            structured_output=None,
            model_usage=None,
            permission_denials=None,
            deferred_tool_use=None,
            errors=None,
            api_error_status=None,
            uuid=None,
        )

    def set_responses(**kwargs: list[Any]) -> None:
        responses.clear()
        responses.update(kwargs)

    async def fake_query(
        *, prompt: Any, options: Any = None, transport: Any = None
    ) -> Any:
        """Async generator producing the configured response stream.

        Selects which response stream to yield based on a marker in the
        ``options.system_prompt`` (Researcher prompts contain
        "Researcher subagent"; Decision prompts contain "Decision subagent";
        Compiler prompts contain "Strategy Compiler").
        """
        sys_prompt = getattr(options, "system_prompt", "") or ""
        key: str
        if "Researcher subagent" in sys_prompt:
            key = "researcher"
        elif "Decision subagent" in sys_prompt:
            key = "decision"
        elif "Strategy Compiler" in sys_prompt:
            key = "compiler"
        else:
            key = "default"

        calls.append(
            {
                "key": key,
                "prompt": prompt,
                "system_prompt": sys_prompt,
                "allowed_tools": list(getattr(options, "allowed_tools", []) or []),
                "max_turns": getattr(options, "max_turns", None),
                "model": getattr(options, "model", None),
            }
        )

        stream = list(responses.get(key, []))
        # Always cap each stream with a ResultMessage so the runtime can
        # break out cleanly.
        if not stream or not isinstance(stream[-1], ResultMessage):
            stream.append(make_result_message())
        for msg in stream:
            yield msg

    # Patch the symbol the runtime module imports.
    monkeypatch.setattr(
        "gekko.agent.runtime.query", fake_query, raising=False
    )

    # Expose helpers on a small namespace.
    ns = type("FakeSdkQueryNS", (), {})()
    ns.calls = calls
    ns.set_responses = set_responses
    ns.make_text_message = make_text_message
    ns.make_tool_use_message = make_tool_use_message
    ns.make_result_message = make_result_message
    return ns


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


# ---------------------------------------------------------------------------
# Phase 2 fixtures — Plan 02-01 Task 2 (VALIDATION.md §Wave 0 Requirements)
#
# Mirrors the Phase-1 `_get_session_factory` test seam style (PATTERNS §5a).
# All three are function-scoped and opt-in (no autouse).
# ---------------------------------------------------------------------------


@pytest.fixture(params=["PAPER", "LIVE"])
def account_mode(request: pytest.FixtureRequest) -> str:
    """Parametrized fixture yielding both account modes.

    Any test that depends on this fixture is automatically duplicated against
    PAPER and LIVE. Used by Plan 02-02's paper/live pairing tests + the
    Phase-2 walking-skeleton end-to-end test.

    The string values are LOCKED — they match the `account_mode` Literal on
    TradeProposal (plan 02-01 Task 3) and the CHECK constraint on
    `proposals.account_mode` (plan 02-01 Task 4).
    """
    return str(request.param)


@pytest_asyncio.fixture
async def kill_state(
    temp_sqlcipher_db: Any,
) -> AsyncIterator[Any]:
    """Yield an AsyncSession + assert users.kill_active=False around the test.

    Plan 02-05 (kill-switch) uses this fixture: tests open a session, flip
    `users.kill_active=True` to simulate the kill flow, run their assertions,
    and rely on the fixture's teardown to reset the flag to False so test
    isolation is preserved.

    Wave-0 SCAFFOLD: yields the raw engine — plan 02-05 deepens to a full
    session + seeded user row when the kill-switch state machine lands.
    """
    # NB: Wave 0 — no kill_active column exists on `users` yet (plan 02-01
    # Task 4 adds it). The fixture yields the engine so the kill-switch
    # plan can extend with the column-aware setup once the migration lands.
    yield temp_sqlcipher_db


@pytest_asyncio.fixture
async def live_credential_pair(
    temp_sqlcipher_db: Any,
) -> AsyncIterator[Any]:
    """Seed a `(user_id, alpaca_paper)` + `(user_id, alpaca_live)` row pair.

    Plan 02-06 (live credential vault) uses this fixture to exercise the
    credential loader: it MUST select the `kind='alpaca_live'` row when
    constructing a live AlpacaBroker, and the `kind='alpaca_paper'` row when
    constructing a paper AlpacaBroker.

    Wave-0 SCAFFOLD: yields the raw engine — plan 02-06 deepens to actually
    seed the BrokerCredential rows (kind column added by plan 02-01 Task 4).
    """
    yield temp_sqlcipher_db


# ---------------------------------------------------------------------------
# Settings infrastructure
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Phase 3 fixtures — Plan 03-01 Task 1 (VALIDATION.md §Wave 0 Requirements)
#
# Three new fixtures for the P3 schema substrate:
#   - quiet_hours_user: User row with quiet_hours_* + timezone columns (D-47/D-49)
#   - expired_proposal: Proposal with status=PENDING + expires_at in the past (D-50/D-61)
#   - dedup_row_factory: callable yielding SlackActionDedup rows (D-45)
#
# All are function-scoped and opt-in (no autouse).
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def quiet_hours_user(
    temp_sqlcipher_db: Any,
) -> AsyncIterator[Any]:
    """Seed a User row with quiet_hours_start=time(22,0), quiet_hours_end=time(7,0),
    timezone='America/New_York' (D-47/D-49 default).

    The new P3 columns (quiet_hours_start, quiet_hours_end, timezone) are added
    by Alembic 0004 (Plan 03-01 Task 2). Tests that exercise the quiet-hours
    predicate use this fixture to seed the required user state.

    Yields the AsyncEngine (same as temp_sqlcipher_db) after inserting the user row.
    The caller reads the row via session.get(User, user_id).
    """
    from datetime import time

    from sqlalchemy.ext.asyncio import AsyncSession

    from gekko.db.models import User

    _user_id = "test-quiet-user"
    async with AsyncSession(temp_sqlcipher_db) as session, session.begin():
        user = User(
            user_id=_user_id,
            created_at="2026-06-17T00:00:00+00:00",
            agreement_acknowledged_at="2026-06-17T00:00:00+00:00",
            quiet_hours_start=time(22, 0).strftime("%H:%M:%S"),
            quiet_hours_end=time(7, 0).strftime("%H:%M:%S"),
            timezone="America/New_York",
        )
        session.add(user)
    yield temp_sqlcipher_db


@pytest_asyncio.fixture
async def expired_proposal(
    temp_sqlcipher_db: Any,
) -> AsyncIterator[Any]:
    """Seed a Proposal row with status=PENDING and expires_at in the past.

    The ``slack_message_ts`` and ``slack_message_channel`` columns (added by
    Plan 03-01 Task 2 Alembic 0004) are pre-populated with realistic values
    so tests that mock chat.update have identifiers to match against.

    Yields the AsyncEngine after inserting the required user + strategy + proposal rows.
    The caller reads the proposal row via session.get(Proposal, proposal_id).
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.ext.asyncio import AsyncSession

    from gekko.db.models import Proposal, Strategy, User

    _user_id = "test-expired-user"
    _strategy_id = "strat-expired-001"
    _proposal_id = "prop-expired-001"
    _now = datetime.now(UTC)
    _past = (_now - timedelta(minutes=1)).isoformat()
    _created = _now.isoformat()

    async with AsyncSession(temp_sqlcipher_db) as session, session.begin():
        user = User(
            user_id=_user_id,
            created_at=_created,
            agreement_acknowledged_at=_created,
        )
        session.add(user)
        await session.flush()

        strategy = Strategy(
            strategy_id=_strategy_id,
            user_id=_user_id,
            strategy_name="test-strategy",
            version=1,
            payload_json="{}",
            created_at=_created,
        )
        session.add(strategy)
        await session.flush()

        proposal = Proposal(
            proposal_id=_proposal_id,
            user_id=_user_id,
            strategy_id=_strategy_id,
            status="PENDING",
            payload_json="{}",
            client_order_id=None,
            broker_order_id=None,
            created_at=_created,
            updated_at=_created,
            account_mode="PAPER",
            expires_at=_past,
            slack_message_ts="1234567890.000100",
            slack_message_channel="D1234567890",
        )
        session.add(proposal)
    yield temp_sqlcipher_db


# ---------------------------------------------------------------------------
# Phase 5 fixtures — Plan 05-01 Task 1 (VALIDATION.md §Per-Task Verification Map)
#
# Two seed helpers the clean-streak tests (Plan 02) depend on. They write
# ENRICHED audit events (carrying strategy_name + account_mode per D-T01/D-T02)
# so the streak scanner can partition approvals by (strategy_name, account_mode)
# and zero the streak on a cap_rejection (RESEARCH Pattern 4 / Pitfall 1).
#
# Both are function-scoped and opt-in (no autouse). They return async callables
# the test awaits inside its own session/transaction.
# ---------------------------------------------------------------------------


@pytest.fixture
def seed_approval_events() -> Any:
    """Return an async callable seeding N enriched ``approval`` events.

    Usage::

        async def test_streak(temp_sqlcipher_db, seed_approval_events):
            async with AsyncSession(temp_sqlcipher_db) as s, s.begin():
                await seed_approval_events(
                    s, user_id="u1", strategy_name="alpha",
                    account_mode="PAPER", n=10,
                )

    Each event carries the Plan-05-01 enriched ``approval`` payload shape:
    ``{proposal_id, actor, slack_action_id, strategy_name, account_mode}``.
    The streak scanner (Plan 02 ``compute_clean_streak``) reads
    ``strategy_name`` + ``account_mode`` from the inner payload to attribute
    each approval to a (strategy, mode) partition.
    """
    from gekko.audit.log import append_event

    async def _seed(
        session: Any,
        *,
        user_id: str,
        strategy_name: str,
        account_mode: str,
        n: int,
        strategy_id: str | None = None,
    ) -> None:
        for i in range(n):
            await append_event(
                session,
                user_id=user_id,
                strategy_id=strategy_id,
                event_type="approval",
                payload={
                    "proposal_id": f"prop-{strategy_name}-{account_mode}-{i}",
                    "actor": "test-actor",
                    "slack_action_id": "approve_proposal",
                    "strategy_name": strategy_name,
                    "account_mode": account_mode,
                },
            )

    return _seed


@pytest.fixture
def seed_cap_rejection() -> Any:
    """Return an async callable seeding one enriched ``cap_rejection`` event.

    A cap_rejection mid-window zeroes the clean streak (D-T02). The enriched
    payload carries ``strategy_name`` so the scanner attributes the reset to
    the right (strategy, mode) partition.

    Usage::

        await seed_cap_rejection(
            s, user_id="u1", strategy_name="alpha",
            reject_code="hard_cap_position_pct",
        )
    """
    from gekko.audit.log import append_event

    async def _seed(
        session: Any,
        *,
        user_id: str,
        strategy_name: str,
        reject_code: str = "hard_cap_position_pct",
        strategy_id: str | None = None,
        ticker: str = "NVDA",
        proposal_id: str = "prop-cap-001",
    ) -> None:
        await append_event(
            session,
            user_id=user_id,
            strategy_id=strategy_id,
            event_type="cap_rejection",
            payload={
                "reject_code": reject_code,
                "reject_reason": "seeded cap rejection",
                "ticker": ticker,
                "proposal_id": proposal_id,
                "strategy_name": strategy_name,
            },
        )

    return _seed


@pytest.fixture
def dedup_row_factory() -> Any:
    """Return a callable that builds SlackActionDedup row kwargs.

    The factory accepts kwargs matching the SlackActionDedup columns declared
    in Plan 03-01 Task 2 (Alembic 0004):

        proposal_id, action_id, actor_slack_user_id, actor_gekko_user_id,
        source, slack_trigger_id

    Usage::

        row_kwargs = dedup_row_factory(
            proposal_id="prop-001",
            action_id="approve_proposal",
            actor_gekko_user_id="chris",
            source="slack",
        )
        # Pass to session.add(SlackActionDedup(**row_kwargs, inserted_at=..., result=...))

    Note: ``inserted_at`` and ``result`` are not supplied by the factory —
    the caller fills them (as they are determined by the insert outcome, not
    the caller's intent).
    """
    from datetime import UTC, datetime

    def _factory(
        proposal_id: str = "prop-001",
        action_id: str = "approve_proposal",
        actor_slack_user_id: str | None = None,
        actor_gekko_user_id: str = "chris",
        source: str = "slack",
        slack_trigger_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "proposal_id": proposal_id,
            "action_id": action_id,
            "actor_slack_user_id": actor_slack_user_id,
            "actor_gekko_user_id": actor_gekko_user_id,
            "source": source,
            "slack_trigger_id": slack_trigger_id,
            "inserted_at": datetime.now(UTC).isoformat(),
            "result": "first_write",
        }

    return _factory
