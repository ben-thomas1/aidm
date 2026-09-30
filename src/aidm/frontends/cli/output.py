"""Rich console output with a semantic role theme.

System/error styles are used now for command output; the player/npc/exposition/
choice roles are wired for DM narration in Phase 5.
"""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

_THEME = Theme(
    {
        "system": "dim",
        "error": "bold red",
        "player": "bold cyan",
        "npc": "green",
        "exposition": "italic",
        "choice": "yellow",
    },
)

# No fixed ``file`` so the console tracks the live sys.stdout (patch_stdout-friendly).
console = Console(theme=_THEME, highlight=False, markup=False)


def system(text: str) -> None:
    """Print a neutral system/info message."""
    console.print(text, style="system")


def error(text: str) -> None:
    """Print an error message."""
    console.print(text, style="error")


def say(role: str, text: str) -> None:
    """Print narration text in the given semantic role's style."""
    console.print(text, style=role)


def narration(text: str) -> None:
    """Print DM narration with a blank line above and below for breathing room."""
    console.print()
    console.print(text, style="exposition")
    console.print()


class NarrationStreamPrinter:
    """Print narration fragments live as they stream, with surrounding breathing room.

    Pass :meth:`write` as the ``on_narration`` sink to ``Engine.run_turn``; call
    :meth:`close` once the turn returns to finish the line. ``markup=False`` keeps
    arbitrary model text (e.g. stray ``[`` / ``]``) from being parsed as Rich markup.
    """

    def __init__(self) -> None:
        self._started = False

    def write(self, fragment: str) -> None:
        """Print one streamed narration fragment (blank line precedes the first)."""
        if not self._started:
            console.print()
            self._started = True
        console.print(fragment, style="exposition", end="", markup=False)

    def close(self) -> None:
        """Finish the narration block (newline + trailing blank) if anything was printed."""
        if self._started:
            console.print("\n")
