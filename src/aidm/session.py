"""Runtime session state for a play session."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from aidm.orchestrator import Engine


class Screen(Enum):
    """The CLI screen the player is currently on."""

    WELCOME = "welcome"
    GAME = "game"


@dataclass
class Session:
    """Mutable per-session state: current screen and loaded save."""

    screen: Screen
    loaded_save: Path | None = None


@dataclass
class Context:
    """Execution context handed to command handlers.

    Holds the session and the orchestrator engine (LLM + MCP).
    """

    session: Session
    engine: Engine | None = None
