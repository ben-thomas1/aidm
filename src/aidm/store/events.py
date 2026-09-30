"""Append-only event log read/write (`events/log.jsonl`)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from aidm.const import EVENTS_DIR, LOG_FILENAME
from aidm.domain import Event
from aidm.paths import unlinked
from aidm.store.io import atomic_write_text
from aidm.store.meta import load_meta

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path


def log_path(save_dir: Path) -> Path:
    """Return the path to the event log."""
    return unlinked(save_dir / EVENTS_DIR / LOG_FILENAME)


def _iter_events(path: Path) -> Iterator[Event]:
    """Read strictly: damaged history must not be silently discarded or extended."""
    if not path.is_file():
        return
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = Event.model_validate_json(line)
        except ValueError:
            msg = f"Invalid event log record at line {number}; restore the save from a backup."
            raise ValueError(msg) from None
        if event.id in seen:
            msg = f"Duplicate event ID at line {number}."
            raise ValueError(msg)
        seen.add(event.id)
        yield event


def read_events(save_dir: Path, limit: int | None = None) -> list[Event]:
    """Read events from the log, optionally only the last ``limit``."""
    if limit is not None and limit < 0:
        msg = "limit must be nonnegative."
        raise ValueError(msg)
    if limit == 0:
        return []
    events = list(_iter_events(log_path(save_dir)))
    return events[-limit:] if limit is not None else events


def event_count(save_dir: Path) -> int:
    """Return the number of (parseable) events in the log."""
    return sum(1 for _ in _iter_events(log_path(save_dir)))


def new_event(
    save_dir: Path,
    event_type: str,
    *,
    actor: str | None = None,
    targets: Sequence[str] | None = None,
    payload: dict[str, Any] | None = None,
    outcome: dict[str, Any] | None = None,
    caused_by: str | None = None,
    covers_turns: tuple[int, int] | None = None,
) -> Event:
    """Build an event stamped with the next id, the current turn, and a timestamp."""
    from aidm.store.transaction import recover_pending  # noqa: PLC0415 (break store cycle)

    recover_pending(save_dir)
    return Event(
        id=f"evt_{event_count(save_dir) + 1:04d}",
        turn=load_meta(save_dir).current_turn,
        ts=datetime.now(UTC).isoformat(),
        type=event_type,
        actor=actor,
        targets=list(targets) if targets else [],
        payload=payload or {},
        outcome=outcome,
        caused_by=caused_by,
        covers_turns=covers_turns,
    )


def append_record(save_dir: Path, event: Event) -> None:
    """Append a single event to the log (no projection)."""
    append_records(save_dir, [event])


def append_records(save_dir: Path, events: list[Event]) -> None:
    """Extend the logical append-only log with an atomic file replacement."""
    if not events:
        return
    path = log_path(save_dir)
    existing = read_events(save_dir)
    ids = [event.id for event in [*existing, *events]]
    if len(set(ids)) != len(ids):
        msg = "Duplicate event ID."
        raise ValueError(msg)
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    if previous and not previous.endswith("\n"):
        previous += "\n"
    atomic_write_text(path, previous + "".join(event.model_dump_json() + "\n" for event in events))
