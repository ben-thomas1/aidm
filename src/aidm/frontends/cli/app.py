"""AI Dungeon Master terminal REPL entrypoint (async runtime)."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from aidm.config import get_config, init_config
from aidm.frontends.cli import output
from aidm.frontends.cli.commands import CommandResult
from aidm.frontends.cli.dev import DevRegistry
from aidm.frontends.cli.dev_commands import register_dev_commands
from aidm.frontends.cli.prompt import make_session, read_line
from aidm.frontends.cli.screens import build_game_registry, build_welcome_registry
from aidm.orchestrator import Engine
from aidm.session import Context, Screen, Session

if TYPE_CHECKING:
    from aidm.frontends.cli.commands import CommandRegistry


async def handle_line(line: str, registry: CommandRegistry, ctx: Context) -> CommandResult:  # noqa: PLR0911 (command dispatch)
    """Interpret one line of REPL input on the current screen."""
    stripped = line.strip()
    if not stripped.startswith("/"):
        if not stripped:
            return CommandResult()
        if ctx.session.screen is Screen.GAME:
            return await _play_turn(ctx, stripped)
        return CommandResult(message="Type /help for commands.")
    parts = stripped[1:].split()
    if not parts:
        return CommandResult(message="Empty command. Type /help.", is_error=True)
    name, *args = parts
    command = registry.get(name) if name else None
    if command is None:
        hint = f"Unknown command: /{name}. Type /help." if name else "Empty command. Type /help."
        return CommandResult(message=hint, is_error=True)
    try:
        result = command.handler(ctx, args)
        return result if isinstance(result, CommandResult) else await result
    except OSError, ValueError:
        return CommandResult(
            message="Command failed: invalid input or unreadable save. Check the name and save files.",
            is_error=True,
        )


async def _play_turn(ctx: Context, text: str) -> CommandResult:
    """Run one DM turn from free-text input; render narration and handle end-game."""
    if ctx.engine is None or ctx.session.loaded_save is None:
        return CommandResult(message="No game loaded.", is_error=True)
    printer = output.NarrationStreamPrinter()
    try:
        result = await ctx.engine.run_turn(
            ctx.session.loaded_save, text, on_narration=printer.write
        )
    except OSError, ValueError:
        await ctx.engine.clear_active_save()
        ctx.session.loaded_save = None
        return CommandResult(
            message="Turn interrupted by a save error. Reload to recover; earlier changes may be saved.",
            is_error=True,
            goto=Screen.WELCOME,
        )
    finally:
        printer.close()
    if result.error is not None:
        output.error(result.error)
    if result.ended:
        output.say("exposition", "Your story ends here — your character has died.")
        await ctx.engine.clear_active_save()
        ctx.session.loaded_save = None
        return CommandResult(goto=Screen.WELCOME)
    return CommandResult()


async def run_repl(
    registries: dict[Screen, CommandRegistry],
    ctx: Context,
    dev_registry: DevRegistry | None,
) -> None:
    """Run the read-eval-print loop, dispatching to the current screen."""
    config = get_config()
    output.system("AI Dungeon Master [dev]" if config.features.dev_mode else "AI Dungeon Master")
    output.system("Type /help for commands, /quit to exit.")
    session = make_session()
    while True:
        try:
            line = await read_line(session, ctx, registries, dev_registry)
        except KeyboardInterrupt:  # noqa: S112 (Ctrl-C cancels the current line)
            continue
        except EOFError:
            break
        result = await handle_line(line, registries[ctx.session.screen], ctx)
        if result.message is not None:
            (output.error if result.is_error else output.system)(result.message)
        if result.goto is not None:
            ctx.session.screen = result.goto
        if result.should_quit:
            break


async def _main() -> None:
    parser = argparse.ArgumentParser(prog="aidm", description="AI Dungeon Master")
    parser.add_argument("-c", "--config", help="Path to the config file (default: ./config.json).")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Enable dev diagnostic commands (also via AIDM_DEV=1).",
    )
    parsed = parser.parse_args()
    config_path = Path(parsed.config) if parsed.config else None
    try:
        config = init_config(dev=parsed.dev, config_path=config_path)
    except (OSError, ValueError) as exc:
        output.error(
            "Failed to load config. Use a valid JSON config with an LLM endpoint; see examples/."
        )
        raise SystemExit(1) from exc

    dev_registry = DevRegistry() if config.features.dev_mode else None
    if dev_registry is not None:
        register_dev_commands(dev_registry)
    registries = {
        Screen.WELCOME: build_welcome_registry(dev_registry),
        Screen.GAME: build_game_registry(dev_registry),
    }
    ctx = Context(session=Session(screen=Screen.WELCOME))
    ctx.engine = Engine()  # connects lazily on first /load, /new, or turn
    try:
        await run_repl(registries, ctx, dev_registry)
    finally:
        await ctx.engine.aclose()


def main() -> None:
    """CLI entrypoint."""
    asyncio.run(_main())
