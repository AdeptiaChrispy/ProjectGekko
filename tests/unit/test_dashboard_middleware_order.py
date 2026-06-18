"""Wave 0 stub — populated in Plan 03-05.

AST gate: SessionMiddleware registered BEFORE banner-state middleware in create_app
(Starlette reverse-order execution rule).
"""

from __future__ import annotations

import pytest


def test_session_middleware_before_banner_middleware() -> None:
    pytest.skip("Wave 0 stub — populated in Plan 03-05")
