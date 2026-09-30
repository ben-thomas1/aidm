"""Dev diagnostics for the store, registered under ``/dev store …`` in dev mode.

All commands operate on the currently loaded save.
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

from aidm import sim, store
from aidm.frontends.cli.commands import CommandResult
from aidm.mcp import tools

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aidm.frontends.cli.dev import DevRegistry
    from aidm.session import Context

_NO_GAME = CommandResult(message="No game loaded. Use /load or /new first.")


def _parse_value(raw: str) -> int | float | str:
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def _entities(ctx: Context, _args: Sequence[str]) -> CommandResult:
    if ctx.session.loaded_save is None:
        return _NO_GAME
    entities = store.list_entities(ctx.session.loaded_save)
    if not entities:
        return CommandResult(message="No entities.")
    return CommandResult(message="\n".join(f"  {e.slug} ({e.type})" for e in entities))


def _entity(ctx: Context, args: Sequence[str]) -> CommandResult:
    if ctx.session.loaded_save is None:
        return _NO_GAME
    if not args:
        return CommandResult(message="Usage: /dev store entity <slug>")
    try:
        entity = store.load_entity(ctx.session.loaded_save, args[0])
    except FileNotFoundError as exc:
        return CommandResult(message=str(exc))
    return CommandResult(message=json.dumps(entity.model_dump(), indent=2))


def _set(ctx: Context, args: Sequence[str]) -> CommandResult:
    if ctx.session.loaded_save is None:
        return _NO_GAME
    if len(args) < 3:
        return CommandResult(message="Usage: /dev store set <slug> <field> <value>")
    slug, field, value = args[0], args[1], _parse_value(args[2])
    result = tools.propose_state_change(ctx.session.loaded_save, slug, {field: value})
    return CommandResult(message=json.dumps(result), is_error=not result["ok"])


def _move(ctx: Context, args: Sequence[str]) -> CommandResult:
    if ctx.session.loaded_save is None:
        return _NO_GAME
    if len(args) < 2:
        return CommandResult(message="Usage: /dev store move <slug> <location>")
    slug, location = args[0], args[1]
    result = tools.propose_move(ctx.session.loaded_save, slug, location)
    return CommandResult(message=json.dumps(result), is_error=not result["ok"])


def _log(ctx: Context, args: Sequence[str]) -> CommandResult:
    if ctx.session.loaded_save is None:
        return _NO_GAME
    limit = 10
    if args:
        try:
            limit = int(args[0])
        except ValueError:
            return CommandResult(message="Usage: /dev store log [n]")
    events = store.read_events(ctx.session.loaded_save, limit=limit)
    if not events:
        return CommandResult(message="No events.")
    return CommandResult(
        message="\n".join(f"  {e.id} t{e.turn} {e.type} {e.targets} {e.payload}" for e in events),
    )


def _turn(ctx: Context, _args: Sequence[str]) -> CommandResult:
    if ctx.session.loaded_save is None:
        return _NO_GAME
    return CommandResult(message=f"turn {store.load_meta(ctx.session.loaded_save).current_turn}")


_NO_ENGINE = CommandResult(message="Engine is not available.")


async def _tool_list(ctx: Context, _args: Sequence[str]) -> CommandResult:
    if ctx.engine is None:
        return _NO_ENGINE
    try:
        names = await ctx.engine.list_tools()
    except Exception as exc:  # noqa: BLE001 (surface transport/startup errors)
        return CommandResult(message=f"MCP error: {exc}")
    return CommandResult(message="Tools:\n" + "\n".join(f"  {n}" for n in names))


async def _tool_call(ctx: Context, args: Sequence[str]) -> CommandResult:
    if ctx.engine is None:
        return _NO_ENGINE
    if not args:
        return CommandResult(message="Usage: /dev tool call <name> <json>")
    name = args[0]
    raw = " ".join(args[1:]) or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return CommandResult(message=f"Invalid JSON: {exc}")
    try:
        result = await ctx.engine.call_tool(name, parsed)
    except Exception as exc:  # noqa: BLE001 (surface any tool/transport error to the user)
        return CommandResult(message=f"Tool error: {exc}")
    return CommandResult(message=json.dumps(result, indent=2, default=str))


async def _llm_raw(_ctx: Context, args: Sequence[str]) -> CommandResult:
    if not args:
        return CommandResult(message="Usage: /dev llm raw <prompt>")
    from pydantic_ai import Agent  # noqa: PLC0415 (lazy: keep heavy deps off startup)

    from aidm.llm import build_model  # noqa: PLC0415

    try:
        async with AsyncExitStack() as stack:
            result = await Agent(build_model(stack)).run(" ".join(args))
    except Exception as exc:  # noqa: BLE001 (surface any LLM/transport error to the user)
        return CommandResult(message=f"LLM error: {exc}", is_error=True)
    return CommandResult(message=result.output)


def _llm_context(ctx: Context, _args: Sequence[str]) -> CommandResult:
    if ctx.session.loaded_save is None:
        return _NO_GAME
    from aidm.orchestrator import assembler  # noqa: PLC0415 (avoid import cycle at module load)

    return CommandResult(message=assembler.build_context(ctx.session.loaded_save))


async def _sim_tick(ctx: Context, _args: Sequence[str]) -> CommandResult:
    if ctx.session.loaded_save is None:
        return _NO_GAME
    if ctx.engine is None:
        return _NO_ENGINE
    await sim.tick(ctx.session.loaded_save, ctx.engine.generate)
    turn = store.load_meta(ctx.session.loaded_save).current_turn
    return CommandResult(message=f"Ticked the world. Now at turn {turn}.")


async def _sim_catchup(ctx: Context, args: Sequence[str]) -> CommandResult:
    if ctx.session.loaded_save is None:
        return _NO_GAME
    if ctx.engine is None:
        return _NO_ENGINE
    if not args:
        return CommandResult(message="Usage: /dev sim catchup <slug>")
    try:
        did = sim.catch_up(ctx.session.loaded_save, args[0])
    except FileNotFoundError as exc:
        return CommandResult(message=str(exc))
    return CommandResult(
        message=f"Caught up {args[0]}." if did else f"{args[0]} is already current."
    )


async def _sim_bgroll(ctx: Context, _args: Sequence[str]) -> CommandResult:
    if ctx.session.loaded_save is None:
        return _NO_GAME
    if ctx.engine is None:
        return _NO_ENGINE
    description = await sim.fire_background(ctx.session.loaded_save, ctx.engine.generate)
    return CommandResult(message=f"Background event recorded: {description}")


def register_dev_commands(dev_registry: DevRegistry) -> None:
    """Register the ``/dev store …`` and ``/dev tool …`` diagnostics."""
    dev_registry.register("store", "entities", "List entities.", _entities)
    dev_registry.register("store", "entity", "Show an entity: /dev store entity <slug>.", _entity)
    dev_registry.register(
        "store", "set", "Set a field: /dev store set <slug> <field> <value>.", _set
    )
    dev_registry.register(
        "store", "move", "Move an entity: /dev store move <slug> <location>.", _move
    )
    dev_registry.register("store", "log", "Recent events: /dev store log [n].", _log)
    dev_registry.register("store", "turn", "Show the current turn.", _turn)
    dev_registry.register("tool", "list", "List MCP tools.", _tool_list)
    dev_registry.register(
        "tool", "call", "Call an MCP tool: /dev tool call <name> <json>.", _tool_call
    )
    dev_registry.register(
        "llm", "raw", "Raw LLM completion (no tools): /dev llm raw <prompt>.", _llm_raw
    )
    dev_registry.register("llm", "context", "Dump the assembled DM context.", _llm_context)
    dev_registry.register(
        "sim", "tick", "Force one world tick (regen + bg roll + turn).", _sim_tick
    )
    dev_registry.register(
        "sim", "catchup", "Catch up an entity: /dev sim catchup <slug>.", _sim_catchup
    )
    dev_registry.register("sim", "bgroll", "Force a background event now.", _sim_bgroll)
