"""Centralized Pydantic Settings — Plan 01-02 Task 2.

Single source of truth for every env-var Project Gekko reads. No other module
should call `os.environ.get(...)` directly — they import `get_settings()` from
here and read typed fields.

Design notes:
  * Every secret-shaped field is `SecretStr` so `repr(settings)` shows
    `**********` instead of the raw value (defends AUTH-04 against accidental
    f-string / repr leaks).
  * Construction raises `pydantic.ValidationError` when a required env-var is
    missing — `gekko doctor` (Plan 01-01) is the authoritative env-audit
    surface that translates this into a friendly user message.
  * `get_settings()` is `lru_cache`'d so the env is read once per process.
    Tests clear the cache via `get_settings.cache_clear()`.
  * `db_path_for(user_id)` / `db_url_for(user_id)` honor A13: each user's
    SQLCipher DB lives at `~/.gekko/<user_id>.db`. The URL is a SCAFFOLD
    string — Plan 01-03 refines the exact SQLCipher driver URL.

References:
  * CONTEXT.md D-19 (passphrase-on-start), D-21 (per-user isolated deployment)
  * RESEARCH.md §"Standard Stack — env vars expected by the agent runtime"
  * SKELETON.md §"Demo Script" (what env vars `gekko init` collects)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    """Default GEKKO_DATA_DIR — per-user `.gekko` under the OS home directory (A13)."""
    return Path.home() / ".gekko"


class Settings(BaseSettings):
    """Process-wide configuration read from env vars (and optionally a `.env` file).

    Every secret field is a `SecretStr` so accidental string formatting cannot
    leak the value. Use `.get_secret_value()` only at the moment the secret is
    handed to an SDK client.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Anthropic ----------------------------------------------------------
    anthropic_api_key: SecretStr = Field(
        description="Claude Agent SDK API key. Required.",
    )

    # ---- Alpaca paper-trading -----------------------------------------------
    # Phase 1 only ever talks to the paper endpoint. Live keys are rejected by
    # the AlpacaBroker constructor (Plan 01-05) — this Settings class is
    # intentionally agnostic about that since both shapes live in env vars.
    alpaca_paper_api_key: SecretStr = Field(
        description="Alpaca paper-trading API key (PK...). Required for P1.",
    )
    alpaca_paper_secret_key: SecretStr = Field(
        description="Alpaca paper-trading secret. Required for P1.",
    )

    # ---- Slack --------------------------------------------------------------
    slack_bot_token: SecretStr = Field(
        description="Slack bot OAuth token (xoxb-...). Required for HITL DMs.",
    )
    slack_signing_secret: SecretStr = Field(
        description="Slack signing secret used to verify request signatures.",
    )
    slack_user_id: str = Field(
        description="Slack user ID (e.g., U12345ABC) that receives HITL DMs. Required.",
    )
    slack_app_token: SecretStr | None = Field(
        default=None,
        description=(
            "Optional Slack App-Level Token (xapp-...) used to enable Socket "
            "Mode. When set, the dashboard's lifespan starts an "
            "AsyncSocketModeHandler — bot interactivity flows over an outbound "
            "WebSocket and NO public Request URL / tunnel is required. "
            "Needs the `connections:write` app-level scope. Leave unset to "
            "use the HTTP `POST /slack/events` adapter (requires a tunnel)."
        ),
    )

    # ---- Optional data providers (graceful-degrade per RES-02) --------------
    finnhub_api_key: SecretStr | None = Field(
        default=None,
        description="Finnhub free-tier API key for news/sentiment evidence (optional).",
    )

    # ---- Gekko runtime ------------------------------------------------------
    gekko_user_id: str = Field(
        description=(
            "Active user identity for this Gekko process (D-21: per-user isolated "
            "deployment; matches Slack user). Required."
        ),
    )
    gekko_log_level: str = Field(
        default="INFO",
        description="Log level for the structlog wrapper bound logger.",
    )
    gekko_data_dir: Path = Field(
        default_factory=_default_data_dir,
        description=(
            "Where per-user SQLCipher DB files live. Default: ~/.gekko (A13). "
            "Override via GEKKO_DATA_DIR for tests or custom installs."
        ),
    )
    gekko_user_agent: str = Field(
        default="ProjectGekko/0.1 admin@example.com",
        description=(
            "User-Agent header for SEC EDGAR per Pitfall 12 (SEC fair-use policy). "
            "Override in production with a real contact email."
        ),
    )
    dashboard_url: str = Field(
        default="http://localhost:8000",
        description=(
            "Base URL of the operator's Gekko dashboard. Used by the HITL-06 "
            "dual-channel Slack DM (Plan 02-06) to link the operator to "
            "`{dashboard_url}/live-confirm/{proposal_id}`. Override in "
            "production with the real public-facing or tunnel URL."
        ),
    )

    # ---- Methods ------------------------------------------------------------

    def db_path_for(self, user_id: str) -> Path:
        """Per-user SQLCipher DB path. Honors A13 (`~/.gekko/<user_id>.db`).

        `gekko_data_dir` is `expanduser()`-resolved so tildes survive env-var
        injection. The parent dir is NOT created here — Plan 01-03's engine
        bootstrap owns directory creation.
        """
        return self.gekko_data_dir.expanduser() / f"{user_id}.db"

    def db_url_for(self, user_id: str) -> str:
        """SCAFFOLD SQLAlchemy URL referencing the SQLCipher dialect.

        Plan 01-03 will refine this to the exact driver + passphrase plumbing
        used by `gekko.db.engine.get_async_engine`. For now we return a
        placeholder URL that satisfies the AUTH-03 "uses SQLCipher" contract.

        WARNING — placeholder passphrase: the literal "PLACEHOLDER" string is
        used here only to make the URL syntactically well-formed. The real
        engine never builds a URL with the passphrase embedded (Plan 01-03
        injects PRAGMA key via a connect-event hook, NOT via the URL).
        """
        path = self.db_path_for(user_id)
        # Use forward-slash form so the URL survives both POSIX and Windows.
        path_str = path.as_posix()
        return f"sqlite+pysqlcipher://:PLACEHOLDER@/{path_str}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings singleton.

    Wrapped in `lru_cache` so we read env vars exactly once per process. Tests
    that need a fresh read call `get_settings.cache_clear()` (see the
    `clean_settings_env` fixture in tests/conftest.py).

    Construction surfaces `pydantic.ValidationError` to the caller; downstream
    consumers (`gekko doctor`, `gekko serve`) are expected to translate that
    into a user-friendly message.
    """
    return Settings()  # type: ignore[call-arg]  # all fields satisfied via env


__all__: tuple[str, ...] = ("Settings", "get_settings")
