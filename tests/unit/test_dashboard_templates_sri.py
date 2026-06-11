"""SRI lint gate + vendored-HTMX integrity tests — Plan 01-09 Task 3.

Three tests defending against supply-chain regressions (T-01-09-08, T-01-09-09):

1. No external ``<script src="http(s)://...">`` tag in any Jinja2 template
   without ``integrity="sha384-..."`` AND ``crossorigin="anonymous"``.
   Vendored same-origin ``<script src="/static/...">`` tags pass through.
2. ``base.html.j2`` specifically loads HTMX from ``/static/htmx.min.js``
   (NOT from unpkg / jsdelivr / cdnjs).
3. The on-disk SHA-384 of ``src/gekko/dashboard/static/htmx.min.js``
   matches the digest recorded in ``static/VENDOR.md``. Tamper or stale
   metadata = test failure.
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES = _REPO_ROOT / "src" / "gekko" / "dashboard" / "templates"
_STATIC = _REPO_ROOT / "src" / "gekko" / "dashboard" / "static"

_EXTERNAL_SCRIPT = re.compile(
    r'<script[^>]*\bsrc=["\']https?://[^"\']+["\'][^>]*>',
    re.IGNORECASE,
)
_HAS_INTEGRITY = re.compile(
    r'\bintegrity=["\']sha(?:256|384|512)-[A-Za-z0-9+/=]+["\']',
    re.IGNORECASE,
)
_HAS_CROSSORIGIN = re.compile(
    r'\bcrossorigin=["\']anonymous["\']', re.IGNORECASE
)


def test_no_external_scripts_without_sri() -> None:
    """External ``<script src="http(s)://...">`` MUST have integrity + crossorigin."""
    offending: list[str] = []
    for tpl in _TEMPLATES.rglob("*.html.j2"):
        text = tpl.read_text(encoding="utf-8")
        for match in _EXTERNAL_SCRIPT.finditer(text):
            tag = match.group(0)
            if not (_HAS_INTEGRITY.search(tag) and _HAS_CROSSORIGIN.search(tag)):
                offending.append(f"{tpl}: {tag}")

    assert not offending, (
        "External <script> tags without SRI found:\n  "
        + "\n  ".join(offending)
        + "\nFix: vendor the script under src/gekko/dashboard/static/ "
        "(preferred) OR add integrity=\"sha384-...\" "
        "crossorigin=\"anonymous\" attributes."
    )


def test_htmx_is_vendored_not_cdn() -> None:
    """``base.html.j2`` loads HTMX from /static/, not a CDN."""
    base = (_TEMPLATES / "base.html.j2").read_text(encoding="utf-8")
    assert "/static/htmx" in base, (
        "base.html.j2 should load HTMX from /static/htmx.min.js (vendored)"
    )
    forbidden = [
        "unpkg.com/htmx",
        "cdn.jsdelivr.net/npm/htmx",
        "cdnjs.cloudflare.com/ajax/libs/htmx",
    ]
    for needle in forbidden:
        assert needle not in base, (
            f"base.html.j2 must NOT load HTMX from CDN ({needle}); "
            "vendor it under /static/"
        )


def test_vendored_htmx_sha384_matches_vendor_md() -> None:
    """On-disk SHA-384 of htmx.min.js must equal the digest in VENDOR.md.

    Detects tampering or stale vendor metadata. Re-vendor via the
    procedure documented in VENDOR.md and commit the matching VENDOR.md
    update together with the new JS file.
    """
    htmx = _STATIC / "htmx.min.js"
    vendor_md = _STATIC / "VENDOR.md"
    if not htmx.exists() or not vendor_md.exists():
        pytest.skip("Vendored assets not yet present (run Task 3 vendoring)")
    actual = (
        "sha384-"
        + base64.b64encode(hashlib.sha384(htmx.read_bytes()).digest()).decode()
    )
    assert actual in vendor_md.read_text(encoding="utf-8"), (
        f"Vendored htmx.min.js SHA-384 ({actual}) is not recorded in "
        "VENDOR.md — either the file was tampered with or VENDOR.md is "
        "stale. Re-vendor per VENDOR.md and update its digest."
    )
