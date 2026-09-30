"""pydantic-ai agent wiring: the DM agent, its MCP server, and tool interception.

This module imports ``pydantic_ai`` eagerly, so it is imported lazily by the
engine (only when a game actually starts) to keep it off the CLI startup path.

Two interception seams keep the model honest:

* ``_filter_tools`` (a ``PrepareTools`` capability) hides the admin tools and the
  ``outcome`` parameter from the model — the model never manages rolls or save
  lifecycle.
* ``_process_tool_call`` attaches each ``resolve_outcome`` roll to the next write
  event (deterministic replay) and turns a hard-rule rejection into a
  ``ModelRetry`` so the DM re-proposes a valid change.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.capabilities import PrepareTools
from pydantic_ai.mcp import MCPToolset

from aidm.orchestrator.prompts import NARRATOR_SYSTEM_PROMPT, RESOLVER_SYSTEM_PROMPT

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from pydantic_ai.mcp import CallToolFunc, ToolResult
    from pydantic_ai.models import Model
    from pydantic_ai.tools import ToolDefinition

    from aidm.orchestrator.trace import TurnTrace

# Tools never shown to the model: save lifecycle is driven by the CLI, not the DM.
_ADMIN_TOOLS = frozenset({"set_active_save", "clear_active_save", "get_active_save"})
# Outcome-bearing write tools: each carries an ``outcome`` the orchestrator fills
# (hidden from the model) and attaches the pending roll to.
_OUTCOME_TOOLS = frozenset(
    {
        "propose_state_change",
        "propose_create_entity",
        "propose_relationship_change",
        "propose_world_event",
        "propose_move",
    },
)
# All state-mutating tools. A rejection from any becomes a ModelRetry. The trait/
# condition tools mutate state but take no ``outcome`` (no roll is attached).
_WRITE_TOOLS = _OUTCOME_TOOLS | frozenset(
    {"add_trait", "remove_trait", "add_condition", "remove_condition"},
)
# Re-proposal budget: 5 rejected attempts before the engine narrates a failure.
TOOL_RETRIES = 5


@dataclass
class TurnDeps:
    """Run-scoped state shared with :func:`_process_tool_call` for one turn."""

    pending_roll: dict[str, Any] | None = None
    trace: TurnTrace | None = None
    # Human-readable lines describing each applied write/roll this turn; the engine
    # joins these into the "effects digest" handed to the narrator (v3 §4).
    effects: list[str] = field(default_factory=list)


def _strip_outcome(tool_def: ToolDefinition) -> ToolDefinition:
    """Return a copy of ``tool_def`` with the ``outcome`` parameter removed."""
    schema = tool_def.parameters_json_schema
    properties = schema.get("properties")
    if not isinstance(properties, dict) or "outcome" not in properties:
        return tool_def
    new_schema = {**schema, "properties": {k: v for k, v in properties.items() if k != "outcome"}}
    required = schema.get("required")
    if isinstance(required, list):
        new_schema["required"] = [r for r in required if r != "outcome"]
    return replace(tool_def, parameters_json_schema=new_schema)


async def _filter_tools(
    _ctx: RunContext[TurnDeps],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    """Hide admin tools and the ``outcome`` parameter before the model sees them."""
    visible = []
    for tool_def in tool_defs:
        if tool_def.name in _ADMIN_TOOLS:
            continue
        visible.append(
            replace(
                _strip_outcome(tool_def) if tool_def.name in _OUTCOME_TOOLS else tool_def,
                sequential=True,
            )
        )
    return visible


def _roll_options(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Rebuild the recorded ``options`` block from a ``resolve_outcome`` call's args."""
    labels = args.get("labels") or []
    weights = args.get("weights")
    if isinstance(weights, list) and len(weights) == len(labels):
        return [
            {"label": label, "weight": weight}
            for label, weight in zip(labels, weights, strict=False)
        ]
    return [{"label": label} for label in labels]


def _short(value: Any, limit: int = 80) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _result_status(result: ToolResult) -> str:
    if isinstance(result, dict):
        if "ok" in result:
            return "ok" if result.get("ok") else "rejected"
        rolled = result.get("result")
        if rolled is not None:
            return f"result={rolled}"
    elif isinstance(result, str):  # an unwrapped resolve_outcome label
        return f"result={_short(result)}"
    return "done"


def _effect_line(name: str, args: dict[str, Any]) -> str:
    """A past-tense, human-readable summary of one applied write, for the effects digest.

    Pure and total: reads args defensively so digest assembly can never break a turn.
    """
    slug = args.get("slug")
    if name == "propose_state_change":
        changes = args.get("changes") or {}
        fields = (
            ", ".join(f"{k}={v}" for k, v in changes.items())
            if isinstance(changes, dict)
            else _short(changes)
        )
        line = f"{slug} changed: {fields}"
    elif name == "propose_move":
        line = f"{slug} moved to {args.get('location')}"
    elif name == "propose_relationship_change":
        line = f"{args.get('holder')}→{args.get('target')}: {_short({k: args[k] for k in ('value', 'note') if k in args})}"
    elif name == "propose_create_entity":
        line = f"created {args.get('type')} {slug}"
    elif name == "propose_world_event":
        line = f"event: {args.get('narrative') or args.get('type')}"
    elif name in {"add_trait", "remove_trait"}:
        verb = "gained trait" if name == "add_trait" else "lost trait"
        line = f"{slug} {verb} {args.get('trait')}"
    elif name in {"add_condition", "remove_condition"}:
        verb = "now" if name == "add_condition" else "no longer"
        line = f"{slug} {verb} {args.get('condition')}"
    else:
        line = f"{name}: {_short(args)}"
    return line


async def _process_tool_call(
    ctx: RunContext[TurnDeps],
    call_tool: CallToolFunc,
    name: str,
    args: dict[str, Any],
) -> ToolResult:
    """Capture outcome rolls, attach them to writes, map rejections to retries, and trace."""
    trace = ctx.deps.trace
    if trace is not None:
        trace.write(f"tool→ {name}", _short(args))
    start = time.monotonic()
    if name == "resolve_outcome":
        result = await call_tool(name, args)
        if isinstance(result, dict) and result.get("error"):
            raise ModelRetry(str(result.get("error")))
        # FastMCP unwraps a single-key {"result": x} return to the bare value x, so the
        # rolled label arrives as a plain string here, not a dict — capture either shape.
        label = result.get("result") if isinstance(result, dict) else result
        ctx.deps.pending_roll = {"options": _roll_options(args), "result": label}
        ctx.deps.effects.append(f"roll: {label}")
    elif name in _WRITE_TOOLS:
        # Attach a pending roll only to outcome-bearing writes (trait/condition tools
        # take no ``outcome``); every write still maps a rejection to a ModelRetry.
        if name in _OUTCOME_TOOLS and ctx.deps.pending_roll is not None:
            args = {**args, "outcome": ctx.deps.pending_roll}
        result = await call_tool(name, args)
        if isinstance(result, dict) and result.get("ok") is False:
            reason = str(result.get("reason") or "change rejected")
            if trace is not None:
                trace.write(f"tool✗ {name}", f"{reason}  {time.monotonic() - start:.2f}s")
            raise ModelRetry(reason)
        ctx.deps.effects.append(_effect_line(name, args))
        if name in _OUTCOME_TOOLS:
            ctx.deps.pending_roll = None
    else:
        result = await call_tool(name, args)
    if trace is not None:
        trace.write(f"tool← {name}", f"{_result_status(result)}  {time.monotonic() - start:.2f}s")
    return result


def build_server() -> MCPToolset[TurnDeps]:
    """Build the single stdio MCP toolset (one subprocess, one active save)."""
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "aidm.mcp.server"],
        env=dict(os.environ),
    )
    return MCPToolset(
        transport,
        process_tool_call=_process_tool_call,
        max_retries=TOOL_RETRIES,
    )


def build_agent(model: Model, server: MCPToolset[TurnDeps]) -> Agent[TurnDeps, str]:
    """Build the resolver agent over ``model``, using ``server`` as its only toolset.

    The resolver resolves the player's action and mutates world state via the tools; its
    text output is discarded (the narrator produces all player-facing prose).
    """
    return Agent(
        model,
        toolsets=[server],
        instructions=RESOLVER_SYSTEM_PROMPT,
        deps_type=TurnDeps,
        retries=TOOL_RETRIES,
        capabilities=[PrepareTools(_filter_tools)],
    )


def build_narrator(model: Model) -> Agent[None, str]:
    """Build the no-tools narrator agent over ``model``.

    With no toolset its entire text output is the player-facing narration, so narration
    can never be lost to an interleaved tool call (the v2 ``result.output`` bug).
    """
    return Agent(model, instructions=NARRATOR_SYSTEM_PROMPT)
