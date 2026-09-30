"""Filesystem persistence: entities, events, projections, and metadata.

Operates on a loaded save directory. The Phase-3 MCP server wraps these helpers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aidm.store.entities import list_entities, load_entity, save_entity
from aidm.store.events import event_count, new_event, read_events
from aidm.store.meta import load_meta, save_meta
from aidm.store.scenario import load_scenario
from aidm.store.story import load_story, save_story
from aidm.store.transaction import append_events, recover_pending
from aidm.store.transcript import append_transcript, read_transcript

if TYPE_CHECKING:
    from pathlib import Path

    from aidm.domain import Event

__all__ = [
    "append_event",
    "append_events",
    "append_transcript",
    "event_count",
    "list_entities",
    "load_entity",
    "load_meta",
    "load_scenario",
    "load_story",
    "new_event",
    "read_events",
    "read_transcript",
    "recover_pending",
    "save_entity",
    "save_meta",
    "save_story",
]


def append_event(save_dir: Path, event: Event) -> bool:
    """Persist one validated projection and event with interruption recovery."""
    return append_events(save_dir, [event])
