"""prompt_toolkit session: styled prompt, history, completion, and status toolbar."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

from aidm.const import GAME_PROMPT, HISTORY_FILENAME, WELCOME_PROMPT
from aidm.frontends.cli.completion import build_completer
from aidm.session import Screen

if TYPE_CHECKING:
    from prompt_toolkit.formatted_text import StyleAndTextTuples

    from aidm.frontends.cli.commands import CommandRegistry
    from aidm.frontends.cli.dev import DevRegistry
    from aidm.session import Context

_STYLE = Style.from_dict(
    {
        "prompt": "bold ansicyan",
        "prompt-game": "bold ansigreen",
        "bottom-toolbar": "bg:#222222 #aaaaaa",
    },
)


class SlashCommandFileHistory(FileHistory):
    """File history that persists only slash commands.

    Free-text gameplay input stays navigable within the session (kept in memory
    by the base class) but is never written to the history file.
    """

    def store_string(self, string: str) -> None:
        """Append ``string`` to the file only if it is a slash command."""
        if string.strip().startswith("/"):
            super().store_string(string)


def make_session() -> PromptSession[str]:
    """Create the shared prompt session (history, auto-suggest, style)."""
    return PromptSession(
        history=SlashCommandFileHistory(HISTORY_FILENAME),
        auto_suggest=AutoSuggestFromHistory(),
        style=_STYLE,
        complete_while_typing=True,
    )


def _prompt_message(ctx: Context) -> StyleAndTextTuples:
    if ctx.session.screen is Screen.GAME:
        return [("class:prompt-game", GAME_PROMPT)]
    return [("class:prompt", WELCOME_PROMPT)]


def _toolbar(ctx: Context) -> StyleAndTextTuples:
    screen = "in-game" if ctx.session.screen is Screen.GAME else "welcome"
    parts = [screen]
    if ctx.session.loaded_save is not None:
        parts.append(f"save: {ctx.session.loaded_save.name}")
    engine_status = "ready" if (ctx.engine is not None and ctx.engine.is_ready) else "idle"
    parts.append(f"engine: {engine_status}")
    return [("class:bottom-toolbar", f" {' | '.join(parts)} ")]


async def read_line(
    session: PromptSession[str],
    ctx: Context,
    registries: dict[Screen, CommandRegistry],
    dev_registry: DevRegistry | None,
) -> str:
    """Read one line: full UX on a TTY, plain ``input`` when piped/non-interactive."""
    if not sys.stdin.isatty():
        return await asyncio.to_thread(input)
    completer = build_completer(ctx, registries, dev_registry)
    with patch_stdout():
        return await session.prompt_async(
            _prompt_message(ctx),
            completer=completer,
            bottom_toolbar=lambda: _toolbar(ctx),
        )
