"""Plan 01-02 Task 2 — `gekko.config.Settings` Pydantic Settings tests.

Per VALIDATION.md row `01-02-T2`: Pydantic Settings must (a) raise ValidationError
when a required env-var is missing, (b) never leak secret values via repr, and
(c) provide per-user DB path/URL helpers honoring A13 (`~/.gekko/<user_id>.db`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from pydantic import SecretStr, ValidationError

# Required env-vars per PLAN.md Task 2 <action>
_REQUIRED_ENV: dict[str, str] = {
    "ANTHROPIC_API_KEY": "secret-anthropic-XYZ",
    "ALPACA_PAPER_API_KEY": "secret-alpaca-key-XYZ",
    "ALPACA_PAPER_SECRET_KEY": "secret-alpaca-secret-XYZ",
    "SLACK_BOT_TOKEN": "xoxb-secret-bot-XYZ",
    "SLACK_SIGNING_SECRET": "secret-signing-XYZ",
    "SLACK_USER_ID": "U12345TEST",
    "GEKKO_USER_ID": "alice",
}

# Names of env-vars to fully clear so leftover dev-shell values do not leak in.
# Includes the cached singleton breakers used by Settings.
_NUKE_ENV: tuple[str, ...] = (
    *_REQUIRED_ENV.keys(),
    "FINNHUB_API_KEY",
    "GEKKO_LOG_LEVEL",
    "GEKKO_DATA_DIR",
    "GEKKO_USER_AGENT",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Strip every env-var the Settings class reads, then yield the monkeypatch.

    Also clears the get_settings() lru_cache so each test sees a fresh Settings.
    """
    for name in _NUKE_ENV:
        monkeypatch.delenv(name, raising=False)

    # Ensure pydantic-settings doesn't pick up a stray .env in the repo root.
    # We do this by overriding the model_config dynamically isn't possible without
    # imports; instead we rely on chdir to a tmp dir if needed. For Phase 1
    # there's no .env tracked in the repo, so deleting env-vars suffices.

    from gekko.config import get_settings

    get_settings.cache_clear()

    yield monkeypatch

    get_settings.cache_clear()


@pytest.fixture
def full_env(clean_env: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """clean_env + every required key set to a sentinel value."""
    for name, value in _REQUIRED_ENV.items():
        clean_env.setenv(name, value)
    return clean_env


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_settings_constructs_with_all_required_envs(full_env: pytest.MonkeyPatch) -> None:
    """Happy path: every required env present, Settings constructs cleanly."""
    from gekko.config import Settings

    s = Settings()
    assert isinstance(s.anthropic_api_key, SecretStr)
    assert s.anthropic_api_key.get_secret_value() == "secret-anthropic-XYZ"
    assert s.gekko_user_id == "alice"


def test_missing_anthropic_key_raises_validation_error(clean_env: pytest.MonkeyPatch) -> None:
    """Without ANTHROPIC_API_KEY, construction raises ValidationError."""
    # Set everything EXCEPT ANTHROPIC_API_KEY
    for name, value in _REQUIRED_ENV.items():
        if name == "ANTHROPIC_API_KEY":
            continue
        clean_env.setenv(name, value)

    from gekko.config import Settings

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    # The validation error should reference the missing field.
    errors_text = str(exc_info.value).lower()
    assert "anthropic_api_key" in errors_text


def test_db_path_for_returns_expanded_home(full_env: pytest.MonkeyPatch) -> None:
    """`db_path_for('alice')` returns `~/.gekko/alice.db` expanded (per A13)."""
    from gekko.config import Settings

    s = Settings()
    db_path = s.db_path_for("alice")
    expected = (Path.home() / ".gekko" / "alice.db").resolve()
    assert db_path.resolve() == expected


def test_db_url_for_references_sqlcipher_dialect(full_env: pytest.MonkeyPatch) -> None:
    """`db_url_for('alice')` returns a SQLAlchemy URL referencing the SQLCipher dialect."""
    from gekko.config import Settings

    s = Settings()
    url = s.db_url_for("alice")
    assert isinstance(url, str)
    assert "sqlcipher" in url.lower() or "pysqlcipher" in url.lower()
    # The expanded user db filename should appear in the URL string.
    assert "alice.db" in url


def test_get_settings_returns_lru_cached_singleton(full_env: pytest.MonkeyPatch) -> None:
    """`get_settings()` returns the same instance across calls."""
    from gekko.config import get_settings

    a = get_settings()
    b = get_settings()
    assert a is b, "get_settings() must be an lru_cache'd singleton"


def test_repr_does_not_leak_secret_values(full_env: pytest.MonkeyPatch) -> None:
    """`repr(settings)` must NOT include any actual secret string."""
    from gekko.config import Settings

    s = Settings()
    text = repr(s)
    for name, value in _REQUIRED_ENV.items():
        if name in ("SLACK_USER_ID", "GEKKO_USER_ID"):
            # These are NOT secrets — they're identifiers — and may appear.
            continue
        assert value not in text, (
            f"AUTH-04 violation via repr(): {name}'s secret value {value!r} "
            f"leaked into repr(settings) output"
        )


def test_gekko_log_level_env_override(full_env: pytest.MonkeyPatch) -> None:
    """`GEKKO_LOG_LEVEL` env override is honored; default is INFO."""
    from gekko.config import Settings

    s = Settings()
    assert s.gekko_log_level == "INFO"  # default

    full_env.setenv("GEKKO_LOG_LEVEL", "DEBUG")
    s2 = Settings()
    assert s2.gekko_log_level == "DEBUG"


def test_gekko_user_id_is_active_user_identity(full_env: pytest.MonkeyPatch) -> None:
    """`GEKKO_USER_ID` env-var becomes the active user (single-user P1, D-21)."""
    from gekko.config import Settings

    full_env.setenv("GEKKO_USER_ID", "bob")
    s = Settings()
    assert s.gekko_user_id == "bob"
    # And it threads into db_path_for when called with its own value:
    assert s.db_path_for(s.gekko_user_id).name == "bob.db"
