"""A live, human-readable per-step trace of a DM turn (dev mode).

One timestamped line per step, flushed immediately, so a turn in flight can be watched
with ``tail -f saves/<run>/trace.log`` — revealing whether time is going into a slow model
generation or a hung tool. Best-effort: disabled (no-op) outside dev mode, and never raises
into gameplay.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class TurnTrace:
    """Append timestamped lines to a save's ``trace.log`` (no-op when ``path`` is None)."""

    def __init__(self, path: Path | None, turn: int) -> None:
        self._path = path
        self._turn = turn

    def write(self, event: str, detail: str = "") -> None:
        """Append ``HH:MM:SS.mmm  t<turn>  <event>  <detail>`` and flush; swallow IO errors."""
        if self._path is None:
            return
        stamp = datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3]
        line = f"{stamp}  t{self._turn}  {event}"
        if detail:
            line += f"  {detail}"
        # tracing must never break a turn
        with contextlib.suppress(OSError), self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
