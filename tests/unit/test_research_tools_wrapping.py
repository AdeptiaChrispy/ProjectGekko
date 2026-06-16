"""Wave-0 stub — RES-07 untrusted-content wrapping at Researcher tool boundary.

# WAVE-0 STUB: owned by plan 02-04 — DO NOT delete the skip until that plan's tasks land

Covers RES-07 — the four Researcher tools wrap fetched content
(yahoo_news.body, finnhub_news.headline, web_fetch.text) in
<UNTRUSTED>...</UNTRUSTED> markers BEFORE the Researcher LLM sees them.
The injection defense lives at the tool layer so the LLM cannot bypass.
"""

from __future__ import annotations

import pytest

pytest.skip("Wave-0 stub", allow_module_level=True)


def test_yahoo_news_body_wrapped_in_untrusted_markers_placeholder() -> None:
    """Will assert yahoo_news returns body wrapped in <UNTRUSTED>..</UNTRUSTED>."""
    pass
