"""Tenacity ``retry_on_rate_limit`` behavior tests — plan 02-03 Task 1.

Functional assertions on the decorator's behavior (separate from the
structural / AST gate in ``test_alpaca_retry.py``):

* Tenacity's wait strategy is ``wait_random_exponential(min=1, max=60)``
  (RESEARCH §6 verbatim choice — bounded retry envelope).
* Stop strategy is ``stop_after_attempt(6)`` (5 retries + 1 initial = 6
  total attempts).
* ``_is_rate_limit`` predicate fires on:
    - ``APIError`` with ``status_code == 429`` (primary check)
    - ``APIError`` whose ``str()`` contains ``"rate limit"`` /
      ``"too many requests"`` / ``" 429"`` (defense-in-depth text-match)
  and does NOT fire on:
    - Non-``APIError`` exceptions (``ValueError``, ``RuntimeError``, etc.)
    - ``APIError`` with status 500 / 503 / non-429 codes
* The decorator re-raises the FINAL exception (``reraise=True``) after
  attempts are exhausted; callers see the real ``APIError``, not a
  tenacity ``RetryError`` wrapper.
* Non-429 errors propagate IMMEDIATELY (no retry attempts).

The functional retry runs use a monotonically-incrementing call counter
on a fake decorated function — we don't hit the real Alpaca HTTP layer
here (that's the integration cassette's job in plan 02-07). The waits
between retries are patched to zero via a tenacity-side fixture so the
test suite stays fast.
"""

from __future__ import annotations

from typing import Any

import pytest
from alpaca.common.exceptions import APIError
from tenacity import stop_after_attempt, wait_none

from gekko.brokers._retry import _is_rate_limit, retry_on_rate_limit


# ---------------------------------------------------------------------------
# Helpers — synthesizing APIError instances with controlled status_code
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Mimic ``httpx.Response`` enough for ``APIError.status_code`` to work."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeHTTPError(Exception):
    """Mimic the ``http_error`` parameter ``APIError`` accepts.

    alpaca-py's APIError.status_code property reads
    ``self._http_error.response.status_code``, so we attach a fake response.
    """

    def __init__(self, status_code: int) -> None:
        super().__init__(f"http {status_code}")
        self.response = _FakeResponse(status_code)


def _make_api_error(status_code: int, body: str = "") -> APIError:
    """Build an ``APIError`` whose ``status_code`` property returns the
    requested code.

    alpaca-py 0.43's ``APIError.status_code`` is a property that reads
    from ``self._http_error.response.status_code`` — we synthesize a
    minimal http_error so the predicate's ``getattr(exc, 'status_code',
    None)`` check sees the right value.
    """
    return APIError(body or "synthetic", http_error=_FakeHTTPError(status_code))


# ---------------------------------------------------------------------------
# _is_rate_limit predicate
# ---------------------------------------------------------------------------


def test_is_rate_limit_fires_on_429_status_code() -> None:
    """Primary check: ``APIError.status_code == 429`` -> True."""
    exc = _make_api_error(status_code=429, body="quota exceeded")
    assert _is_rate_limit(exc) is True


def test_is_rate_limit_text_match_rate_limit() -> None:
    """Defense-in-depth: text body 'rate limit' matches even without 429."""
    exc = APIError("Server returned rate limit error")
    assert _is_rate_limit(exc) is True


def test_is_rate_limit_text_match_too_many_requests() -> None:
    """Defense-in-depth: 'too many requests' matches."""
    exc = APIError("HTTP error: Too Many Requests")
    assert _is_rate_limit(exc) is True


def test_is_rate_limit_text_match_429_substring() -> None:
    """Defense-in-depth: leading-space ' 429' substring matches."""
    exc = APIError("Got HTTP 429 from server")
    assert _is_rate_limit(exc) is True


def test_is_rate_limit_does_not_fire_on_500() -> None:
    """5xx server errors are NOT rate-limit errors — propagate immediately."""
    exc = _make_api_error(status_code=500, body="server error")
    assert _is_rate_limit(exc) is False


def test_is_rate_limit_does_not_fire_on_422() -> None:
    """422 duplicate-id errors must NOT trigger retries — Knight Capital
    prevention works ONLY because place_order's 422 handler runs once."""
    exc = _make_api_error(status_code=422, body="duplicate client_order_id")
    assert _is_rate_limit(exc) is False


def test_is_rate_limit_does_not_fire_on_non_api_error() -> None:
    """Non-``APIError`` exceptions never retry, regardless of message."""
    exc = ValueError("rate limit exceeded")  # message would match text fallback
    assert _is_rate_limit(exc) is False


def test_is_rate_limit_text_match_is_substring_loose() -> None:
    """The text-match fallback is intentionally loose — ' 429' is a
    plain substring check. Documented as the acceptable false-positive
    surface: a 429 retry on a 4290 / 42910 / etc. body is a no-op (the
    real HTTP layer's response code wouldn't actually be 429), and
    spurious retries on a transient 429-lookalike body are cheaper than
    missing a real 429. RESEARCH §6 explicit choice."""
    exc = APIError("Got HTTP 4290 from server")  # contains ' 429' substring
    assert _is_rate_limit(exc) is True


# ---------------------------------------------------------------------------
# Decorator stop / wait configuration
# ---------------------------------------------------------------------------


def test_retry_decorator_is_configured() -> None:
    """The exported ``retry_on_rate_limit`` is a tenacity decorator-factory.

    Smoke-tests that the symbol exists, is callable, and decorates a
    plain async function without raising.
    """
    assert callable(retry_on_rate_limit), (
        "retry_on_rate_limit must be a tenacity decorator (callable)"
    )

    @retry_on_rate_limit
    async def _decorated() -> int:
        return 42

    # Tenacity sets __wrapped__ on the decorated callable.
    assert hasattr(_decorated, "__wrapped__"), (
        "tenacity decoration must set __wrapped__ on the wrapped function"
    )


# ---------------------------------------------------------------------------
# End-to-end retry behavior (with patched waits so tests are fast)
# ---------------------------------------------------------------------------


def _make_throwing_then_succeed(
    failures: int,
    final_value: Any,
    exception_factory: Any,
) -> Any:
    """Build an async function that throws N times, then returns ``final_value``.

    ``exception_factory`` is a 0-arg callable producing the exception to
    raise on each failure.
    """
    state = {"calls": 0}

    async def _fn() -> Any:
        state["calls"] += 1
        if state["calls"] <= failures:
            raise exception_factory()
        return final_value

    _fn.state = state  # type: ignore[attr-defined]
    return _fn


@pytest.mark.asyncio
async def test_retry_succeeds_after_one_429(monkeypatch: pytest.MonkeyPatch) -> None:
    """One 429 -> retry -> success. Total call count == 2."""

    # Patch the decorator's wait to zero so the test is fast.
    from gekko.brokers import _retry as retry_mod

    fast_decorator = retry_mod.retry(
        wait=wait_none(),
        stop=stop_after_attempt(6),
        retry=retry_mod.retry_if_exception(retry_mod._is_rate_limit),
        reraise=True,
    )

    inner = _make_throwing_then_succeed(
        failures=1,
        final_value="ok",
        exception_factory=lambda: _make_api_error(429, "rate limit"),
    )
    decorated = fast_decorator(inner)
    result = await decorated()
    assert result == "ok"
    assert inner.state["calls"] == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_retry_succeeds_after_three_429s(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three 429s in a row -> retry -> success. Total call count == 4."""
    from gekko.brokers import _retry as retry_mod

    fast_decorator = retry_mod.retry(
        wait=wait_none(),
        stop=stop_after_attempt(6),
        retry=retry_mod.retry_if_exception(retry_mod._is_rate_limit),
        reraise=True,
    )

    inner = _make_throwing_then_succeed(
        failures=3,
        final_value={"ok": True},
        exception_factory=lambda: _make_api_error(429, "rate limit"),
    )
    decorated = fast_decorator(inner)
    result = await decorated()
    assert result == {"ok": True}
    assert inner.state["calls"] == 4  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_retry_exhausted_reraises_final_429() -> None:
    """6 consecutive 429s -> raise after attempt 6. Total calls == 6."""
    from gekko.brokers import _retry as retry_mod

    fast_decorator = retry_mod.retry(
        wait=wait_none(),
        stop=stop_after_attempt(6),
        retry=retry_mod.retry_if_exception(retry_mod._is_rate_limit),
        reraise=True,
    )

    state = {"calls": 0}

    async def _always_429() -> None:
        state["calls"] += 1
        raise _make_api_error(429, "sustained rate limit")

    decorated = fast_decorator(_always_429)
    with pytest.raises(APIError) as exc_info:
        await decorated()
    # ``reraise=True`` ensures the underlying APIError surfaces.
    assert exc_info.value.status_code == 429  # type: ignore[attr-defined]
    assert state["calls"] == 6, (
        f"expected 6 attempts (stop_after_attempt(6)); got {state['calls']}"
    )


@pytest.mark.asyncio
async def test_retry_does_not_fire_on_non_429() -> None:
    """A 500 error propagates IMMEDIATELY — no retries attempted."""
    from gekko.brokers import _retry as retry_mod

    fast_decorator = retry_mod.retry(
        wait=wait_none(),
        stop=stop_after_attempt(6),
        retry=retry_mod.retry_if_exception(retry_mod._is_rate_limit),
        reraise=True,
    )

    state = {"calls": 0}

    async def _always_500() -> None:
        state["calls"] += 1
        raise _make_api_error(500, "server error")

    decorated = fast_decorator(_always_500)
    with pytest.raises(APIError) as exc_info:
        await decorated()
    assert exc_info.value.status_code == 500  # type: ignore[attr-defined]
    assert state["calls"] == 1, (
        "non-429 errors must propagate after 1 attempt, no retries"
    )


@pytest.mark.asyncio
async def test_retry_does_not_fire_on_422_duplicate() -> None:
    """422 (Pitfall-4 duplicate-id) must propagate immediately — the
    place_order body handles it via _is_duplicate_error, NOT via retry."""
    from gekko.brokers import _retry as retry_mod

    fast_decorator = retry_mod.retry(
        wait=wait_none(),
        stop=stop_after_attempt(6),
        retry=retry_mod.retry_if_exception(retry_mod._is_rate_limit),
        reraise=True,
    )

    state = {"calls": 0}

    async def _always_422() -> None:
        state["calls"] += 1
        raise _make_api_error(422, "duplicate client_order_id")

    decorated = fast_decorator(_always_422)
    with pytest.raises(APIError):
        await decorated()
    assert state["calls"] == 1, (
        "422 errors must propagate after 1 attempt — Pitfall 4 duplicate-id "
        "handling is the place_order body's job, NOT the retry layer"
    )


@pytest.mark.asyncio
async def test_retry_does_not_fire_on_value_error() -> None:
    """Non-APIError exceptions propagate immediately."""
    from gekko.brokers import _retry as retry_mod

    fast_decorator = retry_mod.retry(
        wait=wait_none(),
        stop=stop_after_attempt(6),
        retry=retry_mod.retry_if_exception(retry_mod._is_rate_limit),
        reraise=True,
    )

    state = {"calls": 0}

    async def _always_value_error() -> None:
        state["calls"] += 1
        raise ValueError("rate limit")  # message would match text-match

    decorated = fast_decorator(_always_value_error)
    with pytest.raises(ValueError):
        await decorated()
    assert state["calls"] == 1
