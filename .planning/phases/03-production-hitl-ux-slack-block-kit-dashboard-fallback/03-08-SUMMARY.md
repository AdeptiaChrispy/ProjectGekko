---
phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
plan: "08"
subsystem: auth
tags: [fastapi, session, require_session, auth-gate, security, CR-01, HITL-06]

# Dependency graph
requires:
  - phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
    plan: "05"
    provides: require_session dependency function + login routes + session middleware (D-57/D-58)
  - phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback
    plan: "06"
    provides: live-confirm, promote-to-live, kill-switch routes (the ungated endpoints this plan gates)
provides:
  - "Fail-closed router-level auth: APIRouter(dependencies=[Depends(require_session)]) gates all dashboard routes"
  - "public_router for /login (GET/POST) and /healthz — explicitly exempt from session requirement"
  - "Session-derived user_id threading: all gated routes use Depends(require_session) for per-request identity"
  - "Regression test suite: 14 tests asserting 302->login for 8 safety-critical routes + public-route positive controls"
affects:
  - phase 03 verification (CR-01 BLOCKER closed)
  - future dashboard routes (inherit router-level auth automatically)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Two-router pattern: public_router (no auth) + router (require_session) — FastAPI router-level dependencies are additive, not overridable per-route"
    - "Fail-closed auth: router-level Depends(require_session) is the default gate; public routes live on a separate router, not an exemption override"
    - "Session identity threading: route handlers declare user_id: str = Depends(require_session) to shadow the router-level dep and receive the value"

key-files:
  created:
    - tests/unit/test_dashboard_auth_safety_routes.py
  modified:
    - src/gekko/dashboard/routes.py
    - src/gekko/dashboard/app.py

key-decisions:
  - "Two-router pattern (public_router + router) instead of per-route dependencies=[] override — FastAPI merges router-level deps additively; dependencies=[] on a route means no additional deps, not no deps at all"
  - "require_session moved before router declaration — function must exist at module-load time when APIRouter(dependencies=[Depends(require_session)]) is constructed"
  - "GET / intentionally gated (fail-closed) — unauthenticated callers hitting root receive 302 to /login; no reason to expose root publicly"
  - "Session-derived user_id replaces settings.gekko_user_id in all gated route handlers — closes the identity drift risk where the session user could differ from the configured user_id"

patterns-established:
  - "New dashboard routes: declare on `router` (not `public_router`) to inherit auth automatically; no boilerplate needed"
  - "Public routes: declare on `public_router` and import both routers in app.py via app.include_router(public_router) + app.include_router(router)"

requirements-completed:
  - DASH-04

# Metrics
duration: 45min
completed: 2026-06-18
---

# Phase 03 Plan 08: Dashboard Auth Safety Routes Summary

**Router-level fail-closed auth applied to all 8 safety-critical dashboard routes via APIRouter(dependencies=[Depends(require_session)]), closing CR-01 BLOCKER**

## Performance

- **Duration:** ~45 min
- **Started:** 2026-06-18T12:25:40Z
- **Completed:** 2026-06-18T13:10:00Z
- **Tasks:** 2
- **Files modified:** 3 (routes.py, app.py) + 1 created (test file)

## Accomplishments

- Closed CR-01 BLOCKER: POST /live-confirm, GET /live-confirm, POST /kill, POST /unkill, GET /kill/state, POST /strategies/{name}/promote-to-live, POST /trigger/{name}, and all GET/POST /strategies* routes now require a valid session (return 302 to /login for unauthenticated callers)
- Replaced per-request `settings.gekko_user_id` identity lookups in all gated route handlers with `user_id: str = Depends(require_session)` — session identity is now the canonical per-request principal
- 14-test regression suite covering all safety-critical routes + public-route positive controls (/login, /healthz)
- HITL-06 dual-channel guarantee strengthened: the dashboard second channel now requires distinct authentication, making it non-trivially bypassable

## Task Commits

1. **Task 1 + Task 2: Apply router-level require_session + regression tests** - `f2a907c` (fix)

## Files Created/Modified

- `src/gekko/dashboard/routes.py` — Introduced `public_router` (no auth), moved `require_session` before router, changed main `router = APIRouter(dependencies=[Depends(require_session)])`, moved /login and /healthz to `public_router`, threaded `user_id: str = Depends(require_session)` into 10 route handlers, removed `settings.gekko_user_id` from all gated handlers
- `src/gekko/dashboard/app.py` — Added `public_router` import, added `app.include_router(public_router)` before `app.include_router(router)` in `create_app()`
- `tests/unit/test_dashboard_auth_safety_routes.py` — 14 tests: parametrized unauthenticated-redirects suite (11 safety-critical routes), test_login_page_is_public, test_healthz_is_public, test_authenticated_approvals_passes_auth

## Decisions Made

- **Two-router pattern instead of per-route dependencies=[]**: FastAPI's `dependencies=[]` on a per-route decorator is additive (no additional deps on this route), not an override of router-level deps. Discovered this during TDD green phase when GET /login still returned 302. Solution: `public_router = APIRouter()` for exempt routes, `router = APIRouter(dependencies=[Depends(require_session)])` for everything else.
- **require_session relocated above router declaration**: The `APIRouter(dependencies=[Depends(require_session)])` constructor reference must resolve at module-load time. Moved the function above the router instantiation to prevent NameError.
- **GET / intentionally gated**: The root redirect route is left on the auth-gated `router` — an unauthenticated visitor hitting `/` receives 302 to `/login`, which is the correct fail-closed behavior.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] FastAPI dependencies=[] does not override router-level deps**
- **Found during:** Task 1 green phase (TDD — tests still failing after initial implementation attempt)
- **Issue:** Plan specified `@router.get("/login", dependencies=[])` to exempt public routes. FastAPI merges dependencies additively; `dependencies=[]` means "no additional deps" not "ignore router deps". GET /login still returned 302 even with `dependencies=[]`.
- **Fix:** Introduced `public_router = APIRouter()` (no auth dependency); moved /login GET/POST and /healthz onto `public_router`. Updated app.py to register both routers. This is the correct FastAPI pattern for per-route auth exemption.
- **Files modified:** src/gekko/dashboard/routes.py, src/gekko/dashboard/app.py
- **Verification:** test_login_page_is_public and test_healthz_is_public pass (200); all 11 safety-critical route tests still pass (302)
- **Committed in:** f2a907c (combined with all Task 1+2 changes)

---

**Total deviations:** 1 auto-fixed (Rule 1 - bug in plan's FastAPI assumption)
**Impact on plan:** The two-router pattern achieves exactly the same security outcome the plan specified. Plan's structural intent (fail-closed router + public exemptions for login/healthz) is fully realized. No scope creep.

## Issues Encountered

None beyond the deviation documented above.

## Known Stubs

None — this plan introduces no UI rendering or data-wiring stubs.

## Threat Flags

No new threat surface introduced. All mitigations from the plan's threat register were applied:
- T-03-08-01 through T-03-08-06: router-level Depends(require_session) gates all listed endpoints
- T-03-08-07: Starlette SessionMiddleware HMAC-SHA256 signing unchanged (D-58)
- T-03-08-SC: No new package installs

## Self-Check

Files exist:
- `src/gekko/dashboard/routes.py` — FOUND (modified)
- `src/gekko/dashboard/app.py` — FOUND (modified)
- `tests/unit/test_dashboard_auth_safety_routes.py` — FOUND (created)
- `.planning/phases/03-production-hitl-ux-slack-block-kit-dashboard-fallback/03-08-SUMMARY.md` — FOUND (this file)

Commits exist: f2a907c (verified via git log)

Test results: 14 passed in test_dashboard_auth_safety_routes.py; 26 passed in combined dashboard unit suite; no regressions in existing test_dashboard_login, test_dashboard_approvals, test_dashboard_edit_size, test_dashboard_middleware_order

## Next Phase Readiness

- CR-01 BLOCKER is closed; the HITL-06 dual-channel guarantee now holds end-to-end
- New dashboard routes added in future plans will automatically inherit auth via the `router` object — no boilerplate needed
- Public routes (liveness probes, new auth endpoints) must be declared on `public_router` and imported in app.py

---
*Phase: 03-production-hitl-ux-slack-block-kit-dashboard-fallback*
*Completed: 2026-06-18*
