/**
 * edit-size-slider.js — Plan 03-14 (D-62), CSP fix (code review WR-01/WR-02)
 *
 * Live readout for the edit-size range slider.
 * Loaded via <script src="/static/edit-size-slider.js"> inside
 * edit_size_modal.html.j2 (CSP-safe: script-src 'self').
 *
 * CSP constraint: the app sets `script-src 'self'` with NO 'unsafe-inline'
 * (base.html.j2). Inline event-handler attributes (oninput=...) and inline
 * <script> blocks are therefore BLOCKED by the browser. We bind behaviour
 * here instead:
 *   1. A delegated 'input' listener on document catches handle moves from
 *      any .edit-size-slider, including HTMX-injected ones.
 *   2. htmx:afterSettle re-renders the readout each time the modal partial
 *      is swapped in.
 *   3. An immediate initAllReadouts() call covers the common case where this
 *      script executes right after HTMX injects the slider into the DOM.
 *   4. DOMContentLoaded covers a full-page (non-HTMX) render.
 * Setup is guarded so repeated injection of this <script> binds only once.
 */

/**
 * Update the #size-readout element with a live share/notional/equity summary.
 *
 * @param {HTMLInputElement} el - The range input element.
 */
function updateSizeReadout(el) {
    if (!el) {
        return;
    }
    var qty = parseInt(el.value, 10);
    if (isNaN(qty) || qty < 1) {
        qty = 1;
    }

    var refPrice = parseFloat(el.dataset.refPrice || "0");
    var equity = el.dataset.equity || "";

    var notional = qty * refPrice;
    var notionalStr = notional.toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });

    var pctDisplay = "";
    if (equity) {
        var rawEquity = parseFloat(equity.replace(/[^0-9.]/g, "")) || 0;
        if (rawEquity > 0) {
            var pctVal = (notional / rawEquity * 100).toFixed(1);
            pctDisplay = pctVal + "% of your " + equity;
        }
    }

    var readout = document.getElementById("size-readout");
    if (!readout) {
        return;
    }

    if (pctDisplay) {
        readout.textContent = qty + " shares ≈ $" + notionalStr + " — " + pctDisplay;
    } else {
        readout.textContent = qty + " shares";
    }
}

/** Render the readout for every slider currently in the DOM. */
function initAllReadouts() {
    var sliders = document.querySelectorAll('.edit-size-slider');
    sliders.forEach(function (slider) {
        updateSizeReadout(slider);
    });
}

// One-time, CSP-safe binding. The <script src> may be re-injected on each
// modal open (HTMX runs scripts in swapped content); guard so we attach the
// document-level listeners only once.
if (!window.__editSizeSliderBound) {
    window.__editSizeSliderBound = true;

    // Delegated input handler — survives HTMX swaps (replaces inline oninput).
    document.addEventListener('input', function (e) {
        var t = e.target;
        if (t && t.classList && t.classList.contains('edit-size-slider')) {
            updateSizeReadout(t);
        }
    });

    // Re-render after HTMX settles new content (modal injected into #modal-mount).
    document.body.addEventListener('htmx:afterSettle', initAllReadouts);

    // Full-page (non-HTMX) render fallback.
    document.addEventListener('DOMContentLoaded', initAllReadouts);
}

// Immediate pass: when HTMX injects this partial, the slider is already in the
// DOM by the time this script executes, so render the initial readout now.
initAllReadouts();
