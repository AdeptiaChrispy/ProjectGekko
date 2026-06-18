"""SQLCipher passphrase vault — Plan 01-09 Task 1 (D-19).

Process-wide module-global passphrase cache. The CLI bootstrap
(``gekko init`` / ``gekko serve`` / ``gekko run`` / ``gekko audit ...``)
populates it via :func:`prompt_passphrase` (interactive ``getpass``) or
:func:`set_passphrase` (non-interactive — env var, test fixture). Every
downstream module that builds a per-user SQLCipher engine reads from
here via :func:`get_passphrase`.

Replaces the per-module ``_GET_PASSPHRASE()`` indirection that Plans
01-07 (agent runtime) and 01-08 (executor + slack handler) installed as
placeholders. Now the single source of truth lives in
:mod:`gekko.vault.passphrase`.

D-19 invariants:
  * Lives in process memory only — NEVER persisted, NEVER logged,
    NEVER returned by :func:`repr`.
  * No keychain integration — the operator types the passphrase at
    process start. If the host crashes, the next ``gekko serve``
    prompts again. Cross-platform parity is the design rationale per
    PROJECT.md key decision.
  * Module-global is safe under D-18's single-process modular monolith
    assumption. A future multi-process variant would replace this with
    a SHM-backed cache + parent-process prompt.

Test isolation:
  * :func:`set_passphrase` is the test seam — tests call it directly
    instead of stubbing :mod:`getpass`. Each test ends by calling
    :func:`clear` so a leaked passphrase doesn't pollute the next test.
"""

from __future__ import annotations

import getpass
import os

# ---------------------------------------------------------------------------
# Module-global cache
# ---------------------------------------------------------------------------

#: The cached passphrase. ``None`` means "not set yet". Read via
#: :func:`get_passphrase` (raises if ``None``) — never read directly.
_passphrase: str | None = None

#: Env-var fallback for non-interactive bootstrap paths (CI runs,
#: Windows TTY where ``getpass`` can't read piped stdin, the walking-
#: skeleton manual-demo flow). When set, :func:`prompt_passphrase` uses
#: its value WITHOUT prompting. The env-var is also the same name
#: ``gekko init`` already passes to the alembic subprocess, so the two
#: code paths stay consistent.
#:
#: Operator caveat: an env-var-stored passphrase is readable by any
#: process in the same env tree (child processes, debuggers, `ps`
#: visibility on some platforms). Prefer the interactive prompt for
#: long-running production processes; only use the env-var path for
#: deliberate non-interactive scenarios.
_ENV_VAR: str = "GEKKO_DB_PASSPHRASE"


def prompt_passphrase(
    prompt_text: str = "SQLCipher passphrase: ",
) -> str:
    """Read the passphrase and cache it for the process lifetime.

    Resolution order:

      1. If the cache is already populated (a prior call set it), return
         that value — no prompt, no env read.
      2. If the :data:`_ENV_VAR` environment variable is set to a
         non-empty value, cache + return it (no prompt).
      3. Otherwise call :func:`getpass.getpass` interactively.

    :param prompt_text: The prompt shown to the operator when falling
        through to the interactive branch.
    :returns: The cached passphrase. NEVER logged.
    """
    global _passphrase
    if _passphrase is not None:
        return _passphrase
    env_value = os.environ.get(_ENV_VAR, "").strip()
    if env_value:
        _passphrase = env_value
        return _passphrase
    _passphrase = getpass.getpass(prompt_text)
    return _passphrase


def set_passphrase(passphrase: str) -> None:
    """Set the cached passphrase directly — non-interactive path.

    Used by:
      * ``gekko init`` after the first-run wizard collected + confirmed
        the new passphrase.
      * Test fixtures that need a known passphrase without monkeypatching
        :mod:`getpass`.
      * Environments where the operator supplies the passphrase via env
        var (e.g., ``GEKKO_DB_PASSPHRASE``).
    """
    global _passphrase
    _passphrase = passphrase


def get_passphrase() -> str:
    """Return the cached passphrase, or raise if not set.

    :raises RuntimeError: When neither :func:`prompt_passphrase` nor
        :func:`set_passphrase` has been called yet. The CLI surfaces
        this as a clear "Run ``gekko init`` first" message.
    """
    if _passphrase is None:
        msg = (
            "Passphrase not set. Run `gekko init` first, or call "
            "`gekko.vault.passphrase.set_passphrase(...)` during "
            "process bootstrap."
        )
        raise RuntimeError(msg)
    return _passphrase


def verify_passphrase(candidate: str) -> bool:
    """Return True iff ``candidate`` matches the cached passphrase.

    Used by the dashboard ``POST /login`` handler (D-57). Compares the
    submitted passphrase against the in-memory cache without exposing the
    cached value in logs or exceptions.

    :param candidate: The operator-submitted passphrase from the login form.
    :returns: True if ``candidate == _passphrase``, False otherwise.
    :raises RuntimeError: When no passphrase has been cached yet (same as
        :func:`get_passphrase`). The operator must have run ``gekko serve``
        which prompts and caches the passphrase before routes can fire.
    """
    cached = get_passphrase()  # raises RuntimeError if not set
    return candidate == cached


def clear() -> None:
    """Forget the cached passphrase. Test-only helper."""
    global _passphrase
    _passphrase = None


__all__: tuple[str, ...] = (
    "clear",
    "get_passphrase",
    "prompt_passphrase",
    "set_passphrase",
    "verify_passphrase",
)
