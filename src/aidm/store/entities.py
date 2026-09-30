"""Load, save, and list entity files within a save directory.

Entities live in per-type subdirectories (``characters/``, ``locations/``, ...).
Slugs are globally unique, so an entity can be found by slug alone.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from aidm.const import EVENTS_DIR
from aidm.domain import Entity
from aidm.paths import identifier, unlinked
from aidm.store.io import atomic_write_text

if TYPE_CHECKING:
    from pathlib import Path


def _entity_dirs(save_dir: Path) -> list[Path]:
    unlinked(save_dir)
    return [unlinked(p) for p in save_dir.iterdir() if p.is_dir() and p.name != EVENTS_DIR]


def entity_path(save_dir: Path, entity: Entity) -> Path:
    """Return the on-disk path for ``entity`` (``<type>s/<slug>.json``)."""
    return unlinked(save_dir / f"{identifier(entity.type)}s" / f"{identifier(entity.slug)}.json")


def find_entity_path(save_dir: Path, slug: str) -> Path | None:
    """Find an entity file by slug across all type subdirectories."""
    identifier(slug)
    matches = []
    for directory in _entity_dirs(save_dir):
        candidate = unlinked(directory / f"{slug}.json")
        if candidate.is_file():
            matches.append(candidate)
    if len(matches) > 1:
        msg = f"Duplicate entity slug: {slug}"
        raise ValueError(msg)
    return matches[0] if matches else None


def load_entity(save_dir: Path, slug: str) -> Entity:
    """Load an entity by slug, raising if it does not exist."""
    path = find_entity_path(save_dir, slug)
    if path is None:
        msg = f"No such entity: {slug}"
        raise FileNotFoundError(msg)
    entity = Entity.model_validate_json(path.read_text(encoding="utf-8"))
    if entity_path(save_dir, entity) != path:
        msg = "Entity identity does not match its file path."
        raise ValueError(msg)
    return entity


def save_entity(save_dir: Path, entity: Entity) -> None:
    """Write an entity to its type subdirectory."""
    path = entity_path(save_dir, entity)
    atomic_write_text(path, json.dumps(entity.model_dump(), indent=2) + "\n")


def list_entities(save_dir: Path) -> list[Entity]:
    """Load every entity in the save, sorted by directory then slug."""
    entities: list[Entity] = []
    for directory in sorted(_entity_dirs(save_dir), key=lambda d: d.name):
        entities.extend(
            load_entity(save_dir, path.stem) for path in sorted(directory.glob("*.json"))
        )
    return entities
