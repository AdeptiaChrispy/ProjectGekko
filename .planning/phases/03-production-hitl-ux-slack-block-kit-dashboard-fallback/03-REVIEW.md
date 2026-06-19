---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
reviewed: 2026-06-19T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - src/gekko/approval/actions.py
  - src/gekko/approval/slack_handler.py
  - src/gekko/dashboard/routes.py
  - src/gekko/dashboard/templates/edit_size_modal.html.j2
  - src/gekko/dashboard/templates/approvals_index.html.j2
  - src/gekko/dashboard/templates/_proposals_list.html.j2
findings:
  critical: 2
  warning: 2
  info: 2
  total: 6
status: issues_found
---

# Phase 03 Gap-Closure: Code Review Report

**Reviewed:** 2026-06-19T00:00:00Z
**Depth:** standard
**Diff Base:** edd9c7d..HEAD
**Files Reviewed:** 6
**Status:** issues_found

## Summary

This is a targeted gap-closure review covering four changes introduced since edd9c7d:
(1) `_check_edit_size_caps` in `actions.py` — the new OrderGuard hard-cap validator
replacing the 2% drift gate for operator edits; (2) the Slack and dashboard wiring that
calls it (equity fetch, strategy load, cap check); (3) the new `GET /approvals/poll`
HTMX route with 30-second polling; (4) the `edit_size_modal.html.j2` plain-language
framing and `_proposals_list.html.j2` fragment.

The Decimal arithmetic in `_check_edit_size_caps` is correct. The `/approvals/poll`
route is on the authenticated router and auth is enforced. Jinja2 autoescape is on by
default via Starlette's `Jinja2Templates._create_env`, so `drift_error` and all
user-visible strings are XSS-safe. No broker credentials are logged. However, two
BLOCKERs were found: the cap gate silently no-ops when the strategy row cannot be
loaded (both Slack and dashboard paths), and the Slack background workflow crashes with
an `InvalidOperation` on any market-order edit (zero `target_notional_usd`), leaving
the proposal stuck in PENDING with no operator signal.

---

## Critical Issues

### CR-01: Cap gate silently bypassed when strategy cannot be loaded

**File:** `src/gekko/approval/slack_handler.py:824` and `src/gekko/dashboard/routes.py:724`

**Issue:** Both edit-size paths (Slack view-submission and dashboard POST /edit-submit)
initialise `strategy = None` / `strategy_obj = None` before attempting to load the
strategy from the DB. When loading fails for any reason — DB connection error, missing
strategy row, corrupt `payload_json`, or any exception inside the outer
`try/except Exception` blocks — the variable stays `None`. Both paths then gate the
cap check behind `if strategy is not None:` and skip it entirely when `None`. The
operator receives no error; the modal/form closes and the edit proceeds to
`transition_status(PENDING → APPROVED)` + executor dispatch with an **unchecked
quantity**.

This means any DB hiccup at the moment of an edit-size submission silently bypasses
the OrderGuard hard-cap gate entirely. OrderGuard does re-check at `execute_proposal`
time, but if the strategy hard caps are also unavailable at that point (same failure
mode), there is no backstop. For real-money trades this is the primary hard-stop
against oversized orders.

Concrete trigger: the strategy row is missing (e.g. deleted or strategy_id mismatch),
or the strategy `payload_json` is corrupt (Pydantic parse error at line 805–810 in
`slack_handler.py` or line 687–691 in `routes.py`). The outer `except Exception` at
`slack_handler.py:815` and the inner `except Exception` at `routes.py:690` both swallow
the error and leave `strategy = None`.

**Fix:** Treat a missing or unloadable strategy as a hard rejection, not a pass-through.
If the strategy cannot be loaded, refuse the edit with a user-visible error rather than
skipping the cap check:

```python
# slack_handler.py — inside handle_edit_size_view_submission
if strategy is None:
    await ack({
        "response_action": "errors",
        "errors": {
            "qty_block": (
                "Could not load strategy risk caps — edit rejected. "
                "Try again or contact support."
            )
        },
    })
    return

ok, cap_msg = _check_edit_size_caps(new_qty, ref_price, strategy, equity)
```

```python
# routes.py — inside edit_size_submit
if strategy_obj is None:
    return templates.TemplateResponse(
        request,
        "edit_size_modal.html.j2",
        {
            ...context...,
            "drift_error": (
                "Could not load strategy risk caps — edit rejected. "
                "Refresh and try again."
            ),
        },
    )
```

The `equity == 0` fail-open is an acceptable separate gate (paper account may be
unfunded) and the behaviour is documented. Strategy-not-found is a different condition
and must not silently pass.

---

### CR-02: `_edit_size_submit_workflow` crashes and silently abandons the edit when `target_notional_usd == "0"`

**File:** `src/gekko/approval/slack_handler.py:909–910`

**Issue:** The background workflow computes an audit `drift_pct` field as:

```python
"drift_pct": abs(new_notional - Decimal(meta["target_notional_usd"]))
              / Decimal(meta["target_notional_usd"]),
```

`meta["target_notional_usd"]` is set in `handle_edit_size` at line 666:

```python
target = tp.target_notional_usd or Decimal("0")
```

For a market order with no `target_notional_usd` (or one that is explicitly zero),
`target = Decimal("0")` is stored in `private_metadata`. In the background workflow,
`Decimal("0")` is the denominator, which raises `decimal.InvalidOperation` (Decimal
division by zero).

The exception propagates inside the `async with sf() as session, session.begin():`
block at line 866, rolling back the transaction. The enclosing `except Exception` at
line 928 catches it and logs it, but:

1. The dedup `claim_action` row was committed as `"first_write"` (it runs first in the
   same transaction — but since the transaction rolls back, the dedup row is also
   rolled back). On retry the operator triggers the workflow again, hitting the same
   crash.
2. `execute_proposal` is never dispatched (line 926 is after the rolled-back block).
3. The Slack modal already closed (`ack()` was called at line 834). The operator sees
   nothing — no error, no confirmation, no trade.

This affects any proposal created from a market order where `target_notional_usd` is
zero or absent.

**Fix:** Guard the division and omit or cap the `drift_pct` field when the denominator
is zero:

```python
_target_notional = Decimal(meta["target_notional_usd"])
_drift_pct = (
    abs(new_notional - _target_notional) / _target_notional
    if _target_notional > Decimal("0")
    else Decimal("0")
)
await append_event(
    session,
    ...
    payload=normalize_decimals({
        ...
        "drift_pct": _drift_pct,
        ...
    }),
)
```

---

## Warnings

### WR-01: `outcome` and `updated_row` are potentially unbound on exception in `edit_size_submit`

**File:** `src/gekko/dashboard/routes.py:812,815`

**Issue:** In the `edit_size_submit` handler, `outcome` and `updated_row` are first
assigned inside the `async with sf3() as session2, session2.begin():` block (lines
746 and 797/800). The surrounding `try/finally` has no `except` clause:

```python
try:
    async with sf3() as session2, session2.begin():
        outcome = await claim_action(...)   # if this raises…
        ...
        updated_row = row2                  # …this is never reached
finally:
    if engine3 is not None:
        await engine3.dispose()

if outcome == "first_write":        # line 812 — UnboundLocalError
    asyncio.create_task(...)
if updated_row is None:             # line 815 — UnboundLocalError
    raise HTTPException(...)
```

If `claim_action` raises an unexpected exception (e.g. a DB connection error), the
exception propagates through `finally` and then hits line 812 as an `UnboundLocalError`,
masking the original error and producing a confusing 500 traceback. Similarly, if the
`HTTPException(404)` raised at line 764 (row2 is None) propagates out, `outcome` is
bound (`"first_write"`) but `updated_row` is not, causing `UnboundLocalError` at
line 815.

**Fix:** Initialize both before the `try` block:

```python
outcome: str = ""
updated_row: ProposalRow | None = None

sf3, engine3 = _get_session_factory(user_id)
try:
    async with sf3() as session2, session2.begin():
        ...
finally:
    if engine3 is not None:
        await engine3.dispose()
```

---

### WR-02: `require_session` applied twice on `/approvals/poll`

**File:** `src/gekko/dashboard/routes.py:289–292`

**Issue:** The `/approvals/poll` route is declared on `router` (line 116), which already
has `dependencies=[Depends(require_session)]` at the router level. The route handler
also declares `user_id: str = Depends(require_session)` explicitly:

```python
router = APIRouter(dependencies=[Depends(require_session)])  # line 116

@router.get("/approvals/poll", ...)
async def approvals_poll(
    request: Request,
    user_id: str = Depends(require_session),   # redundant — already on router
) -> HTMLResponse:
```

FastAPI applies both. Each call to `require_session` reads `request.session` and calls
`get_settings()` — two redundant DB/settings lookups per poll request. This also
creates inconsistency: most other routes on this router do declare the explicit
`Depends(require_session)` to capture the `user_id` return value (that's intentional
for scoping queries). The poll route is fine; the router-level dependency is the
redundant one. However, the redundancy is non-obvious and has caused confusion about
which layer enforces auth.

**Fix:** Keep the explicit `Depends(require_session)` on the route signature (needed to
obtain `user_id`). The router-level dependency provides defense-in-depth and the
double invocation is benign. Add a comment clarifying the intentional layering:

```python
# router already enforces require_session; explicit Depends here captures user_id.
async def approvals_poll(
    request: Request,
    user_id: str = Depends(require_session),
) -> HTMLResponse:
```

---

## Info

### IN-01: `actions.py` `__all__` exports private names

**File:** `src/gekko/approval/actions.py:27`

**Issue:** `__all__ = ("_check_edit_size_caps", "_drift_check")` lists names with
leading underscores, which conventionally signal private helpers not intended for
external `from module import *` use. This is the only module in the reviewed set that
exports underscore-prefixed names via `__all__`.

**Fix:** If these helpers are only called by `slack_handler.py` and `routes.py` via
explicit imports (not `*`), remove them from `__all__` or drop `__all__` from this
module entirely. Alternatively, if the names are intentionally semi-public (e.g. for
testing), rename without the leading underscore and update callers.

---

### IN-02: HTMX poll fires on container that contains interactive buttons — mid-flight dedup race

**File:** `src/gekko/dashboard/templates/approvals_index.html.j2:19–25`

**Issue:** The 30-second poll replaces `#proposals-list-container`'s `innerHTML` with
a fresh fragment. If the operator is in the process of clicking Approve/Reject on a
card at the moment the poll fires, the DOM swap destroys the card (and the HTMX
in-flight request from the button). The button click's HTMX request may complete after
the swap, leaving the server in APPROVED state but the DOM showing a stale PENDING
card (or an empty slot if the poll response omitted the now-APPROVED proposal from the
PENDING/AWAITING query). The result is a confusing UI with no action feedback.

This is not a data-correctness issue (dedup prevents double-execution), but it can
produce misleading UX.

**Fix:** Add `hx-sync="#proposals-list-container:replace"` or use HTMX's `hx-swap-oob`
to update the proposal list only when no other requests are in-flight on the container.
Alternatively, poll with `hx-trigger="every 30s [!htmx.closest('#proposals-list-container').classList.contains('htmx-request')]"` to suppress the poll while a card action is in progress.

---

_Reviewed: 2026-06-19T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
