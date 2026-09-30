"""Dev-mode diagnostic commands, namespaced under ``/dev``.

Later build phases self-register diagnostics here (e.g. ``/dev entity <slug>``)
so each component can be exercised manually from the CLI as soon as it exists.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from aidm.frontends.cli.commands import CommandResult
from aidm.session import Context

DevHandler = Callable[[Context, Sequence[str]], CommandResult | Awaitable[CommandResult]]


@dataclass(frozen=True)
class DevCommand:
    """A diagnostic command addressed as ``/dev <component> <action>``."""

    component: str
    action: str
    summary: str
    handler: DevHandler

    @property
    def key(self) -> str:
        """Return the ``"<component> <action>"`` lookup key."""
        return f"{self.component} {self.action}"


@dataclass
class DevRegistry:
    """Registry of dev commands keyed by ``"<component> <action>"``."""

    _commands: dict[str, DevCommand] = field(default_factory=dict)

    def register(
        self,
        component: str,
        action: str,
        summary: str,
        handler: DevHandler,
    ) -> None:
        """Register a dev command, raising if the key is already taken."""
        command = DevCommand(component, action, summary, handler)
        if command.key in self._commands:
            msg = f"Dev command already registered: {command.key}"
            raise ValueError(msg)
        self._commands[command.key] = command

    def all_commands(self) -> list[DevCommand]:
        """Return all registered dev commands, sorted by key."""
        return sorted(self._commands.values(), key=lambda c: c.key)

    async def dispatch(self, ctx: Context, args: Sequence[str]) -> CommandResult:
        """Route ``/dev <component> <action> [args...]`` to a handler."""
        if len(args) < 2:
            return CommandResult(message=self.render_help())
        key = f"{args[0]} {args[1]}"
        command = self._commands.get(key)
        if command is None:
            return CommandResult(
                message=f"Unknown dev command: {key}. Type /dev to list diagnostics.",
            )
        result = command.handler(ctx, args[2:])
        if isinstance(result, CommandResult):
            return result
        return await result

    def render_help(self) -> str:
        """Return a listing of registered dev commands."""
        if not self._commands:
            return "No dev commands registered yet."
        lines = ["Dev commands:"]
        lines.extend(f"  /dev {c.key:<22} {c.summary}" for c in self.all_commands())
        return "\n".join(lines)
