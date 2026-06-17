"""Live credential vault round-trip — Plan 02-06 Task 1 (BROK-A-02 / D-34).

Tests :func:`gekko.vault.credentials.store_live_credentials` +
:func:`gekko.vault.credentials.load_live_credentials` against a real
SQLCipher engine — confirms the credential row writes + the load returns
the same bytes. Phase-1 D-19 SQLCipher whole-DB encryption is the at-rest
defense; the blob columns store PLAINTEXT bytes-for-bytes.

Also covers the EXEC-05 D-34 extension: ``check_paper_live_pairing`` with
the ``credential_kind`` kwarg rejects a paper credential paired with a
live strategy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from gekko.db.models import BrokerCredential, User
from gekko.db.session import make_session_factory
from gekko.vault import credentials as creds_mod


@pytest_asyncio.fixture
async def seeded_user_engine(temp_sqlcipher_db: Any) -> Any:
    """Seed a User row so credentials FKs are satisfied."""
    sf = make_session_factory(temp_sqlcipher_db)
    async with sf() as session, session.begin():
        session.add(
            User(
                user_id="test-user",
                created_at=datetime.now(UTC).isoformat(),
                kill_active=False,
            )
        )
    return temp_sqlcipher_db


def _patch_session_factory(
    monkeypatch: pytest.MonkeyPatch, sf: Any
) -> None:
    """Wire creds_mod._get_session_factory to a test session factory."""
    monkeypatch.setattr(
        creds_mod, "_get_session_factory", lambda _u: (sf, None)
    )


# ---------------------------------------------------------------------------
# store_live_credentials + load_live_credentials round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_then_load_returns_plaintext_round_trip(
    seeded_user_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(seeded_user_engine)
    _patch_session_factory(monkeypatch, sf)

    await creds_mod.store_live_credentials(
        user_id="test-user",
        api_key="AKLIVE_test_key_12345",
        secret_key="seclive_test_secret_67890",
    )
    result = await creds_mod.load_live_credentials("test-user")
    assert result == (
        "AKLIVE_test_key_12345",
        "seclive_test_secret_67890",
    )


@pytest.mark.asyncio
async def test_load_returns_none_when_no_live_row(
    seeded_user_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(seeded_user_engine)
    _patch_session_factory(monkeypatch, sf)

    result = await creds_mod.load_live_credentials("test-user")
    assert result is None


@pytest.mark.asyncio
async def test_store_writes_correct_row_shape(
    seeded_user_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(seeded_user_engine)
    _patch_session_factory(monkeypatch, sf)

    await creds_mod.store_live_credentials(
        user_id="test-user",
        api_key="AKLIVE",
        secret_key="seclive",
    )
    async with sf() as session:
        rows = list(
            (
                await session.execute(
                    select(BrokerCredential).where(
                        BrokerCredential.user_id == "test-user"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.broker == "alpaca"
    assert row.kind == "alpaca_live"
    assert row.key_blob == "AKLIVE"
    assert row.secret_blob == "seclive"
    assert row.paper is False


@pytest.mark.asyncio
async def test_repr_does_not_leak_key_blob_or_secret_blob(
    seeded_user_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(seeded_user_engine)
    _patch_session_factory(monkeypatch, sf)

    await creds_mod.store_live_credentials(
        user_id="test-user",
        api_key="LEAK_TEST_KEY_DO_NOT_LOG_ME",
        secret_key="LEAK_TEST_SECRET_DO_NOT_LOG_ME",
    )
    async with sf() as session:
        row = (
            await session.execute(
                select(BrokerCredential).where(
                    BrokerCredential.user_id == "test-user"
                )
            )
        ).scalar_one()
    rendered = repr(row)
    assert "LEAK_TEST_KEY_DO_NOT_LOG_ME" not in rendered
    assert "LEAK_TEST_SECRET_DO_NOT_LOG_ME" not in rendered
    # kind IS in repr (non-sensitive discriminator per plan 02-01).
    assert "alpaca_live" in rendered


@pytest.mark.asyncio
async def test_paper_and_live_credentials_coexist(
    seeded_user_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The composite PK (user_id, broker, kind) allows BOTH paper + live rows."""
    sf = make_session_factory(seeded_user_engine)
    _patch_session_factory(monkeypatch, sf)

    # Seed a paper credential the way the rest of the system does it
    # (Phase-1 doesn't have a dedicated helper for this — direct ORM
    # insert through the same session).
    async with sf() as session, session.begin():
        session.add(
            BrokerCredential(
                user_id="test-user",
                broker="alpaca",
                kind="alpaca_paper",
                key_blob="paper_key",
                secret_blob="paper_secret",
                paper=True,
                created_at=datetime.now(UTC).isoformat(),
            )
        )

    await creds_mod.store_live_credentials(
        user_id="test-user",
        api_key="live_key",
        secret_key="live_secret",
    )

    async with sf() as session:
        rows = list(
            (
                await session.execute(
                    select(BrokerCredential).where(
                        BrokerCredential.user_id == "test-user"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert {r.kind for r in rows} == {"alpaca_paper", "alpaca_live"}


@pytest.mark.asyncio
async def test_duplicate_live_credential_insert_raises_integrity_error(
    seeded_user_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = make_session_factory(seeded_user_engine)
    _patch_session_factory(monkeypatch, sf)

    await creds_mod.store_live_credentials(
        user_id="test-user", api_key="k1", secret_key="s1"
    )
    with pytest.raises(IntegrityError):
        await creds_mod.store_live_credentials(
            user_id="test-user", api_key="k2", secret_key="s2"
        )


@pytest.mark.asyncio
async def test_store_emits_credentials_added_audit_event(
    seeded_user_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit event with context='credentials.added' lands in the chain."""
    from gekko.db.models import Event as EventRow

    sf = make_session_factory(seeded_user_engine)
    _patch_session_factory(monkeypatch, sf)

    await creds_mod.store_live_credentials(
        user_id="test-user", api_key="k1", secret_key="s1"
    )
    async with sf() as session:
        events = list(
            (
                await session.execute(
                    select(EventRow).where(EventRow.user_id == "test-user")
                )
            )
            .scalars()
            .all()
        )
    assert len(events) >= 1
    creds_events = [
        e for e in events if '"credentials.added"' in e.payload_json
    ]
    assert len(creds_events) == 1
    # Confirm the audit payload does NOT contain the key value.
    payload = creds_events[0].payload_json
    assert "k1" not in payload
    assert "s1" not in payload


# ---------------------------------------------------------------------------
# check_paper_live_pairing — credential_kind fourth axis (EXEC-05 / D-34)
# ---------------------------------------------------------------------------


def test_check_paper_live_pairing_credential_kind_match_passes() -> None:
    """live strategy + live credential = no rejection."""
    from gekko.execution.checks._paper_live import check_paper_live_pairing
    from unittest.mock import MagicMock

    broker = MagicMock()
    broker.is_paper = False
    # Should not raise.
    check_paper_live_pairing(
        broker=broker,
        strategy_mode="live",
        account_mode="LIVE",
        user_id="test-user",
        credential_kind="alpaca_live",
    )


def test_check_paper_live_pairing_credential_kind_mismatch_rejects() -> None:
    """live strategy + paper credential = paper_live_mismatch_credential."""
    from gekko.core.errors import OrderGuardRejected
    from gekko.execution.checks._paper_live import check_paper_live_pairing
    from unittest.mock import MagicMock

    broker = MagicMock()
    broker.is_paper = False
    with pytest.raises(OrderGuardRejected) as exc_info:
        check_paper_live_pairing(
            broker=broker,
            strategy_mode="live",
            account_mode="LIVE",
            user_id="test-user",
            credential_kind="alpaca_paper",
        )
    assert exc_info.value.reject_code == "paper_live_mismatch_credential"


def test_check_paper_live_pairing_no_credential_kind_kwarg_skips_check() -> None:
    """When ``credential_kind=None`` the function behaves as Phase 02-02."""
    from gekko.execution.checks._paper_live import check_paper_live_pairing
    from unittest.mock import MagicMock

    broker = MagicMock()
    broker.is_paper = True
    # Should not raise — credential_kind is None so the fourth axis check
    # is skipped. The first 3 axes (mode/account/broker) all align.
    check_paper_live_pairing(
        broker=broker,
        strategy_mode="paper",
        account_mode="PAPER",
        user_id="test-user",
    )
