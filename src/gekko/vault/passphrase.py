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

# ---------------------------------------------------------------------------
# Module-global cache
# ---------------------------------------------------------------------------

#: The cached passphrase. ``None`` means "not set yet". Read via
#: :func:`get_passphrase` (raises if ``None``) — never read directly.
_passphrase: str | None = None


def prompt_passphrase(
    prompt_text: str = "SQLCipher passphrase: ",
) -> str:
    """Read the passphrase from stdin (interactive ``getpass``) and cache it.

    Subsequent calls return the cached value without re-prompting (so
    ``gekko serve`` followed by background jobs all see the same
    passphrase). To force a re-prompt, call :func:`clear` first.

    :param prompt_text: The prompt shown to the operator. Defaults to a
        generic "SQLCipher passphrase: " — callers can supply more
        specific wording (e.g., "Enter passphrase to unlock DB: ").
    :returns: The cached passphrase (either freshly prompted or from a
        prior call). NEVER logged.
    """
    global _passphrase
    if _passphrase is not None:
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


def clear() -> None:
    """Forget the cached passphrase. Test-only helper."""
    global _passphrase
    _passphrase = None


__all__: tuple[str, ...] = (
    "clear",
    "get_passphrase",
    "prompt_passphrase",
    "set_passphrase",
)
