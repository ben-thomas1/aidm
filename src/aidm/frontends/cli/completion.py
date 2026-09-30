"""Context-aware command completion, rebuilt fresh for each prompt."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_toolkit.completion import Completer, Completion

from aidm import saveload, store

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Any

    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from aidm.frontends.cli.commands import CommandRegistry
    from aidm.frontends.cli.dev import DevRegistry
    from aidm.session import Context, Screen

_SAVE_ARG_COMMANDS = {"load", "delete"}


class CommandCompleter(Completer):
    """Whitespace-token tree completer.

    Walks a nested ``spec`` (``{token: subspec | None}``) by the completed tokens and
    completes the partial last token against the current level's keys. Unlike
    ``NestedCompleter`` it matches the whole token, so the leading ``/`` of a command is
    completed correctly (prompt_toolkit's word boundary otherwise excludes ``/``).
    """

    def __init__(self, spec: dict[str, Any]) -> None:
        self._spec = spec

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """Yield completions for the partial last token at the matching spec level."""
        parts = document.text_before_cursor.lstrip().split(" ")
        node: Any = self._spec
        for part in parts[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
            if not isinstance(node, dict):
                return
        if not isinstance(node, dict):
            return
        word = parts[-1]
        for key in node:
            if key.startswith(word):
                yield Completion(key, start_position=-len(word))


def build_completer(
    ctx: Context,
    registries: dict[Screen, CommandRegistry],
    dev_registry: DevRegistry | None,
) -> CommandCompleter:
    """Build a completer for the current screen + live save/game/entity/tool data."""
    saves = dict.fromkeys(saveload.list_saves())
    games = dict.fromkeys(saveload.list_games())
    entities = _entity_names(ctx)
    spec: dict[str, Any] = {}
    for command in registries[ctx.session.screen].all_commands():
        key = f"/{command.name}"
        if command.name in _SAVE_ARG_COMMANDS:
            spec[key] = dict(saves)
        elif command.name == "new":
            spec[key] = dict(games)
        elif command.name == "dev" and dev_registry is not None:
            spec[key] = _dev_spec(ctx, dev_registry, entities)
        else:
            spec[key] = None
    return CommandCompleter(spec)


def _dev_spec(
    ctx: Context,
    dev_registry: DevRegistry,
    entities: dict[str, None],
) -> dict[str, Any]:
    spec: dict[str, Any] = {}
    for command in dev_registry.all_commands():
        actions = spec.setdefault(command.component, {})
        if command.component == "store" and command.action in {"entity", "set", "move"}:
            actions[command.action] = dict(entities)
        elif command.component == "tool" and command.action == "call":
            actions[command.action] = dict.fromkeys(_tool_names(ctx))
        else:
            actions[command.action] = None
    return spec


def _entity_names(ctx: Context) -> dict[str, None]:
    save = ctx.session.loaded_save
    if save is None:
        return {}
    return {entity.slug: None for entity in store.list_entities(save)}


def _tool_names(ctx: Context) -> list[str]:
    if ctx.engine is None:
        return []
    return ctx.engine.tool_names()
