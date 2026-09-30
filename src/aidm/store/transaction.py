"""Recoverable event batches for the single-writer JSON store.

A small redo journal holds validated final entity states and their events. Reload
finishes an interrupted write idempotently. This is not a whole-turn transaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from aidm.domain import Entity, Event
from aidm.paths import unlinked
from aidm.store.entities import entity_path, save_entity
from aidm.store.events import append_records, read_events
from aidm.store.io import atomic_write_text
from aidm.store.projections import project_event

if TYPE_CHECKING:
    from pathlib import Path

JOURNAL = ".pending-events.json"


class PendingEvents(BaseModel):
    """Validated redo record; no arbitrary destination paths are stored."""

    model_config = ConfigDict(extra="forbid")
    events: list[Event]
    entities: list[Entity]


def recover_pending(save_dir: Path) -> None:
    """Finish an interrupted batch, retaining its journal if recovery fails."""
    path = unlinked(save_dir / JOURNAL)
    if not path.exists():
        return
    pending = PendingEvents.model_validate_json(path.read_text(encoding="utf-8"))
    existing = {event.id: event for event in read_events(save_dir)}
    for event in pending.events:
        if event.id in existing and existing[event.id] != event:
            msg = "Conflicting event ID in recovery journal."
            raise ValueError(msg)
    for entity in pending.entities:
        save_entity(save_dir, entity)
    append_records(save_dir, [event for event in pending.events if event.id not in existing])
    path.unlink()


def append_events(save_dir: Path, events: list[Event]) -> bool:
    """Validate all projections, then persist one recoverable batch."""
    recover_pending(save_dir)
    existing = {event.id for event in read_events(save_dir)}
    states: dict[str, Entity] = {}
    for event in events:
        if event.id in existing:
            msg = "Duplicate event ID."
            raise ValueError(msg)
        existing.add(event.id)
        slug = event.targets[0] if event.targets else ""
        entity = project_event(save_dir, event, states.get(slug))
        if entity is not None:
            entity_path(save_dir, entity)  # reject unsafe paths before writing the journal
            states[entity.slug] = entity
    if not events:
        return False
    pending = PendingEvents(events=events, entities=list(states.values()))
    atomic_write_text(save_dir / JOURNAL, pending.model_dump_json() + "\n")
    recover_pending(save_dir)
    return bool(states)
