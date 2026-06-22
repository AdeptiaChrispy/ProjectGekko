/**
 * edit-size-slider.js — Plan 03-14 (D-62)
 *
 * Live readout for the edit-size range slider.
 * Loaded via <script src="/static/edit-size-slider.js"> inside
 * edit_size_modal.html.j2 (CSP-safe: script-src 'self').
 *
 * The slider's oninput="updateSizeReadout(this)" attribute calls this
 * function on every handle move. A DOMContentLoaded listener in the
 * partial initialises the readout on first render so the operator sees
 * context immediately without having to drag the handle.
 */

/**
 * Update the #size-readout element with a live share/notional/equity summary.
 *
 * @param {HTMLInputElement} el - The range input element.
 */
function updateSizeReadout(el) {
    var qty = parseInt(el.value, 10);
    if (isNaN(qty) || qty < 1) {
        qty = 1;
    }

    var refPrice = parseFloat(el.dataset.refPrice || "0");
    var equity = el.dataset.equity || "";
    var maxPct = parseFloat(el.dataset.maxPct || "0");

    var notional = qty * refPrice;
    var notionalStr = notional.toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });

    var pctDisplay = "";
    if (maxPct > 0 && equity) {
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

// Initialise the readout on first render (before any drag)
document.addEventListener('DOMContentLoaded', function () {
    var sliders = document.querySelectorAll('.edit-size-slider');
    sliders.forEach(function (slider) {
        updateSizeReadout(slider);
    });
});
