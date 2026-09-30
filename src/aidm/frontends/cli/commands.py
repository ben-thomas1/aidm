"""Slash-command model and registry for the CLI REPL."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aidm.session import Context

if TYPE_CHECKING:
    from aidm.session import Screen


@dataclass(frozen=True)
class CommandResult:
    """Outcome of running a command.

    Parameters
    ----------
    message
        Text to print to the player, if any.
    should_quit
        When ``True``, the REPL exits after handling this result.
    goto
        When set, the REPL switches to this screen after handling the result.
    is_error
        When ``True``, the message is rendered in the error style.
    """

    message: str | None = None
    should_quit: bool = False
    goto: Screen | None = None
    is_error: bool = False


CommandHandler = Callable[[Context, Sequence[str]], CommandResult | Awaitable[CommandResult]]


@dataclass(frozen=True)
class Command:
    """A registered slash command."""

    name: str
    summary: str
    handler: CommandHandler


@dataclass
class CommandRegistry:
    """Registry of slash commands keyed by name."""

    _commands: dict[str, Command] = field(default_factory=dict)

    def register(self, name: str, summary: str, handler: CommandHandler) -> None:
        """Register a command, raising if the name is already taken."""
        if name in self._commands:
            msg = f"Command already registered: {name}"
            raise ValueError(msg)
        self._commands[name] = Command(name, summary, handler)

    def get(self, name: str) -> Command | None:
        """Return the command with ``name``, or ``None`` if unregistered."""
        return self._commands.get(name)

    def all_commands(self) -> list[Command]:
        """Return all registered commands, sorted by name."""
        return sorted(self._commands.values(), key=lambda c: c.name)
