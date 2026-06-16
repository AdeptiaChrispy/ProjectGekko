"""Tenacity retry decorator for broker GET rate-limit (429) handling — Plan 02-03 Task 1.

Per RESEARCH §6 (EXEC-08) + PATTERNS §2 row 3:

* Tenacity-wraps Alpaca rate-limit 429 responses with exponential backoff +
  jitter (``wait_random_exponential(min=1, max=60)``) + bounded attempts
  (``stop_after_attempt(6)`` = 5 retries + 1 initial = 6 total attempts).
* Only retries on 429 (``_is_rate_limit`` checks ``APIError.status_code == 429``
  with defense-in-depth text-match fallback for ``"rate limit"`` /
  ``"too many requests"`` / ``" 429"`` substrings).
* ``before_sleep_log`` emits a structured warning on each retry attempt
  (operator visibility into rate-limit thrash).
* ``reraise=True`` — the FINAL 429 (after 6 attempts) propagates as the
  underlying APIError. We do NOT swallow it; callers see the real broker
  error and decide whether to fail closed.

**EXEC-03 / Pitfall 4 / Knight Capital invariant (load-bearing):** This
decorator is applied to broker GET methods ONLY. Never to ``place_order``
(POST). Never to ``cancel_order`` (per RESEARCH §6 Open Question #1 —
a 429 retry storm during a kill is the worst possible failure mode; the
kill switch's ``asyncio.gather`` + 4s timeout is the failure-tolerant
scaffold). The AST-walk gate in ``tests/unit/test_alpaca_retry.py``
enforces this — parsing the source tree and asserting
``len(place_order.decorator_list) == 0``.

References:
  * .planning/phases/02-orderguard.../02-RESEARCH.md  §6 (verbatim)
  * .planning/phases/02-orderguard.../02-PATTERNS.md  §2 row 3 + §4 row 3
  * src/gekko/brokers/alpaca.py — decorator applied to GETs only
"""

from __future__ import annotations

import logging

from alpaca.common.exceptions import APIError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from gekko.logging_config import get_logger

log = get_logger(__name__)


def _is_rate_limit(exc: BaseException) -> bool:
    """Return True if ``exc`` is a 429 rate-limit error from the broker.

    Primary check: ``APIError.status_code == 429``.

    Defense-in-depth text-match fallback (some alpaca-py 0.43 code paths
    surface 429 as a generic ``APIError`` with the body text):

    * ``"rate limit"`` (lowercase)
    * ``"too many requests"`` (lowercase)
    * ``" 429"`` (with leading space — avoids matching `4290`, `42910`, etc.)
    """
    if not isinstance(exc, APIError):
        return False
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    text = str(exc).lower()
    return (
        "rate limit" in text
        or "too many requests" in text
        or " 429" in text
    )


#: The tenacity ``retry`` decorator factory applied to broker GET methods.
#:
#: * ``wait=wait_random_exponential(min=1, max=60)`` — base 1s, cap 60s,
#:   jittered randomly within the exponential envelope. RESEARCH §6
#:   verbatim choice.
#: * ``stop=stop_after_attempt(6)`` — 6 total attempts (1 initial + 5
#:   retries). With max wait 60s the worst-case retry window is ~5 minutes,
#:   which is the upper bound the kill switch + executor timeouts assume.
#: * ``retry=retry_if_exception(_is_rate_limit)`` — non-429 errors propagate
#:   immediately. Notably, ``BrokerConfigError`` / ``BrokerOrderError`` /
#:   any 5xx error NEVER trigger retries.
#: * ``before_sleep=before_sleep_log(log, logging.WARNING)`` — structured
#:   warning logged on each retry attempt (operator visibility).
#: * ``reraise=True`` — exhausted attempts re-raise the LAST exception
#:   (rather than tenacity's RetryError wrapper). Callers see the real
#:   APIError(429).
retry_on_rate_limit = retry(
    wait=wait_random_exponential(min=1, max=60),
    stop=stop_after_attempt(6),
    retry=retry_if_exception(_is_rate_limit),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)


__all__: tuple[str, ...] = ("_is_rate_limit", "retry_on_rate_limit")
