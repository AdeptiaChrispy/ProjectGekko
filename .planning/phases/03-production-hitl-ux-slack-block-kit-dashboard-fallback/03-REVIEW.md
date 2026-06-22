---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
reviewed: 2026-06-22T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - src/gekko/dashboard/routes.py
  - src/gekko/dashboard/templates/edit_size_modal.html.j2
  - src/gekko/dashboard/static/edit-size-slider.js
  - src/gekko/reporter/slack.py
  - src/gekko/slack/interactivity.py
  - src/gekko/approval/slack_handler.py
findings:
  critical: 0
  warning: 2
  info: 1
  total: 3
status: issues_found
---

# Phase 03 (Gap Closure): Code Review Report — D-62 Edit-Size Slider Redesign

**Reviewed:** 2026-06-22T00:00:00Z
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Reviewed the D-62 gap-closure diff (`5c8f0f9..HEAD`) across 6 files covering the edit-size slider redesign. The primary review concerns — server-side cap authority, auth regression, credential leakage, and Decimal math — are all sound:

- `_check_edit_size_caps` and `edit_size_submit` are unchanged except for adding the new slider context variables to error re-render paths. The server-side cap gate is unweakened.
- `edit_size_get` is correctly on the authenticated `router` (router-level `Depends(require_session)`) and has an explicit `Depends(require_session)` in its signature as well. No auth regression.
- Broker credentials (`alpaca_paper_api_key`, `alpaca_paper_secret_key`) are extracted via `.get_secret_value()` and never placed into the template context, logs, or response. Only the formatted equity number flows to the template.
- `max_shares` computation uses Decimal arithmetic with `int()` (truncates toward zero — equivalent to `floor()` for positive values). Correct.
- Jinja2 autoescape is active for `.html.j2` templates (confirmed at runtime: `env.autoescape = True`). All template variables — `ticker`, `qty`, `proposal_id`, `drift_error`, `ref_price`, `account_equity_display` — are HTML-escaped before rendering. No XSS via template injection.
- The retired Slack handlers (`_edit_size` action, `_edit_size_submit` view) ack immediately in both `interactivity.py` and `slack_handler.py`. No `dispatch_failed` risk.

Two warnings were found: the live readout feature will be silently non-functional in production due to two independent issues (CSP blocking inline event handlers, and the DOMContentLoaded init timing mismatch with HTMX). One info item notes dead code left from the retirement.

---

## Narrative Findings (AI reviewer)

## Warnings

### WR-01: Slider readout is non-functional under the enforced CSP — `oninput` inline handler blocked

**File:** `src/gekko/dashboard/templates/edit_size_modal.html.j2:71`

**Issue:** The template's `oninput="updateSizeReadout(this)"` attribute is an inline event handler. The application's Content-Security-Policy (set in `base.html.j2:26`) is `script-src 'self'` with no `'unsafe-inline'` directive. Under CSP level 2, inline event handler attributes (`onclick=`, `oninput=`, etc.) are blocked when `'unsafe-inline'` is absent — regardless of whether the referenced function is defined in an external same-origin script. The browser will silently suppress the handler; no console error is shown to the operator. The `<script src="/static/edit-size-slider.js">` tag itself is allowed (same-origin), but the inline attribute that calls into it is not.

The edit-size modal is delivered as an HTMX partial injected into `#modal-mount` via `hx-swap="innerHTML"`. This means the CSP is enforced on the dynamically-injected content the same way it would be on statically-served HTML.

The slider value (`<input type="range">`) still submits correctly — this is a display-only readout failure, not a cap-bypass or data-loss risk — but the operator will never see the live "N shares ≈ $X — Y% equity" readout, defeating the purpose of the D-62 redesign.

**Fix:** Replace the inline event handler with a CSP-safe event binding added from the external `.js` file itself. The correct hook for HTMX-swapped content is `htmx:afterSettle`, which fires after every successful HTMX swap. Register the slider handler once at the document level:

```javascript
// In edit-size-slider.js — replace the DOMContentLoaded block with:
document.addEventListener('htmx:afterSettle', function (evt) {
    var sliders = evt.detail.elt.querySelectorAll('.edit-size-slider');
    sliders.forEach(function (slider) {
        // Remove any previously bound listener to avoid double-firing
        slider.removeEventListener('input', _onSliderInput);
        slider.addEventListener('input', _onSliderInput);
        updateSizeReadout(slider);  // initialise readout immediately on inject
    });
});

function _onSliderInput(evt) {
    updateSizeReadout(evt.target);
}
```

Then remove `oninput="updateSizeReadout(this)"` from the `<input type="range">` element in the template.

---

### WR-02: `DOMContentLoaded` init never fires for HTMX-injected partials — initial readout always blank

**File:** `src/gekko/dashboard/static/edit-size-slider.js:57-62`

**Issue:** The `DOMContentLoaded` event fires once per full page load. The edit-size modal is loaded as an HTMX partial (`hx-get="/approvals/{id}/edit-size"`, `hx-swap="innerHTML"` into `#modal-mount`), injected into the DOM after the page has already loaded. By the time the modal HTML is swapped in and the `<script src="/static/edit-size-slider.js">` tag is re-evaluated by HTMX, `DOMContentLoaded` has already fired and the listener added on lines 57–62 will never trigger. The `querySelectorAll('.edit-size-slider')` call inside it will not find any sliders.

This is independent from WR-01: even if CSP were relaxed to allow `'unsafe-inline'`, the initial readout showing the proposed qty's dollar value and equity percentage would still be blank on first render because this init never runs.

**Fix:** Covered by the same `htmx:afterSettle` delegate approach described in WR-01. The `htmx:afterSettle` listener fires for every HTMX swap targeting `#modal-mount`, which is exactly when the slider appears in the DOM. No DOMContentLoaded block is needed.

---

## Info

### IN-01: `_edit_size_submit_workflow` is dead code post-D-62

**File:** `src/gekko/approval/slack_handler.py:642-741`

**Issue:** `_edit_size_submit_workflow` was the backend logic for the old Slack Block Kit modal view submission flow. With the retirement of `handle_edit_size_view_submission` to a no-op ack stub in D-62, this function is unreachable from any Bolt handler. It carries imports, DB operations, and an `asyncio.create_task` call that will never execute. It is not harmful but adds ~100 lines of misleading code that a future reader might mistakenly believe is still in the live path.

**Fix:** Remove `_edit_size_submit_workflow` from `slack_handler.py` in the next cleanup pass. Also remove its only remaining caller reference in `__all__` if present (currently `handle_edit_size_stub` alias remains — that is fine to keep for backward-compat; it's the workflow function that should be dropped).

---

_Reviewed: 2026-06-22T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
