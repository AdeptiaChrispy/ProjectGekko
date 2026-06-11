# Vendored Static Assets

Per Plan 01-09 Task 3 supply-chain hardening (T-01-09-08), the dashboard
loads every browser-side script from same-origin paths under `/static/`.
External CDN `<script>` tags are prohibited unless they carry SRI
(`integrity="sha384-..." crossorigin="anonymous"`) attributes. The lint
gate in `tests/unit/test_dashboard_templates_sri.py` enforces this on
every template at every build.

## Vendored files

### htmx.min.js

| Field | Value |
|-------|-------|
| Package | [`htmx.org`](https://htmx.org/) |
| Version | **2.0.4** |
| Source URL | `https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js` |
| Downloaded | 2026-06-11 |
| SHA-384 | `sha384-HGfztofotfshcF7+8n44JQL2oJmowVChPTg48S+jvZoztPfvwD79OC/LTtG6dMp+` |
| License | BSD 2-Clause (htmx) |

The dashboard's `base.html.j2` references this via:

```html
<script src="/static/htmx.min.js" defer></script>
```

Same-origin path — no third-party CDN, no SRI attribute needed (the
SRI lint gate only requires SRI on `http(s)://` script srcs; vendored
same-origin scripts are trusted by definition).

### tailwind.css

| Field | Value |
|-------|-------|
| Package | Hand-crafted utility subset (NOT a full Tailwind build) |
| Source | `src/gekko/dashboard/static/tailwind.css` (this repo) |
| Notes | P1 minimal CSS. P9 deployment phase replaces with a proper Tailwind standalone CLI build. See ROADMAP Phase 9. |

The dashboard's `base.html.j2` references this via:

```html
<link rel="stylesheet" href="/static/tailwind.css">
```

## Re-vendoring procedure

To bump HTMX (or re-pull on tampering suspicion):

```bash
uv run python -c "
import httpx, hashlib, base64, pathlib
URL = 'https://unpkg.com/htmx.org@<NEW-VERSION>/dist/htmx.min.js'
data = httpx.get(URL, timeout=30, follow_redirects=True).content
out = pathlib.Path('src/gekko/dashboard/static/htmx.min.js')
out.write_bytes(data)
digest = 'sha384-' + base64.b64encode(hashlib.sha384(data).digest()).decode()
print('SHA-384:', digest)
print('Update VENDOR.md with the new version + digest + URL + date.')
"
```

Then update this VENDOR.md with the new version, source URL, date, and
SHA-384 digest. Commit the JS file + VENDOR.md change together. The
`test_vendored_htmx_sha384_matches_vendor_md` lint test will fail if the
two drift apart.
