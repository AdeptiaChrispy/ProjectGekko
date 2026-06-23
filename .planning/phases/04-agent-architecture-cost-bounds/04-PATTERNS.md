# Phase 4: Agent Architecture & Cost Bounds — Pattern Map

**Mapped:** 2026-06-23
**Files analyzed:** 11 (7 new, 4 modified)
**Analogs found:** 11 / 11

---

## File Classification

| New / Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---------------------|------|-----------|----------------|---------------|
| `src/gekko/agent/pricing.py` | utility (constants + formula) | transform | `src/gekko/agent/budget.py` (small constants module + dataclass) | role-match |
| `src/gekko/agent/cost_ceiling.py` | utility (deterministic guard) | request-response | `src/gekko/approval/quiet_hours.py` (deterministic pre-LLM gate) | exact |
| `src/gekko/agent/runtime.py` (modify) | orchestrator | request-response | self — existing quiet-hours gate + `_run_researcher` loop | exact |
| `src/gekko/dashboard/routes.py` (modify) | route / controller | CRUD | self — `settings_get` / `settings_post` (lines 1074-1200) | exact |
| `src/gekko/dashboard/templates/spend.html.j2` | template | request-response | `src/gekko/dashboard/templates/settings.html.j2` | role-match |
| `src/gekko/dashboard/templates/settings.html.j2` (modify) | template | request-response | self — existing `<fieldset>` + `<input>` form pattern | exact |
| `migrations/versions/0005_p4_cost_ceiling.py` | migration | batch | `migrations/versions/0004_p3_hitl_ux.py` | exact |
| `src/gekko/db/models.py` (modify `User`) | model | CRUD | self — existing Phase-3 user column additions (lines 182-192) | exact |
| `src/gekko/agent/tools/finnhub_news.py` — suspicious-content scan site | utility | event-driven | `src/gekko/agent/tools/web_fetch.py` lines 118-130 (`<untrusted_content>` wrapping) | exact |
| `tests/unit/test_decision_prompt_isolation.py` (add gate) | test (AST gate) | — | self — existing `test_directory_wide_ast_walk_no_raw_transcript_references_in_decision_path` | exact |
| `src/gekko/scheduler/jobs.py` (modify) | scheduler | event-driven | self — `schedule_strategy_daily` + `reschedule_job` | exact |

---

## Pattern Assignments

---

### `src/gekko/agent/pricing.py` (utility, transform)

**Analog:** `src/gekko/agent/budget.py`

**Why:** `budget.py` is the canonical "small agent module with typed constants + a dataclass that does arithmetic" pattern. `pricing.py` is the same shape: a few `Decimal` constants and one formula function, no LLM dependency.

**Imports pattern** (`budget.py` lines 30-37):
```python
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from gekko.core.errors import BudgetExceeded
from gekko.logging_config import get_logger
```

**For pricing.py, replace with:**
```python
from __future__ import annotations

from decimal import Decimal
```

**Core constants pattern** (`budget.py` lines 62-65 — soft-cap constants with inline comments):
```python
soft_max_calls: int = 12
soft_max_tokens: int = 8000
soft_max_seconds: float = 60.0
```

**Mirror this shape — declare each constant as a module-level `Decimal` with a source comment:**
```python
# Per Anthropic pricing page, verified 2026-06-23
# Source: platform.claude.com/docs/en/about-claude/pricing
SONNET_INPUT_PER_MTOK  = Decimal("3.00")   # $/MTok
SONNET_OUTPUT_PER_MTOK = Decimal("15.00")  # $/MTok
HAIKU_INPUT_PER_MTOK   = Decimal("1.00")   # $/MTok
HAIKU_OUTPUT_PER_MTOK  = Decimal("5.00")   # $/MTok

DEFAULT_DAILY_CEILING_USD = Decimal("5.00")   # D-02 default; override in Settings
```

**Formula function:** use `Decimal` throughout; `total_cost_usd` from `ResultMessage` is preferred over this formula (formula = fallback for `None`):
```python
def tokens_to_usd(
    input_tokens: int,
    output_tokens: int,
    *,
    model: str = "sonnet",
) -> Decimal:
    """Fallback formula when ResultMessage.total_cost_usd is None."""
    ...
```

**`__all__` pattern** (`budget.py` line 129):
```python
__all__: tuple[str, ...] = ("BudgetTracker",)
```

---

### `src/gekko/agent/cost_ceiling.py` (utility/guard, request-response)

**Analog:** `src/gekko/approval/quiet_hours.py`

**Why:** `quiet_hours.py` is a deterministic pre-LLM gate that (1) reads the DB for user config, (2) does pure Python arithmetic, and (3) returns a bool the orchestrator acts on — with no LLM calls anywhere. `cost_ceiling.py` is the same shape but returns a `CeilingCheck` dataclass instead of bool.

**Module-level session factory seam** (`quiet_hours.py` lines 49-64):
```python
def _get_session_factory(user_id: str):  # type: ignore[return]
    """Build a session factory + owning engine for ``user_id``.
    Mirrors the same indirection used by :mod:`gekko.execution.executor`
    so tests have a single seam to monkeypatch (PATTERNS §2d).
    """
    from gekko.config import get_settings
    from gekko.db.engine import get_async_engine
    from gekko.db.session import make_session_factory
    from gekko.vault.passphrase import get_passphrase as _get_passphrase

    settings = get_settings()
    engine = get_async_engine(settings.db_path_for(user_id), _get_passphrase())
    return make_session_factory(engine), engine
```

**Timezone-boundary computation** (`quiet_hours.py` lines 146-178):
```python
try:
    tz = ZoneInfo(tz_name)
except ZoneInfoNotFoundError as exc:
    msg = f"Invalid IANA timezone {tz_name!r} for user {user_id}"
    raise ValueError(msg) from exc

local_now = now.astimezone(tz)
local_time = local_now.time()
```

**Copy this pattern for "today's start in user's tz":**
```python
from datetime import UTC, datetime, date
from zoneinfo import ZoneInfo

tz = ZoneInfo(user.timezone or "America/New_York")
now_local = datetime.now(UTC).astimezone(tz)
today_start_local = datetime(now_local.year, now_local.month, now_local.day, tzinfo=tz)
today_start_utc_str = today_start_local.astimezone(UTC).isoformat()
```

**Return type:** model after `BudgetTracker`'s dataclass shape — but as a dedicated result dataclass:
```python
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

@dataclass
class CeilingCheck:
    action: Literal["allow", "degrade", "halt"]
    current_spend: Decimal
    ceiling: Decimal
    pct: Decimal           # 0-100
    just_crossed_80: bool  # True on first cycle that tips over 80%
    just_crossed_100: bool # True on first cycle that tips over 100%
```

**Cost-event row fetch + Python-side Decimal sum** (RESEARCH §RQ-3 — do NOT use SQL SUM on JSON field):
```python
import json
rows = await session.execute(
    select(Event.payload_json, Event.strategy_id)
    .where(
        Event.user_id == user_id,
        Event.event_type == "llm_cost",
        Event.ts >= today_start_utc_str,
    )
).all()
total = sum(Decimal(json.loads(r.payload_json)["cost_usd"]) for r in rows)
```

**"One DM" tracking:** read `user.cost_alert_80_sent_date` and `user.cost_alert_100_sent_date` (new columns from migration 0005); compare against `now_local.date().isoformat()`. If they match, `just_crossed_X = False`.

---

### `src/gekko/agent/runtime.py` — ceiling guard insertion (modify)

**Analog:** self — existing quiet-hours gate (`runtime.py` lines 471-493)

**Quiet-hours gate to mirror** (`runtime.py` lines 476-493):
```python
if source == "schedule":
    from gekko.approval.quiet_hours import _resolve_quiet_hours

    _in_window = await _resolve_quiet_hours(
        user_id, datetime.now(UTC), strategy_name=strategy_name
    )
    if _in_window:
        log.info(
            "agent.cycle.skipped_quiet_hours",
            user_id=user_id,
            strategy_name=strategy_name,
            source=source,
        )
        return {
            "run_id": run_id,
            "outcome": "skipped_quiet_hours",
            "source": source,
        }
```

**Insert the cost-ceiling guard immediately AFTER this block, BEFORE `budget = BudgetTracker()` (line 506):**
```python
# ---- Cost-ceiling gate (COST-01 / D-07) ----------------------------------
# ALL trigger sources (not just "schedule") respect the ceiling — a
# manual run also deducts from the daily pool. The halt is absolute.
from gekko.agent.cost_ceiling import check_cost_ceiling

_ceiling = await check_cost_ceiling(session_factory=session_factory, user_id=user_id)
if _ceiling.action == "halt":
    log.info(
        "agent.cycle.skipped_cost_halt",
        user_id=user_id,
        strategy_name=strategy_name,
        source=source,
        spend_usd=str(_ceiling.current_spend),
        ceiling_usd=str(_ceiling.ceiling),
    )
    # D-08: one Slack DM at 100% (just_crossed_100 = False on repeats)
    if _ceiling.just_crossed_100:
        # fire Slack DM via bypass category
        ...
    return {
        "run_id": run_id,
        "outcome": "skipped_cost_halt",
        "source": source,
    }
```

**`ResultMessage` capture pattern for cost ledger** — add to the `async for` loops in both `_run_researcher` and `_run_decision` (`runtime.py` lines 303-311 and 350-364). Current loop:
```python
# _run_researcher, lines 303-311 (current):
async for msg in query(prompt=user_prompt, options=options):
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                accumulated_text += block.text
```

**Extend to capture ResultMessage (add before the loop, capture inside):**
```python
from claude_agent_sdk.types import ResultMessage as SDKResultMessage

result_msg: SDKResultMessage | None = None
input_tokens = 0
output_tokens = 0

async for msg in query(prompt=user_prompt, options=options):
    if isinstance(msg, SDKResultMessage):
        result_msg = msg
    elif isinstance(msg, AssistantMessage):
        if msg.usage:
            input_tokens += msg.usage.get("input_tokens", 0)
            output_tokens += msg.usage.get("output_tokens", 0)
        for block in msg.content:
            if isinstance(block, TextBlock):
                accumulated_text += block.text

cost_usd = Decimal(str(result_msg.total_cost_usd or 0.0)) if result_msg else Decimal("0")
```

**Cost ledger write — copy `append_event` caller pattern** from `runtime.py` lines 395-423 (`_persist_proposal_rejected_event`):
```python
async with session_factory() as session, session.begin():
    await append_event(
        session,
        user_id=user_id,
        strategy_id=strategy_db_id,
        event_type="llm_cost",
        payload=normalize_decimals({
            "run_id": run_id,
            "strategy_name": strategy_name,
            "model": "sonnet",
            "call_type": "researcher",  # or "decision" / "triage"
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
        }),
    )
```

**Slack bypass for cost alerts** — copy `_BYPASS_CATEGORIES` pattern from `src/gekko/execution/executor.py` lines 249-254:
```python
_BYPASS_CATEGORIES = frozenset({"kill_active", "executor_error", "first_live_fill"})
if category in _BYPASS_CATEGORIES:
    await _send_slack_dm(user_id, text)
```
Add `"cost_alert"` to `_BYPASS_CATEGORIES` in `executor.py` (or pass `bypass=True` to the send function — see RESEARCH open question 3).

---

### `src/gekko/dashboard/routes.py` — `settings_get` / `settings_post` extension + new `/spend` route (modify)

**Analog:** self — `settings_get` (lines 1074-1106) and `settings_post` (lines 1109-1200)

**`settings_get` DB load pattern** (lines 1083-1093):
```python
sf, engine = _get_session_factory(user_id)
try:
    async with sf() as session:
        user = (
            await session.execute(
                select(UserRow).where(UserRow.user_id == user_id)
            )
        ).scalar_one_or_none()
finally:
    if engine is not None:
        await engine.dispose()
```

**`settings_post` form field binding pattern** (lines 1109-1114):
```python
@router.post("/settings", response_class=HTMLResponse)
async def settings_post(
    request: Request,
    timezone: str = Form(""),
    quiet_hours_start: str = Form(""),
    quiet_hours_end: str = Form(""),
    user_id: str = Depends(require_session),
) -> HTMLResponse:
```

**Extend `settings_post` with ceiling field — same `Form("")` pattern:**
```python
daily_cost_ceiling_usd: str = Form("5.00"),   # D-02 default shown in form
```

**User row update pattern** (lines 1167-1172):
```python
if user is not None:
    if timezone:
        user.timezone = timezone
    user.quiet_hours_start = quiet_hours_start or None
    user.quiet_hours_end = quiet_hours_end or None
await session.flush()
```

**New `/spend` route** — copy the `approvals_poll` pattern (lines 308-348) for the DB query + template render skeleton:
```python
@router.get("/spend", response_class=HTMLResponse)
async def spend_get(
    request: Request,
    user_id: str = Depends(require_session),
) -> HTMLResponse:
    """GET /spend — today's LLM spend vs ceiling + per-strategy + 7-day history."""
    from gekko.db.models import Event as EventRow, Strategy as StrategyRow
    import json

    sf, engine = _get_session_factory(user_id)
    try:
        async with sf() as session:
            # load user for ceiling + timezone
            # compute today_start_utc_str (see cost_ceiling.py pattern)
            # fetch llm_cost events for today
            # sum total and per-strategy
            # fetch 7-day window events
            ...
    finally:
        if engine is not None:
            await engine.dispose()

    return templates.TemplateResponse(
        request,
        "spend.html.j2",
        {"today_total": ..., "ceiling": ..., "by_strategy": ..., "history": ...},
    )
```

---

### `src/gekko/dashboard/templates/spend.html.j2` (new)

**Analog:** `src/gekko/dashboard/templates/settings.html.j2`

**Base extension pattern** (settings.html.j2 lines 12-13):
```jinja
{% extends "base.html.j2" %}

{% block content %}
<div class="container">
  <h1>Settings</h1>
```

**Copy this exactly — change heading to "Spend" and add the nav link to base.html.j2.**

**Success/error feedback pattern** (settings.html.j2 lines 18-28):
```jinja
{% if success %}
<div role="status" class="chip-live" style="margin-bottom: 1rem; display: inline-block;">
  Settings saved.
</div>
{% endif %}

{% if error %}
<div class="login-error" role="alert" aria-live="assertive">
  {{ error }}
</div>
{% endif %}
```

**Data display pattern — iterate a list:** copy `approvals_index.html.j2` list pattern for the per-strategy rows. The spend page is read-only (no form submission), so no `<form>` element needed; just structured `<div>` / `<table>` blocks.

**No HTMX polling needed** on this page (D-11 is a read view, not real-time). Use a standard page render like `settings.html.j2`.

---

### `src/gekko/dashboard/templates/settings.html.j2` — ceiling field addition (modify)

**Analog:** self — existing `<fieldset>` block (lines 31-65)

**New fieldset pattern to add inside the existing `<form>`:**
```jinja
<fieldset>
  <legend>Daily LLM Cost Ceiling</legend>
  <p class="form-help">
    The agent halts all LLM calls once this daily limit is reached.
    At 80% of this ceiling the agent enters degraded mode (slower cadence,
    cheaper triage gate). Resets at midnight in your configured timezone.
  </p>

  <label for="daily_cost_ceiling_usd">Daily ceiling (USD)</label>
  <input type="number"
         id="daily_cost_ceiling_usd"
         name="daily_cost_ceiling_usd"
         step="0.01"
         min="0.50"
         value="{{ user.daily_cost_ceiling_usd if user and user.daily_cost_ceiling_usd else '5.00' }}">

  <p class="form-help">Default: $5.00/day.</p>
</fieldset>
```

**No new `<form>` — add the fieldset INSIDE the existing `<form method="POST" action="/settings">` so all settings save together in one POST.**

---

### `migrations/versions/0005_p4_cost_ceiling.py` (new)

**Analog:** `migrations/versions/0004_p3_hitl_ux.py` (exact pattern)

**File header / revision wiring** (`0004_p3_hitl_ux.py` lines 1-45):
```python
"""p4_cost_ceiling — Phase 4 schema substrate

Revision ID: 0005_p4_cost_ceiling
Revises: 0004_p3_hitl_ux
Create Date: 2026-06-23 00:00:00
...
"""
from __future__ import annotations
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_p4_cost_ceiling"
down_revision: str | None = "0004_p3_hitl_ux"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
```

**Frozen vocabulary pattern** (`0004_p3_hitl_ux.py` lines 52-127):
```python
# Frozen vocabularies — kept LOCAL to the migration (Plan 01-03 convention).
# Copy ALL existing event_types from _EVENT_TYPES in models.py, then append
# the two new Phase-4 types at the end.
_FROZEN_EVENT_TYPES_PRE = (
    "decision", "proposal", "approval", "rejection",
    "order_submitted", "fill", "kill_switch", "cap_rejection",
    "credentials_added", "live_mode_promoted", "live_mode_demoted",
    "first_live_trade_confirmed", "error",
    "expiration", "dedup_click", "edit_size", "daily_pnl",
)
_FROZEN_EVENT_TYPES_POST = _FROZEN_EVENT_TYPES_PRE + ("llm_cost", "suspicious_content")
```

**`upgrade()` — users columns + events CHECK** (mirror `0004_p3_hitl_ux.py` lines 182-210):
```python
def upgrade() -> None:
    # 1. users — add daily cost ceiling + alert-sent-date columns.
    with op.batch_alter_table("users") as bop:
        bop.add_column(sa.Column(
            "daily_cost_ceiling_usd", sa.String(), nullable=True,
            server_default="'5.00'",
        ))
        bop.add_column(sa.Column("cost_alert_80_sent_date", sa.String(), nullable=True))
        bop.add_column(sa.Column("cost_alert_100_sent_date", sa.String(), nullable=True))

    # 2. events — extend ck_event_type with llm_cost + suspicious_content.
    with op.batch_alter_table("events") as bop:
        bop.drop_constraint("ck_event_type", type_="check")
        bop.create_check_constraint(
            "ck_event_type",
            _in_check("event_type", _FROZEN_EVENT_TYPES_POST),
        )
```

**`downgrade()`** — reverse in the same pattern (events CHECK first, then users columns):
```python
def downgrade() -> None:
    with op.batch_alter_table("events") as bop:
        bop.drop_constraint("ck_event_type", type_="check")
        bop.create_check_constraint(
            "ck_event_type",
            _in_check("event_type", _FROZEN_EVENT_TYPES_PRE),
        )
    with op.batch_alter_table("users") as bop:
        bop.drop_column("cost_alert_100_sent_date")
        bop.drop_column("cost_alert_80_sent_date")
        bop.drop_column("daily_cost_ceiling_usd")
```

**`_in_check` helper** — copy verbatim from `0004_p3_hitl_ux.py` line 125-126:
```python
def _in_check(column: str, allowed: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in allowed)})"
```

---

### `src/gekko/db/models.py` — `User` model extension (modify)

**Analog:** self — Phase-3 quiet-hours column additions (`models.py` lines 183-192)

**Phase-3 pattern to mirror** (lines 183-192):
```python
# Phase-3 / D-47 + D-49 quiet-hours + timezone columns.
quiet_hours_start: Mapped[str | None] = mapped_column(String, nullable=True)
quiet_hours_end: Mapped[str | None] = mapped_column(String, nullable=True)
timezone: Mapped[str | None] = mapped_column(String, nullable=True)
```

**Add the three Phase-4 columns immediately after (same comment-header style):**
```python
# Phase-4 / D-02 + D-12 daily cost ceiling + alert-sent-date columns.
#
# ``daily_cost_ceiling_usd`` stores the configurable per-day USD ceiling as
# a TEXT string (consistent with money-as-TEXT pattern). NULL defaults to
# the DEFAULT_DAILY_CEILING_USD constant at read time.
# ``cost_alert_80_sent_date`` and ``cost_alert_100_sent_date`` store the
# ISO date (YYYY-MM-DD, in the user's timezone) when the 80%/100% DM was
# last sent. Guard compares this against today's local date to enforce
# the "one DM per day" rule (D-06/D-08). NULL = never sent.
daily_cost_ceiling_usd: Mapped[str | None] = mapped_column(String, nullable=True)
cost_alert_80_sent_date: Mapped[str | None] = mapped_column(String, nullable=True)
cost_alert_100_sent_date: Mapped[str | None] = mapped_column(String, nullable=True)
```

**`_EVENT_TYPES` extension** (models.py lines 94-112):
```python
_EVENT_TYPES: tuple[str, ...] = (
    ...
    "daily_pnl",
    # Phase-4 additions:
    "llm_cost",
    "suspicious_content",
)
```

---

### Suspicious-content detector — in `_run_researcher` (new logic in runtime.py)

**Analog:** `src/gekko/agent/tools/web_fetch.py` lines 118-130 (`<untrusted_content>` wrapping pattern) + existing `append_event` callers in `runtime.py` lines 395-423

**Regex pattern** (RESEARCH §RQ-6):
```python
import re

_INJECTION_PATTERNS: re.Pattern[str] = re.compile(
    r"SYSTEM\s*:|OVERRIDE\s*:|ignore\s+previous\s+instructions|"
    r"disregard\s+your\s+instructions|forget\s+your\s+instructions",
    re.IGNORECASE,
)
```

**Scan site:** after `brief = ResearchBrief.model_validate_json(brief_json)` returns, BEFORE `_run_decision` is called. Mirrors how `web_fetch.py` wraps content after it fetches — the scan happens at the trust boundary, not inside the LLM call:

```python
# Suspicious-content scan (SC-2 gap closure).
for evidence in brief.evidence:
    if evidence.quote_text and _INJECTION_PATTERNS.search(evidence.quote_text):
        async with session_factory() as _sc_session, _sc_session.begin():
            await append_event(
                _sc_session,
                user_id=user_id,
                strategy_id=strategy_db_id,
                event_type="suspicious_content",
                payload=normalize_decimals({
                    "run_id": run_id,
                    "source_type": evidence.source_type,
                    "source_url": str(evidence.source_url),
                    "pattern_matched": True,
                }),
            )
```

**`append_event` call shape** — copy exactly from `_persist_proposal_rejected_event` (`runtime.py` lines 395-423): same `async with session_factory() as session, session.begin()` wrapper + `normalize_decimals({...})` on the payload.

---

### `src/gekko/scheduler/jobs.py` — reschedule_job for cadence ×2 (modify)

**Analog:** self — `schedule_strategy_daily` (lines 108-155)

**Existing `add_job` call** (lines 136-147):
```python
scheduler.add_job(
    "gekko.agent.runtime:trigger_strategy_run",
    CronTrigger(hour=hh, minute=mm, timezone=tz),
    kwargs={
        "user_id": user_id,
        "strategy_name": strategy_name,
        "source": "schedule",
    },
    id=job_id,
    replace_existing=True,
)
```

**`reschedule_job` to add** (APScheduler 3.x; confirmed installed at 3.11.2 per RESEARCH §RQ-5):
```python
def reschedule_strategy_degraded(
    scheduler: AsyncIOScheduler,
    *,
    user_id: str,
    strategy_name: str,
    original_schedule_time: str,
) -> str:
    """Double the interval between runs when the ceiling is in degraded mode (D-04)."""
    hh, mm, tz = _parse_schedule_time(original_schedule_time)
    # Approximate cadence ×2: add 12h to the original hour (wraps mod 24).
    degraded_hh = (hh + 12) % 24
    job_id = f"run-{user_id}-{strategy_name}"
    scheduler.reschedule_job(
        job_id,
        trigger=CronTrigger(hour=degraded_hh, minute=mm, timezone=tz),
    )
    log.info(
        "scheduler.job.degraded_cadence",
        job_id=job_id,
        original_hh=hh,
        degraded_hh=degraded_hh,
    )
    return job_id
```

**Restore pattern:** call `schedule_strategy_daily(scheduler, ...)` (existing function — `replace_existing=True` handles the update):
```python
def restore_strategy_normal_cadence(
    scheduler: AsyncIOScheduler,
    *,
    user_id: str,
    strategy_name: str,
    schedule_time: str,
) -> str:
    """Restore normal cadence after ceiling resets (D-03/D-09)."""
    return schedule_strategy_daily(
        scheduler,
        user_id=user_id,
        strategy_name=strategy_name,
        schedule_time=schedule_time,
    )
```

---

### `tests/unit/test_decision_prompt_isolation.py` — D-05 AST gate addition (modify)

**Analog:** self — `test_directory_wide_ast_walk_no_raw_transcript_references_in_decision_path` (lines 103-156)

**Existing AST walk infrastructure** (lines 76-95):
```python
def _agent_py_files() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[2]
    agent_root = repo_root / "src" / "gekko" / "agent"
    assert agent_root.exists(), f"agent root not found at {agent_root}"
    return sorted(agent_root.rglob("*.py"))

def _is_decision_fn_name(name: str) -> bool:
    for pat in _DECISION_FN_NAME_PATTERNS:
        if pat.endswith("_"):
            if name.startswith(pat):
                return True
        else:
            if name == pat:
                return True
    return False
```

**New test to add — "Decision never Haiku" (D-05):**
```python
def test_decision_never_haiku_model() -> None:
    """D-05 AST gate: model='haiku' MUST NOT appear in _run_decision or
    build_decision_prompt. Real-money trade decisions may not use the
    cheaper model. Haiku is triage-only.
    """
    violations: list[str] = []

    for py_file in _agent_py_files():
        src = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src, filename=str(py_file))
        except SyntaxError as exc:
            pytest.fail(f"Failed to parse {py_file}: {exc}")

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not _is_decision_fn_name(node.name):
                continue
            # Walk the body looking for model="haiku" keyword argument
            for sub in ast.walk(node):
                if isinstance(sub, ast.keyword):
                    if (
                        sub.arg == "model"
                        and isinstance(sub.value, ast.Constant)
                        and sub.value.value == "haiku"
                    ):
                        violations.append(
                            f"{py_file.name}:{sub.value.lineno}: "
                            f"function {node.name!r} passes model='haiku' "
                            f"(D-05 violation — Decision must never use Haiku)"
                        )

    assert not violations, (
        "D-05 invariant broken — Decision-path function uses Haiku model:\n  "
        + "\n  ".join(violations)
    )
```

---

## Shared Patterns

### `append_event` + `normalize_decimals` (cost ledger + suspicious-content writes)

**Source:** `src/gekko/audit/log.py` + `src/gekko/audit/canonical.py`
**Apply to:** `cost_ceiling.py`, `runtime.py` (cost-ledger write, suspicious-content write)

```python
# Canonical caller pattern (from runtime.py lines 395-423):
async with session_factory() as session, session.begin():
    await append_event(
        session,
        user_id=user_id,
        strategy_id=strategy_db_id,   # nullable OK for global events
        event_type="llm_cost",         # or "suspicious_content"
        payload=normalize_decimals({   # MUST call normalize_decimals on any Decimal values
            ...
        }),
    )
```

Key rule: `normalize_decimals()` strips trailing zeros so `Decimal("5.00")` and `Decimal("5")` hash identically. Always call it before `append_event`.

### `Decimal(str(float_value))` conversion

**Source:** `src/gekko/audit/canonical.py` docstring ("callers handling money MUST pass through normalize_decimals")
**Apply to:** all SDK `ResultMessage.total_cost_usd` extractions

```python
# total_cost_usd is float | None from the SDK.
cost_usd = Decimal(str(result_msg.total_cost_usd or 0.0)) if result_msg else Decimal("0")
```

Never use `Decimal(float_value)` directly — binary float → Decimal produces irrational precision. Always go through `str()` first.

### `_get_session_factory` test seam

**Source:** `src/gekko/approval/quiet_hours.py` lines 49-64
**Apply to:** `cost_ceiling.py`

Every module that opens its own DB session for a deterministic guard must expose `_get_session_factory(user_id)` as a monkeypatch seam. Tests inject a fixture-scoped factory; production code calls the real one. This is "PATTERNS §2d" in the existing codebase.

### Router auth pattern

**Source:** `src/gekko/dashboard/routes.py` lines 134-135
**Apply to:** new `/spend` route

```python
router = APIRouter(dependencies=[Depends(require_session)])

@router.get("/spend", response_class=HTMLResponse)
async def spend_get(
    request: Request,
    user_id: str = Depends(require_session),
) -> HTMLResponse:
```

The `GET /spend` route belongs on `router` (the auth-gated router), not `public_router`. The `user_id` comes from the `require_session` dependency which also gates the route.

### Slack DM bypass category

**Source:** `src/gekko/execution/executor.py` lines 249-254
**Apply to:** 80%/100% cost-alert DMs in `runtime.py`

```python
_BYPASS_CATEGORIES = frozenset({"kill_active", "executor_error", "first_live_fill"})
```

Cost-alert DMs should bypass quiet hours (operator safety-adjacent per RESEARCH §open question 3). Add `"cost_alert"` to this frozenset in `executor.py` so the `_send_slack_dm_aware` path fires immediately.

### SQLite batch_alter_table for CHECK constraint changes

**Source:** `migrations/versions/0004_p3_hitl_ux.py` lines 204-210
**Apply to:** `0005_p4_cost_ceiling.py`

```python
with op.batch_alter_table("events") as bop:
    bop.drop_constraint("ck_event_type", type_="check")
    bop.create_check_constraint(
        "ck_event_type",
        _in_check("event_type", _FROZEN_EVENT_TYPES_POST),
    )
```

SQLite does not support `ALTER TABLE ... DROP CONSTRAINT` directly. Every CHECK constraint modification MUST use `op.batch_alter_table`. Always drop before recreating.

---

## No Analog Found

All files have close codebase analogs. No "no analog" entries.

---

## Metadata

**Analog search scope:** `src/gekko/agent/`, `src/gekko/dashboard/`, `src/gekko/approval/`, `src/gekko/db/`, `src/gekko/audit/`, `migrations/versions/`, `tests/unit/`
**Files scanned:** 16 source files + 4 migration files + 3 test files
**Pattern extraction date:** 2026-06-23
