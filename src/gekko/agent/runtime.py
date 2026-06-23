"""``trigger_strategy_run`` orchestrator + ``compile_strategy_from_chat`` — Plan 01-07 Task 6.

The **single entry point** all three Phase 1 trigger surfaces call:

* Slack ``/gekko run <strategy>`` (Plan 01-08)
* CLI ``gekko run <strategy>`` (Plan 01-09)
* Dashboard "Run now" button (Plan 01-09)
* APScheduler cadence (Plan 01-09)

Wave 2 invariant per D-06: every trigger surface is a thin wrapper around
:func:`trigger_strategy_run`. The function itself owns:

1. Loading the user's latest Strategy (D-05 snapshot row) + active
   Guidance rows (RES-08).
2. Spawning the Researcher subagent via a single ``query()`` call with a
   Researcher-only ``allowed_tools`` and ``system_prompt``. Parsing the
   ``<RESEARCH_BRIEF>{json}</RESEARCH_BRIEF>`` block from the agent's
   final AssistantMessage text (docs/sdk-shape.md delta #5).
3. Spawning the Decision subagent via a SECOND ``query()`` call with a
   Decision-only ``allowed_tools`` (exactly the two propose_*
   tools — D-11) and the Decision system_prompt that embeds the brief
   inside the ``<RESEARCH_BRIEF source="researcher">`` delimiters
   (D-10 / RESEARCH Pitfall 9 — prompt-injection isolation).
4. Extracting the Decision agent's ``ToolUseBlock`` (name + input) from
   its final AssistantMessage content (docs/sdk-shape.md delta #6).
5. Handing the tool call to the deterministic
   :func:`gekko.agent.proposal_writer.write_proposal` which validates,
   computes the COID, persists the proposal row, and emits the
   decision + proposal audit events (D-15).

The orchestrator uses the SDK as a thin command + control layer per
docs/sdk-shape.md Option A. NO ``client.delegate(...)`` (does not
exist), NO ``result.structured_output`` (does not exist on the relevant
shape). Both subagents are explicitly driven from Python so the
research-transcript-leakage path (D-10) is closed at the orchestrator
level — only the parsed ``ResearchBrief`` JSON crosses the boundary.

The SQLCipher passphrase indirection (``_GET_PASSPHRASE``) is a
placeholder owned by Plan 01-09's CLI bootstrap. Tests inject a
``session_factory`` directly, bypassing the indirection (D-19 single-
process model means the passphrase lives in a module-global cache).

References:
  * .planning/.../01-CONTEXT.md  D-06, D-09, D-10, D-11, D-13, D-15, D-21
  * .planning/.../01-RESEARCH.md  §"Pattern 2 — Researcher->Decision via
    structured Brief"; §"System Architecture Diagram"
  * docs/sdk-shape.md             Option A (two query() calls); deltas
    #3, #5, #6, #7
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from decimal import Decimal

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, query
from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock
from claude_agent_sdk.types import ResultMessage as SDKResultMessage
from sqlalchemy import select

from gekko.agent.budget import BudgetTracker
from gekko.agent.cost_ceiling import CeilingCheck, check_cost_ceiling
from gekko.agent.decision import DECISION_TOOLS, build_decision_prompt
from gekko.agent.proposal_writer import write_proposal
from gekko.agent.researcher import RESEARCHER_TOOLS, build_researcher_prompt
from gekko.agent.tools.alpaca_data import get_quote
from gekko.agent.tools.context import set_tool_context
from gekko.agent.tools.edgar import get_edgar_filing
from gekko.agent.tools.finnhub_news import get_news
from gekko.agent.tools.propose_no_action import propose_no_action
from gekko.agent.tools.propose_trade import propose_trade
from gekko.agent.tools.web_fetch import web_fetch
from gekko.audit.canonical import normalize_decimals
from gekko.audit.log import append_event
from gekko.config import get_settings
from gekko.core.errors import ProposalRejected
from gekko.db.engine import get_async_engine
from gekko.db.models import Guidance as GuidanceRow
from gekko.db.models import Strategy as StrategyRow
from gekko.db.session import AsyncSessionLocal, make_session_factory
from gekko.logging_config import get_logger
from gekko.schemas.proposal import NoActionProposal, TradeProposal
from gekko.schemas.research import ResearchBrief
from gekko.schemas.strategy import Guidance, Strategy

if TYPE_CHECKING:
    from gekko.brokers.base import Brokerage

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Soft cap on Researcher turns. The BudgetTracker provides the hard halt
#: at the tool-call layer (D-13); this is the SDK-level guardrail so the
#: model doesn't spin in a loop without firing a tool.
_RESEARCHER_MAX_TURNS: int = 12

#: Decision agent emits exactly one tool call — give it two turns so the
#: SDK can produce the tool-use block plus the closing turn.
_DECISION_MAX_TURNS: int = 2

#: Default trigger source string per D-06.
_DEFAULT_SOURCE: str = "manual"

#: Model alias per docs/sdk-shape.md delta #7.
_MODEL_ALIAS: str = "sonnet"

#: Default buying power for P1 Decision-prompt context (per Task 4 plan).
_P1_BUYING_POWER_USD: int = 10000

#: SQLCipher passphrase indirection — Plan 01-09 closed this loop by
#: making :mod:`gekko.vault.passphrase` the single source of truth. The
#: two names below are thin shims that delegate to the vault module so
#: existing callers (and Plan 01-07/01-08 tests that patched these names
#: directly) keep working without churn.
#:
#: New code should import from :mod:`gekko.vault.passphrase` directly.


def set_passphrase(passphrase: str) -> None:
    """Deprecated shim — delegates to :func:`gekko.vault.passphrase.set_passphrase`."""
    from gekko.vault.passphrase import set_passphrase as _vault_set

    _vault_set(passphrase)


def _get_passphrase() -> str:
    """Deprecated shim — delegates to :func:`gekko.vault.passphrase.get_passphrase`."""
    from gekko.vault.passphrase import get_passphrase as _vault_get

    return _vault_get()


# ---------------------------------------------------------------------------
# SC-2 suspicious-content injection detector (COST-01 / Phase-4 Plan 04-03)
# ---------------------------------------------------------------------------

#: Module-level compiled regex for detecting prompt-injection patterns in
#: EvidenceSnippet.quote_text.  Scanned AFTER the Researcher brief is parsed
#: and BEFORE the Decision agent is called — at the trust boundary.
#:
#: Patterns (RESEARCH §RQ-6, re.IGNORECASE):
#:   1. "SYSTEM:"    — system-prompt impersonation
#:   2. "OVERRIDE:"  — override instruction pattern
#:   3. "ignore previous instructions" — classic injection phrase
#:   4. "disregard your instructions" — variant
#:   5. "forget your instructions"    — variant
_INJECTION_PATTERNS: re.Pattern[str] = re.compile(
    r"SYSTEM\s*:|OVERRIDE\s*:|ignore\s+previous\s+instructions|"
    r"disregard\s+your\s+instructions|forget\s+your\s+instructions",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Regex for extracting <RESEARCH_BRIEF>{json}</RESEARCH_BRIEF> blocks
# ---------------------------------------------------------------------------

_BRIEF_RE: re.Pattern[str] = re.compile(
    r"<RESEARCH_BRIEF[^>]*>\s*(\{.*?\})\s*</RESEARCH_BRIEF>",
    re.DOTALL,
)


def _extract_research_brief_json(text: str) -> str:
    """Pull the JSON payload out of the first ``<RESEARCH_BRIEF>`` block.

    :raises ValueError: When the text contains no brief block. The runtime
        treats this as a Researcher protocol violation and surfaces an
        error event.
    """
    match = _BRIEF_RE.search(text)
    if match is None:
        msg = (
            "Researcher subagent did not emit a <RESEARCH_BRIEF>...</RESEARCH_BRIEF> "
            "block in its final message; cannot construct a ResearchBrief."
        )
        raise ValueError(msg)
    return match.group(1)


# ---------------------------------------------------------------------------
# Strategy + Guidance loading
# ---------------------------------------------------------------------------


async def load_latest_strategy(
    session_factory: AsyncSessionLocal,
    *,
    user_id: str,
    strategy_name: str,
) -> tuple[Strategy, str]:
    """Load the latest snapshot row for ``(user_id, strategy_name)`` (D-05).

    :returns: ``(Strategy, strategy_db_id)``. ``strategy_db_id`` is the
        DB primary-key string the Proposal Writer uses for FK references.
    :raises ValueError: When no strategy row exists.
    """
    async with session_factory() as session:
        q = (
            select(StrategyRow)
            .where(
                StrategyRow.user_id == user_id,
                StrategyRow.strategy_name == strategy_name,
            )
            .order_by(StrategyRow.version.desc())
            .limit(1)
        )
        row = (await session.execute(q)).scalar_one_or_none()
    if row is None:
        msg = f"Strategy not found: {user_id}/{strategy_name}"
        raise ValueError(msg)
    strategy = Strategy.model_validate_json(row.payload_json)
    return strategy, row.strategy_id


async def load_active_guidance(
    session_factory: AsyncSessionLocal,
    *,
    user_id: str,
    strategy_db_id: str,
) -> list[Guidance]:
    """Return active Guidance rows (per RES-08 / STRAT-03).

    Active = ``expires_at IS NULL`` OR ``expires_at > now()``. Both
    strategy-scoped (matching ``strategy_id``) and global rows are
    included.
    """
    now_iso = datetime.now(UTC).isoformat()
    async with session_factory() as session:
        q = select(GuidanceRow).where(
            GuidanceRow.user_id == user_id,
            (
                (GuidanceRow.strategy_id == strategy_db_id)
                | (GuidanceRow.scope == "global")
            ),
            (
                (GuidanceRow.expires_at.is_(None))
                | (GuidanceRow.expires_at > now_iso)
            ),
        )
        rows = (await session.execute(q)).scalars().all()
    out: list[Guidance] = []
    for r in rows:
        out.append(
            Guidance(
                guidance_id=r.guidance_id,
                user_id=r.user_id,
                strategy_id=r.strategy_id,
                text=r.text,
                scope=r.scope,  # type: ignore[arg-type]
                created_at=r.created_at,
                expires_at=r.expires_at,
            )
        )
    return out


# ---------------------------------------------------------------------------
# MCP server registration helper
# ---------------------------------------------------------------------------


def _build_gekko_mcp_server() -> Any:
    """Register the six Gekko in-process tools as a single MCP server.

    Returns the ``McpSdkServerConfig`` value that goes into
    ``ClaudeAgentOptions.mcp_servers={"gekko": <this>}``. Per
    docs/sdk-shape.md delta #3.
    """
    return create_sdk_mcp_server(
        name="gekko",
        version="1.0.0",
        tools=[
            get_quote,
            get_news,
            get_edgar_filing,
            web_fetch,
            propose_trade,
            propose_no_action,
        ],
    )


# ---------------------------------------------------------------------------
# Researcher phase
# ---------------------------------------------------------------------------


async def _run_researcher(
    *,
    strategy: Strategy,
    guidance: list[Guidance],
    user_id: str,
    run_id: str,
    mcp_server: Any,
    session_factory: AsyncSessionLocal,
    strategy_db_id: str,
    max_turns: int = _RESEARCHER_MAX_TURNS,
    max_evidence_items: int | None = None,
) -> ResearchBrief:
    """Phase A: drive the Researcher subagent and return its ResearchBrief.

    Uses ``query()`` with ``allowed_tools`` restricted to the four
    read-only Researcher tools and a system_prompt that instructs the
    model to emit the brief inside ``<RESEARCH_BRIEF>...</RESEARCH_BRIEF>``
    delimiters per docs/sdk-shape.md delta #5.

    :param session_factory: Used to write the ``llm_cost`` audit event
        (COST-05 / D-10) after the query() stream completes.
    :param strategy_db_id: Strategy FK for the llm_cost event.
    :param max_turns: Override for ``_RESEARCHER_MAX_TURNS`` (Wave 4
        degradation mode passes 6).
    :param max_evidence_items: When set, passed to ``build_researcher_prompt``
        to request a trimmed brief (D-04 tactic 3 — context trim).
    """
    system_prompt = build_researcher_prompt(
        strategy,
        guidance,
        user_id=user_id,
        run_id=run_id,
        max_evidence_items=max_evidence_items,
    )
    options = ClaudeAgentOptions(
        mcp_servers={"gekko": mcp_server},
        allowed_tools=RESEARCHER_TOOLS,
        system_prompt=system_prompt,
        model=_MODEL_ALIAS,
        max_turns=max_turns,
    )

    user_prompt = (
        f"Research strategy {strategy.name!r}. "
        "Use the available tools to gather quotes, news, and SEC filings "
        "for the watchlist. Then emit your final <RESEARCH_BRIEF> block."
    )

    # COST-05 / D-10: capture ResultMessage + token counts for cost ledger.
    result_msg: SDKResultMessage | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    accumulated_text = ""
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

    # Write llm_cost ledger entry (COST-05).
    cost_usd = Decimal(str(result_msg.total_cost_usd or 0.0)) if result_msg else Decimal("0")
    try:
        async with session_factory() as _cost_session, _cost_session.begin():
            await append_event(
                _cost_session,
                user_id=user_id,
                strategy_id=strategy_db_id,
                event_type="llm_cost",
                payload=normalize_decimals({
                    "run_id": run_id,
                    "strategy_name": strategy.name,
                    "model": _MODEL_ALIAS,
                    "call_type": "researcher",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": cost_usd,
                }),
            )
    except Exception:
        # Never let cost-ledger persistence shadow the researcher result.
        log.exception(
            "agent.run.cost_ledger_persist_failed",
            user_id=user_id,
            run_id=run_id,
            call_type="researcher",
        )

    brief_json = _extract_research_brief_json(accumulated_text)
    brief = ResearchBrief.model_validate_json(brief_json)
    return brief


# ---------------------------------------------------------------------------
# Decision phase
# ---------------------------------------------------------------------------


async def _run_decision(
    *,
    strategy: Strategy,
    brief: ResearchBrief,
    mcp_server: Any,
    user_id: str,
    run_id: str,
    strategy_db_id: str,
    strategy_name: str,
    session_factory: AsyncSessionLocal,
) -> tuple[str, dict[str, Any]]:
    """Phase B: drive the Decision subagent and return its tool call.

    :returns: ``(tool_outcome, tool_payload)`` where ``tool_outcome`` is
        the short tool name (``"propose_trade"`` or ``"propose_no_action"``)
        and ``tool_payload`` is the LLM-supplied input dict ready to hand
        to :func:`write_proposal`.

    :param session_factory: Used to write the ``llm_cost`` audit event
        (COST-05 / D-10) after the query() stream completes.
    :param user_id: Owner; used in the llm_cost audit event.
    :param run_id: Run identifier; included in the llm_cost payload.
    :param strategy_db_id: Strategy FK for the llm_cost event.
    :param strategy_name: Strategy slug for the llm_cost payload.

    :raises ValueError: When the Decision agent failed to emit either of
        the two valid tool calls (D-11 protocol violation).
    """
    system_prompt = build_decision_prompt(strategy, brief)
    options = ClaudeAgentOptions(
        mcp_servers={"gekko": mcp_server},
        allowed_tools=DECISION_TOOLS,
        system_prompt=system_prompt,
        model=_MODEL_ALIAS,
        max_turns=_DECISION_MAX_TURNS,
    )

    user_prompt = "Make your decision now."

    tool_outcome: str | None = None
    tool_payload: dict[str, Any] | None = None

    # COST-05 / D-10: capture ResultMessage + token counts for cost ledger.
    result_msg: SDKResultMessage | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    async for msg in query(prompt=user_prompt, options=options):
        if isinstance(msg, SDKResultMessage):
            result_msg = msg
        elif isinstance(msg, AssistantMessage):
            if msg.usage:
                input_tokens += msg.usage.get("input_tokens", 0)
                output_tokens += msg.usage.get("output_tokens", 0)
            for block in msg.content:
                if not isinstance(block, ToolUseBlock):
                    continue
                if block.name in (
                    "mcp__gekko__propose_trade",
                    "mcp__gekko__propose_no_action",
                ):
                    tool_outcome = block.name.replace("mcp__gekko__", "")
                    tool_payload = dict(block.input)
                    break
            if tool_outcome is not None:
                break

    # Write llm_cost ledger entry (COST-05).
    cost_usd = Decimal(str(result_msg.total_cost_usd or 0.0)) if result_msg else Decimal("0")
    try:
        async with session_factory() as _cost_session, _cost_session.begin():
            await append_event(
                _cost_session,
                user_id=user_id,
                strategy_id=strategy_db_id,
                event_type="llm_cost",
                payload=normalize_decimals({
                    "run_id": run_id,
                    "strategy_name": strategy_name,
                    "model": _MODEL_ALIAS,
                    "call_type": "decision",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": cost_usd,
                }),
            )
    except Exception:
        # Never let cost-ledger persistence shadow the decision result.
        log.exception(
            "agent.run.cost_ledger_persist_failed",
            user_id=user_id,
            run_id=run_id,
            call_type="decision",
        )

    if tool_outcome is None or tool_payload is None:
        msg = (
            "Decision subagent did not emit a propose_trade or "
            "propose_no_action tool call (D-11 violation)."
        )
        raise ValueError(msg)

    return tool_outcome, tool_payload


async def _persist_proposal_rejected_event(
    session_factory: AsyncSessionLocal,
    *,
    user_id: str,
    strategy_db_id: str,
    run_id: str,
    decision_id: str,
    payload: dict[str, Any],
    strategy: Strategy,
) -> None:
    """Re-emit the watchlist-violation error event in a fresh transaction.

    The Proposal Writer queues the error event inside its branch but
    then raises :exc:`ProposalRejected`, which rolls back the writer's
    transaction (including the queued event). To keep the audit chain
    accurate the orchestrator opens a fresh transaction and writes the
    error event explicitly.
    """
    try:
        async with session_factory() as session, session.begin():
            await append_event(
                session,
                user_id=user_id,
                strategy_id=strategy_db_id,
                event_type="error",
                payload=normalize_decimals(
                    {
                        "context": "trigger_strategy_run.proposal_rejected",
                        "error_class": "ProposalRejected",
                        "error_message": (
                            f"Decision agent proposed ticker outside watchlist "
                            f"{list(strategy.watchlist)}"
                        ),
                        "rejected_proposal": payload,
                        "run_id": run_id,
                        "decision_id": decision_id,
                    }
                ),
            )
    except Exception:
        # Never let audit-event persistence shadow the original
        # ProposalRejected. Log and swallow.
        log.exception(
            "agent.run.rejection_audit_persist_failed",
            user_id=user_id,
            run_id=run_id,
            decision_id=decision_id,
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def trigger_strategy_run(
    *,
    user_id: str,
    strategy_name: str,
    source: str = _DEFAULT_SOURCE,
    session_factory: AsyncSessionLocal | None = None,
    broker: Brokerage | None = None,
    prompt_model: str = _MODEL_ALIAS,
) -> dict[str, Any]:
    """Single orchestrator entry point — D-06.

    :param user_id: Trigger-surface user identity (Slack user, CLI
        operator, etc.).
    :param strategy_name: The strategy slug to run.
    :param source: One of ``"slack" | "cli" | "dashboard" | "schedule" |
        "manual"`` per D-06.
    :param session_factory: Optional override. When ``None``, the runtime
        builds the per-user SQLCipher engine via :func:`get_async_engine`
        with the cached passphrase. Tests pass their own factory.
    :param broker: Optional :class:`Brokerage` for the Researcher's
        ``get_quote`` tool. Tests typically pass ``None`` or a mock.
    :param prompt_model: Audit annotation for the decision event's
        ``prompt_model`` field. Defaults to ``"sonnet"``.

    :returns: Run summary dict with ``run_id``, ``decision_id``,
        ``outcome`` ("trade" / "no_action"), ``proposal`` (model_dump),
        ``budget`` (BudgetTracker.to_dict()), and ``source``.
    """
    run_id = uuid4().hex
    decision_id = uuid4().hex

    log.info(
        "agent.run.start",
        user_id=user_id,
        strategy_name=strategy_name,
        source=source,
        run_id=run_id,
        decision_id=decision_id,
    )

    # ---- Quiet-hours gate (HITL-05 / D-46) ----------------------------------
    # Scheduled cycles (source="schedule") are skipped when the operative
    # quiet-hours window is active.  Manual invocations (source="manual",
    # "cli", "slack", "dashboard") bypass unconditionally — the operator's
    # explicit intent overrides the quiet-hours setting (D-46).
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

    # ---- Cost-ceiling gate (COST-01 / D-07) ----------------------------------
    # ALL trigger sources (not just "schedule") respect the ceiling — a
    # manual run also deducts from the daily pool. The halt is absolute.
    # Gate is deterministic Python (no LLM call) and fires BEFORE any
    # query() dispatch so the model cannot reason past it (T-04-05).
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
            try:
                from gekko.execution.executor import _send_slack_dm_respecting_quiet_hours
                await _send_slack_dm_respecting_quiet_hours(
                    user_id,
                    f"Daily LLM cost ceiling reached: "
                    f"${_ceiling.current_spend:.4f} / ${_ceiling.ceiling:.2f}. "
                    f"All agent cycles halted until midnight in your timezone. "
                    f"Raise the ceiling in Settings to resume.",
                    category="cost_alert",
                )
            except Exception:
                log.exception(
                    "agent.cycle.cost_halt_dm_failed",
                    user_id=user_id,
                )
        return {
            "run_id": run_id,
            "outcome": "skipped_cost_halt",
            "source": source,
        }
    elif _ceiling.action == "degrade":
        # D-04/D-06: degradation mode — cadence, triage, context trim (Wave 4)
        log.info(
            "agent.cycle.degraded_cost_ceiling",
            user_id=user_id,
            strategy_name=strategy_name,
            source=source,
            spend_usd=str(_ceiling.current_spend),
            ceiling_usd=str(_ceiling.ceiling),
            pct=str(_ceiling.pct),
        )
        if _ceiling.just_crossed_80:
            try:
                from gekko.execution.executor import _send_slack_dm_respecting_quiet_hours
                await _send_slack_dm_respecting_quiet_hours(
                    user_id,
                    f"Daily LLM cost at 80%+ of ceiling: "
                    f"${_ceiling.current_spend:.4f} / ${_ceiling.ceiling:.2f} "
                    f"({_ceiling.pct:.1f}%). Agent entering degraded mode "
                    f"(slower cadence, triage gate active).",
                    category="cost_alert",
                )
            except Exception:
                log.exception(
                    "agent.cycle.cost_degrade_dm_failed",
                    user_id=user_id,
                )
    # degradation_mode flag — consumed by Wave 4 Haiku triage gate.
    _degradation_mode: bool = _ceiling.action == "degrade"

    # Determine session factory + engine ownership.
    own_engine = False
    engine = None
    if session_factory is None:
        settings = get_settings()
        engine = get_async_engine(
            settings.db_path_for(user_id), _get_passphrase()
        )
        session_factory = make_session_factory(engine)
        own_engine = True

    budget = BudgetTracker()
    set_tool_context(budget=budget, broker=broker)

    try:
        # 1. Load strategy + active guidance.
        strategy, strategy_db_id = await load_latest_strategy(
            session_factory, user_id=user_id, strategy_name=strategy_name
        )
        guidance = await load_active_guidance(
            session_factory, user_id=user_id, strategy_db_id=strategy_db_id
        )

        # 2. Build the MCP server (the six in-process tools).
        mcp_server = _build_gekko_mcp_server()

        # 3. Researcher phase.
        _researcher_max_turns = 6 if _degradation_mode else _RESEARCHER_MAX_TURNS
        _researcher_max_evidence = 3 if _degradation_mode else None
        brief = await _run_researcher(
            strategy=strategy,
            guidance=guidance,
            user_id=user_id,
            run_id=run_id,
            mcp_server=mcp_server,
            session_factory=session_factory,
            strategy_db_id=strategy_db_id,
            max_turns=_researcher_max_turns,
            max_evidence_items=_researcher_max_evidence,
        )

        # SC-2 gap closure (Phase-4 Plan 04-03): scan evidence quote_text for
        # injection patterns AFTER brief is parsed, BEFORE Decision agent is called.
        # This is the trust boundary — external content (quote_text from web/news)
        # is scanned here before entering the Decision phase.
        # Neutralization is already in place (D-40 prompt boundary); this logs
        # the detection event for the audit chain (T-04-07).
        for _evidence in brief.evidence:
            if _evidence.quote_text and _INJECTION_PATTERNS.search(_evidence.quote_text):
                try:
                    async with session_factory() as _sc_session, _sc_session.begin():
                        await append_event(
                            _sc_session,
                            user_id=user_id,
                            strategy_id=strategy_db_id,
                            event_type="suspicious_content",
                            payload=normalize_decimals(
                                {
                                    "run_id": run_id,
                                    "source_type": _evidence.source_type,
                                    "source_url": str(_evidence.source_url),
                                    "pattern_matched": True,
                                }
                            ),
                        )
                except Exception:
                    # Never let SC-2 logging shadow the main run — log and continue.
                    log.exception(
                        "agent.run.suspicious_content_log_failed",
                        user_id=user_id,
                        run_id=run_id,
                    )

        # 4. Decision phase.
        tool_outcome, tool_payload = await _run_decision(
            strategy=strategy,
            brief=brief,
            mcp_server=mcp_server,
            user_id=user_id,
            run_id=run_id,
            strategy_db_id=strategy_db_id,
            strategy_name=strategy_name,
            session_factory=session_factory,
        )

        # 5. ProposalWriter — deterministic persistence + audit events.
        #
        # ProposalRejected (watchlist guard) must leave the error event
        # COMMITTED on the audit chain so the orchestrator's "we ran
        # the agent but rejected its output" record survives. The
        # writer appends the error event inside its own try/except
        # branch, then raises — we commit BEFORE the re-raise so the
        # event lands on disk.
        try:
            async with session_factory() as session, session.begin():
                proposal: TradeProposal | NoActionProposal = await write_proposal(
                    session,
                    user_id=user_id,
                    strategy=strategy,
                    strategy_db_id=strategy_db_id,
                    run_id=run_id,
                    decision_id=decision_id,
                    tool_outcome=tool_outcome,
                    payload=tool_payload,
                    prompt_model=prompt_model,
                )
        except ProposalRejected:
            # The error event has already been queued by the writer; the
            # outer ``session.begin()`` context rolled it back when the
            # exception propagated. Open a fresh transaction and re-emit
            # the error event so the audit chain captures the
            # rejection (RESEARCH §Security Domain — hallucinated
            # ticker mitigation must be observable in the audit log).
            await _persist_proposal_rejected_event(
                session_factory,
                user_id=user_id,
                strategy_db_id=strategy_db_id,
                run_id=run_id,
                decision_id=decision_id,
                payload=tool_payload,
                strategy=strategy,
            )
            raise

        log.info(
            "agent.run.complete",
            user_id=user_id,
            run_id=run_id,
            outcome=tool_outcome,
            budget=budget.to_dict(),
        )

        return {
            "run_id": run_id,
            "decision_id": decision_id,
            "outcome": tool_outcome,
            "proposal": proposal.model_dump(mode="json"),
            "budget": budget.to_dict(),
            "source": source,
        }
    finally:
        if own_engine and engine is not None:
            await engine.dispose()


# ---------------------------------------------------------------------------
# STRAT-01 — Strategy compilation from NL chat
# ---------------------------------------------------------------------------


_COMPILER_SYSTEM_PROMPT: str = """\
You are the Strategy Compiler for Gekko. The user describes a strategy in
plain English. Your job is to emit a Strategy JSON document conforming to
this schema:

{schema_json}

Extract:
  - name (short slug, lowercase, dash-separated; no spaces)
  - thesis (plain-English summary of intent, 1-3 sentences)
  - watchlist (uppercase tickers, deduplicated, preserve first-seen order)
  - hard_caps with reasonable defaults:
      max_position_pct = 0.05
      max_daily_loss_usd = 200
      max_trades_per_day = 3
      max_sector_exposure_pct = 0.25
    (The user can override any in chat.)
  - mode = "paper" by default (P1 is paper-only)
  - schedule_time = null unless the user explicitly specifies an
    "HH:MM Tz" pair

OUTPUT FORMAT (load-bearing — the runtime parses this with regex):

Your FINAL message MUST contain exactly one block of the form:

<STRATEGY>
{{ ...strategy JSON conforming to the schema above... }}
</STRATEGY>

IMPORTANT:
  - You do NOT have any tools. Your only output is the <STRATEGY> block.
  - Do NOT include any free-form prose outside the block.
"""


_STRATEGY_RE: re.Pattern[str] = re.compile(
    r"<STRATEGY>\s*(\{.*?\})\s*</STRATEGY>",
    re.DOTALL,
)


def _extract_strategy_json(text: str) -> str:
    """Pull the JSON payload out of the first ``<STRATEGY>`` block."""
    match = _STRATEGY_RE.search(text)
    if match is None:
        msg = "Strategy Compiler did not emit a <STRATEGY>...</STRATEGY> block."
        raise ValueError(msg)
    return match.group(1)


async def compile_strategy_from_chat(
    *,
    user_id: str,
    chat_transcript: str,
) -> Strategy:
    """STRAT-01 — turn an NL chat transcript into a validated Strategy.

    Uses a single ``query()`` call with the compiler system prompt; the
    model emits ``<STRATEGY>{json}</STRATEGY>`` and we parse + validate.

    The Pydantic model's ``strategy_id``, ``user_id``, ``version``, and
    ``created_at`` are filled by this function (NOT the LLM) — same
    runtime-vs-LLM split as the proposal writer.

    :param user_id: Owner of the resulting Strategy snapshot.
    :param chat_transcript: Plain-English description (possibly
        multi-turn) of the intended strategy.
    :returns: A validated :class:`Strategy` ready for the Plan 01-09
        save endpoint.
    """
    schema_json = Strategy.model_json_schema()
    import json

    system_prompt = _COMPILER_SYSTEM_PROMPT.format(
        schema_json=json.dumps(schema_json, indent=2)
    )
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=_MODEL_ALIAS,
        max_turns=2,
    )

    accumulated_text = ""
    async for msg in query(prompt=chat_transcript, options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    accumulated_text += block.text

    strategy_json = _extract_strategy_json(accumulated_text)

    raw = json.loads(strategy_json)
    # Fill runtime-supplied fields (the LLM does not invent them).
    raw.setdefault("user_id", user_id)
    raw.setdefault("strategy_id", "strat-" + uuid4().hex)
    raw.setdefault("version", 1)
    raw.setdefault("created_at", datetime.now(UTC).isoformat())
    raw.setdefault("created_by_chat", True)
    return Strategy.model_validate(raw)


__all__: tuple[str, ...] = (
    "compile_strategy_from_chat",
    "load_active_guidance",
    "load_latest_strategy",
    "set_passphrase",
    "trigger_strategy_run",
)
