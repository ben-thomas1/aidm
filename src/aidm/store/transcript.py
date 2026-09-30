"""Append-only narrative transcript (`transcript.jsonl`).

One record per turn — ``{turn, input, narration}`` — feeds the recent-history prompt.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from aidm.const import TRANSCRIPT_FILENAME
from aidm.paths import unlinked
from aidm.store.io import atomic_write_text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def transcript_path(save_dir: Path) -> Path:
    """Return the path to the save's ``transcript.jsonl``."""
    return unlinked(save_dir / TRANSCRIPT_FILENAME)


def _iter_records(path: Path) -> Iterator[dict[str, Any]]:
    """Yield valid records; reject damaged history without changing it."""
    if not path.is_file():
        return
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("turn"), int)
                or not all(isinstance(record.get(key), str) for key in ("input", "narration"))
            ):
                msg = "Invalid transcript record."
                raise ValueError(msg)  # noqa: TRY301 (one record error boundary)
        except ValueError:
            msg = f"Invalid transcript record at line {number}; restore the save from a backup."
            raise ValueError(msg) from None
        yield record


def read_transcript(save_dir: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """Read transcript records, optionally only the last ``limit``."""
    if limit is not None and limit < 0:
        msg = "limit must be nonnegative."
        raise ValueError(msg)
    if limit == 0:
        return []
    records = list(_iter_records(transcript_path(save_dir)))
    return records[-limit:] if limit is not None else records


def append_transcript(save_dir: Path, turn: int, player_input: str, narration: str) -> None:
    """Append one turn's player action and DM narration to the transcript."""
    path = transcript_path(save_dir)
    read_transcript(save_dir)  # validate before extending existing history
    record = {"turn": turn, "input": player_input, "narration": narration}
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    if previous and not previous.endswith("\n"):
        previous += "\n"
    atomic_write_text(path, previous + json.dumps(record) + "\n")
