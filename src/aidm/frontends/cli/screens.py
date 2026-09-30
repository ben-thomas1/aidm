"""Per-screen command sets for the CLI (welcome menu and in-game)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aidm import saveload, store
from aidm.frontends.cli import output, views
from aidm.frontends.cli.commands import CommandRegistry, CommandResult
from aidm.session import Screen

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from aidm.frontends.cli.commands import CommandHandler
    from aidm.frontends.cli.dev import DevRegistry
    from aidm.session import Context


def _make_help(registry: CommandRegistry) -> CommandHandler:
    def handler(_ctx: Context, _args: Sequence[str]) -> CommandResult:
        lines = ["Commands:"]
        lines.extend(f"  /{c.name:<8} {c.summary}" for c in registry.all_commands())
        return CommandResult(message="\n".join(lines))

    return handler


def _quit(_ctx: Context, _args: Sequence[str]) -> CommandResult:
    return CommandResult(message="Goodbye.", should_quit=True)


def _list_games(_ctx: Context, _args: Sequence[str]) -> CommandResult:
    games = saveload.list_games()
    if not games:
        return CommandResult(message="No games found in games/.")
    return CommandResult(message="Games:\n" + "\n".join(f"  {g}" for g in games))


def _list_saves(_ctx: Context, _args: Sequence[str]) -> CommandResult:
    saves = saveload.list_saves()
    if not saves:
        return CommandResult(message="No saves yet. Start one with /new <game> <save>.")
    return CommandResult(message="Saves:\n" + "\n".join(f"  {s}" for s in saves))


async def _activate_save(ctx: Context, path: Path) -> bool:
    """Enter a save only after validation and successful server activation."""
    ctx.session.loaded_save = None
    saveload.validate_save(path)
    if ctx.engine is None or not await ctx.engine.set_active_save(path):
        return False
    ctx.session.loaded_save = path
    return True


def _show_intro(save_dir: Path) -> None:
    """Print the scenario framing and opening scene (no LLM) when a game begins."""
    scenario = store.load_scenario(save_dir)
    output.say("player", scenario.title)
    if scenario.synopsis:
        output.narration(scenario.synopsis)
    goal = scenario.goal
    if isinstance(goal, dict) and isinstance(goal.get("description"), str):
        output.say("choice", f"Goal: {goal['description']}")
    output.narration(views.scene(save_dir))


async def _new_game(ctx: Context, args: Sequence[str]) -> CommandResult:
    if len(args) < 2:
        return CommandResult(message="Usage: /new <game> <save>")
    game, save = args[0], args[1]
    if not saveload.game_exists(game):
        return CommandResult(message=f"No such game: {game}. Try /games.")
    if saveload.save_exists(save):
        return CommandResult(message=f"Save already exists: {save}.")
    path = saveload.create_save(game, save)
    if not await _activate_save(ctx, path):
        return CommandResult(
            message="Save created, but activation failed. Check configuration and /load it again.",
            is_error=True,
        )
    output.system(f"Started '{save}' from '{game}'.")
    _show_intro(path)
    return CommandResult(goto=Screen.GAME)


async def _load_save(ctx: Context, args: Sequence[str]) -> CommandResult:
    if not args:
        return CommandResult(message="Usage: /load <save>")
    save = args[0]
    if not saveload.save_exists(save):
        return CommandResult(message=f"No such save: {save}. Try /saves.")
    path = saveload.save_path(save)
    if not await _activate_save(ctx, path):
        return CommandResult(
            message="Save activation failed. Check configuration and try again.", is_error=True
        )
    output.system(f"Loaded '{save}'.")
    _show_intro(path)
    return CommandResult(goto=Screen.GAME)


async def _delete_save(ctx: Context, args: Sequence[str]) -> CommandResult:
    if len(args) != 2 or args[1] != "--confirm":
        return CommandResult(message="Permanently delete a save with /delete <save> --confirm")
    name = args[0]
    if not saveload.save_exists(name):
        return CommandResult(message=f"No such save: {name}. Try /saves.")
    saveload.delete_save(name)
    if ctx.session.loaded_save is not None and ctx.session.loaded_save.name == name:
        ctx.session.loaded_save = None
        if ctx.engine is not None:
            await ctx.engine.clear_active_save()
    return CommandResult(message=f"Deleted save '{name}'.")


def _inspect(render: Callable[[Path], str]) -> CommandHandler:
    """Build an in-game handler that renders read-only state from the loaded save."""

    def handler(ctx: Context, _args: Sequence[str]) -> CommandResult:
        if ctx.session.loaded_save is None:
            return CommandResult(message="No game loaded.", is_error=True)
        return CommandResult(message=render(ctx.session.loaded_save))

    return handler


async def _to_menu(ctx: Context, _args: Sequence[str]) -> CommandResult:
    ctx.session.loaded_save = None
    if ctx.engine is not None:
        await ctx.engine.clear_active_save()
    return CommandResult(message="Returned to the main menu.", goto=Screen.WELCOME)


def _register_dev(registry: CommandRegistry, dev_registry: DevRegistry | None) -> None:
    if dev_registry is not None:
        registry.register(
            "dev",
            "Run a dev diagnostic: /dev <component> <action>.",
            dev_registry.dispatch,
        )


def build_welcome_registry(dev_registry: DevRegistry | None) -> CommandRegistry:
    """Build the welcome-screen command set."""
    registry = CommandRegistry()
    registry.register("help", "List available commands.", _make_help(registry))
    registry.register("games", "List available games.", _list_games)
    registry.register("saves", "List your saves.", _list_saves)
    registry.register("new", "Start a new game: /new <game> <save>.", _new_game)
    registry.register("load", "Load a save: /load <save>.", _load_save)
    registry.register("delete", "Delete a save: /delete <save> --confirm.", _delete_save)
    registry.register("quit", "Exit aidm.", _quit)
    registry.register("exit", "Exit aidm (alias for /quit).", _quit)
    _register_dev(registry, dev_registry)
    return registry


def build_game_registry(dev_registry: DevRegistry | None) -> CommandRegistry:
    """Build the in-game command set."""
    registry = CommandRegistry()
    registry.register("help", "List available commands.", _make_help(registry))
    registry.register("look", "Describe where you are and who's here.", _inspect(views.scene))
    registry.register("status", "Show your character's vitals.", _inspect(views.status))
    registry.register("quests", "List your quests.", _inspect(views.quests))
    registry.register("inventory", "List items you carry.", _inspect(views.inventory))
    registry.register("menu", "Return to the main menu.", _to_menu)
    registry.register("quit", "Exit aidm.", _quit)
    registry.register("exit", "Exit aidm (alias for /quit).", _quit)
    _register_dev(registry, dev_registry)
    return registry
