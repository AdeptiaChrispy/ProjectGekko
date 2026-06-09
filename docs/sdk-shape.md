# Claude Agent SDK v0.2.93 — actual API surface (Plan 01-07 Task 1 verification)

**Verified**: 2026-06-09 against installed `claude-agent-sdk==0.2.93`.

The RESEARCH §"Code Examples" sketched an API that **does not match the shipping SDK in
several load-bearing ways**. Tasks 3 / 4 / 6 of Plan 01-07 must follow the patterns
documented here, NOT the RESEARCH sketch. Use this file as the authoritative reference.

---

## What matches RESEARCH (no change)

| Item | Status |
|---|---|
| `from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ClaudeSDKClient, tool, create_sdk_mcp_server, query` | ✓ all imports valid |
| `AgentDefinition(description=..., prompt=..., tools=[...], model=...)` | ✓ fields exist |
| `ClaudeAgentOptions(agents={"researcher": ..., "decision": ...})` | ✓ field exists |
| Tool restriction per subagent via `AgentDefinition.tools: list[str]` | ✓ confirmed |
| Decision subagent locked to exactly `["propose_trade", "propose_no_action"]` | ✓ supported |

## Deltas — RESEARCH was wrong about these

### 1. The `@tool` decorator is positional, not kwargs

**RESEARCH wrote:**
```python
@tool(name="get_quote", description="...", input_schema={"type":"object", ...})
async def get_quote(ticker: str, *, budget: BudgetTracker, broker: Brokerage) -> dict:
    ...
```

**Actual SDK signature:**
```python
def tool(
    name: str,
    description: str,
    input_schema: type | dict[str, Any],
    annotations: ToolAnnotations | None = None,
) -> Callable[..., SdkMcpTool[Any]]:
```

Call positionally OR with explicit kwargs (both work — positional is what the docstring
examples use):
```python
@tool("get_quote", "Fetch latest quote for a US equity", {"ticker": str})
```

### 2. Tool function signature: **one `args: dict`, NOT named params + injected services**

**RESEARCH wrote:**
```python
async def get_quote(ticker: str, *, budget: BudgetTracker, broker: Brokerage) -> dict:
    ...
```

**Actual SDK contract:**
```python
@tool("get_quote", "Fetch latest quote", {"ticker": str})
async def get_quote(args: dict) -> dict:
    ticker = args["ticker"]
    # ...
    return {"content": [{"type": "text", "text": json.dumps(snapshot_dump)}]}
```

The tool function **takes one positional `args: dict`** and **returns MCP content shape**,
not a raw payload dict.

**Application state injection pattern**: tools must close over a factory or module-level
singleton. Pattern:

```python
# gekko/agent/tools/alpaca_data.py
from gekko.agent.budget import BudgetTracker

_BUDGET: BudgetTracker | None = None
_BROKER = None  # set by runtime.trigger_strategy_run before query starts

def set_tool_context(*, budget: BudgetTracker, broker) -> None:
    global _BUDGET, _BROKER
    _BUDGET = budget
    _BROKER = broker

@tool("get_quote", "Fetch latest quote for a US equity", {"ticker": str})
async def get_quote(args: dict) -> dict:
    ticker = args["ticker"].upper()
    snap = await _BROKER.get_quote(ticker)
    _BUDGET.record_call(tokens=100)
    return {"content": [{"type": "text", "text": json.dumps(snap.model_dump(mode="json"))}]}
```

`runtime.trigger_strategy_run` calls `set_tool_context(budget=..., broker=...)` **before**
spawning the SDK client. The Researcher tool calls then have access to the per-run state
via the module-globals. This is single-event-loop, single-process — there is no
cross-strategy bleed risk in P1 because trigger_strategy_run holds the event loop until
the run completes.

### 3. Tools are registered via `create_sdk_mcp_server`, not as a raw list on options

**RESEARCH wrote:**
```python
options = ClaudeAgentOptions(
    agents={"researcher": RESEARCHER, "decision": DECISION},
    tools=[get_quote, get_news, get_edgar_filing, web_fetch, propose_trade, propose_no_action],
)
```

**`options.tools` is for restricting BUILT-IN Claude Code tools** (Bash, Read, Edit, etc.),
not for registering custom tools.

**Actual registration**:
```python
from claude_agent_sdk import create_sdk_mcp_server, ClaudeAgentOptions

gekko_server = create_sdk_mcp_server(
    name="gekko",
    version="1.0.0",
    tools=[get_quote, get_news, get_edgar_filing, web_fetch, propose_trade, propose_no_action],
)
options = ClaudeAgentOptions(
    mcp_servers={"gekko": gekko_server},
    allowed_tools=[
        "mcp__gekko__get_quote",
        "mcp__gekko__get_news",
        "mcp__gekko__get_edgar_filing",
        "mcp__gekko__web_fetch",
        "mcp__gekko__propose_trade",
        "mcp__gekko__propose_no_action",
    ],
    agents={"researcher": RESEARCHER, "decision": DECISION},
)
```

**Tool name in `AgentDefinition.tools`**: use the fully-qualified MCP name. So:
```python
RESEARCHER = AgentDefinition(
    description="...",
    prompt="...",
    tools=[
        "mcp__gekko__get_quote",
        "mcp__gekko__get_news",
        "mcp__gekko__get_edgar_filing",
        "mcp__gekko__web_fetch",
    ],
    model="sonnet",
)

DECISION = AgentDefinition(
    description="...",
    prompt="...",
    tools=["mcp__gekko__propose_trade", "mcp__gekko__propose_no_action"],
    model="sonnet",
)
```

### 4. No `client.delegate(subagent_name, prompt)` method

**RESEARCH wrote:**
```python
brief_result = await client.delegate("researcher", researcher_prompt, context={"budget": budget})
```

**ClaudeSDKClient has no `delegate` method.** Public methods are:
- `connect(prompt=None)`
- `query(prompt, session_id="default")` — submit a user-turn
- `receive_response()` — async iterator over streamed messages
- `receive_messages()` — full message stream (all turns)
- `set_model()`, `set_permission_mode()`, `interrupt()`, `stop_task()`

**Subagent invocation model**: subagents registered via `options.agents={...}` are invoked
by the *parent* agent through the built-in `Task` tool. So the parent prompt asks Claude
to delegate to "researcher" and "decision" by name, and Claude uses the Task tool
internally.

For Plan 01-07 we need a different orchestration shape: **drive the two subagents
explicitly from Python, not via parent-agent Task delegation.** Two equally valid
implementations:

**Option A (recommended for P1) — Two single-shot `query()` calls with different options:**
```python
from claude_agent_sdk import query, ClaudeAgentOptions

# Phase A: Researcher
researcher_opts = ClaudeAgentOptions(
    mcp_servers={"gekko": gekko_server},
    allowed_tools=[
        "mcp__gekko__get_quote", "mcp__gekko__get_news",
        "mcp__gekko__get_edgar_filing", "mcp__gekko__web_fetch",
    ],
    system_prompt=build_researcher_prompt(strategy, guidance),
    model="sonnet",
    max_turns=12,  # bounded by D-13 soft cap
)
brief_text = ""
async for msg in query(prompt=f"Research strategy '{strategy.name}'. Emit <RESEARCH_BRIEF>...</RESEARCH_BRIEF>.", options=researcher_opts):
    # Collect AssistantMessage text blocks; the final one contains <RESEARCH_BRIEF>{...}</RESEARCH_BRIEF>
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                brief_text += block.text
brief = ResearchBrief.model_validate_json(_extract_between(brief_text, "<RESEARCH_BRIEF>", "</RESEARCH_BRIEF>"))

# Phase B: Decision
decision_opts = ClaudeAgentOptions(
    mcp_servers={"gekko": gekko_server},
    allowed_tools=["mcp__gekko__propose_trade", "mcp__gekko__propose_no_action"],
    system_prompt=build_decision_prompt(strategy, brief),
    model="sonnet",
    max_turns=2,  # decision agent emits exactly one tool call
)
tool_outcome, tool_payload = None, None
async for msg in query(prompt="Make your decision now.", options=decision_opts):
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, ToolUseBlock) and block.name in ("mcp__gekko__propose_trade", "mcp__gekko__propose_no_action"):
                tool_outcome = block.name.replace("mcp__gekko__", "")
                tool_payload = block.input
                break
```

This is **what Plan 01-07 should actually implement**. It uses the SDK as a thin
orchestrator and keeps the two subagents fully decoupled with NO leakage of raw
research transcripts into Decision context (D-10 satisfied: only the parsed
`ResearchBrief` JSON crosses the boundary).

**Option B — single ClaudeSDKClient with parent-agent Task delegation**: leaves more
control with the model and is closer to the "agents={...}" idiom, but obscures the
researcher→decision split that D-10/D-11 made deliberately one-shot. Avoid for P1.

### 5. `output_format` is on options, not on AgentDefinition; for P1 we don't use it

The SDK has `ClaudeAgentOptions.output_format: dict[str, Any] | None`, but it's a
**session-level** setting and would constrain BOTH subagents to the same schema. Our
two subagents emit different shapes (`ResearchBrief` vs `TradeProposal`/`NoActionProposal`).

For P1: **don't use `output_format`.**
- Researcher emits `<RESEARCH_BRIEF>{JSON}</RESEARCH_BRIEF>` in its final text — parse with
  regex. Brittle but predictable for a constrained prompt; P4 hardens this with
  proper structured-output enforcement (likely via per-call output_format on Option A
  query()).
- Decision is constrained to emit exactly one MCP tool call (`propose_trade` or
  `propose_no_action`) — the schema-enforcement is at the tool's `input_schema`,
  which Claude validates before the tool fires. No JSON parse step needed; we
  pull `tool_payload = block.input` straight from the `ToolUseBlock`.

### 6. `result.usage.input_tokens` — confirmed available on `ResultMessage`

The final `ResultMessage` in each `receive_response()` / `query()` stream has a
`usage` attribute with token counts. BudgetTracker should `record_call(tokens=...)`
inside the tool BUT can also be refined by adding a hook on `ResultMessage` to sum
the real per-turn usage. For P1 keep the per-tool flat token estimate per RESEARCH
§Pattern 1; refine in P4 with real usage if cost-tracking becomes load-bearing.

### 7. Model id confirmed: `"sonnet"` alias OR `"claude-sonnet-4-5"` literal

`AgentDefinition.model` accepts model aliases per the SDK source:

> Model alias ("sonnet", "opus", "haiku", "inherit") or a full model ID.

The SDK docstring example uses `"claude-sonnet-4-5"` as the literal full ID. **Use
`"sonnet"` for Plan 01-07** — it resolves to the latest Sonnet automatically and
avoids future model-name drift. P4 can pin to a specific model ID when cost/quality
sensitivity warrants.

(RESEARCH wrote `claude-sonnet-4-6` — that exact literal is NOT what the SDK
currently uses. The alias is safer.)

### 8. Runtime requirement: `claude` CLI must be installed and authenticated

The Claude Agent SDK is a wrapper around the Claude Code CLI. It spawns the `claude`
binary as a subprocess and communicates via stdin/stdout. This means:

- **The host machine must have `claude` CLI installed and on PATH.**
- **The user must have authenticated (`claude login` or API key set).**
- An unattended APScheduler-fired strategy run depends on this auth being long-lived.

This is a deployment / OPS concern primarily for Plan 01-09 (CLI bootstrap) and the
P7 ops phase. Document in 01-09's plan that `gekko doctor` should check the `claude`
CLI is available and authenticated. **For P1 integration tests, mock the SDK
entirely** — the actual binary doesn't need to run in CI.

---

## Implementation guidance for Plan 01-07 tasks

### Task 2 (BudgetTracker)
**Unchanged from plan.** SDK delta has no effect — BudgetTracker is pure local state
called from inside each tool function.

### Task 3 (Researcher tools)
**Adjust signatures and return shape:**
- Tools take `args: dict` (one positional), return `{"content": [{"type": "text", "text": json_str}], "is_error": False}`
- Inject broker / budget via `set_tool_context(...)` module-globals, NOT via tool-function kwargs
- Tool name in `@tool("get_quote", ...)` — short name; the SDK prefixes `mcp__gekko__` when registering

### Task 4 (AgentDefinitions)
- `RESEARCHER.tools = ["mcp__gekko__get_quote", "mcp__gekko__get_news", "mcp__gekko__get_edgar_filing", "mcp__gekko__web_fetch"]`
- `DECISION.tools = ["mcp__gekko__propose_trade", "mcp__gekko__propose_no_action"]`
- `model="sonnet"` for both
- System prompts stay as the plan wrote them; D-10 delimiters (`<RESEARCH_BRIEF source="researcher">...</RESEARCH_BRIEF>`) live in Decision's prompt
- Researcher prompt **adds** an explicit instruction: "Your final response MUST contain a
  single `<RESEARCH_BRIEF>{...json...}</RESEARCH_BRIEF>` block conforming to this schema:
  {ResearchBrief.model_json_schema()}"

### Task 5 (ProposalWriter)
**Unchanged from plan.** The writer consumes a dict `tool_payload` and a string
`tool_outcome`; that contract is the same whether `tool_outcome` came from a
`ToolUseBlock.name`/`ToolUseBlock.input` extraction (Option A) or a programmatic
`delegate` (the non-existent RESEARCH-imagined API). ProposalWriter is downstream
of the SDK call.

### Task 6 (trigger_strategy_run)
**Significant rewrite vs plan sketch:**
- Drop `ClaudeAgentOptions(agents={...})` + `client.delegate(...)` pattern
- Use two `query()` calls (Option A above): Researcher → parse `<RESEARCH_BRIEF>` from
  final AssistantMessage text → Decision → extract `ToolUseBlock` from final
  AssistantMessage content
- Both calls share the same `gekko_server` MCP config but with different
  `allowed_tools` + `system_prompt` + `max_turns`
- `set_tool_context(budget=BudgetTracker(), broker=alpaca)` BEFORE the first query call
- Keep the entire orchestration inside a single async function so the module-global
  context is safe (no cross-run interleaving)

### compile_strategy_from_chat
- Single `query()` call
- system_prompt = COMPILER_SYSTEM_PROMPT (asks for `<STRATEGY>{json}</STRATEGY>` block)
- Parse the block out of the final text, validate via `Strategy.model_validate_json(...)`

---

## Token-cost estimates per Researcher tool call (BudgetTracker.record_call argument)

These are coarse P1 estimates; refine via real `ResultMessage.usage` in P4.

| Tool | tokens |
|---|---|
| `get_quote` | 100 |
| `get_news` | 200 |
| `get_edgar_filing` | 300 |
| `web_fetch` | 500 |

(Per Plan 01-07 Task 3 action block — values unchanged.)

---

## Summary of deltas the executor must apply

1. **Tool signatures**: `async def fn(args: dict) -> dict` returning MCP content shape
2. **Tool dependency injection**: module-global pattern via `set_tool_context(...)`, not function kwargs
3. **Tool registration**: `create_sdk_mcp_server` → `mcp_servers={...}` → fully-qualified `mcp__gekko__*` names in `allowed_tools` and `AgentDefinition.tools`
4. **Subagent invocation**: two `query()` calls with different `ClaudeAgentOptions`, not `client.delegate(...)`
5. **Research brief plumbing**: `<RESEARCH_BRIEF>` text-block extraction from AssistantMessage, not a `result.structured_output` property
6. **Decision tool call extraction**: pull `(name, input)` from the `ToolUseBlock` in the Decision agent's final AssistantMessage
7. **Model alias**: `"sonnet"`, not `"claude-sonnet-4-6"`
8. **CI / test pattern**: mock the entire SDK (`query`, `ClaudeSDKClient`) — the `claude` CLI binary is not available in CI; tests use `mock_claude_sdk` from conftest.py

Everything else in Plan 01-07 (BudgetTracker behavior, ProposalWriter persistence,
audit-event payload shape per D-15, threat model) is **unaffected** by these deltas.
