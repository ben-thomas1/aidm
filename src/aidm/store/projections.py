"""Apply events to entity state (maintained projections).

A small set of event types is projected; unknown types remain narrative records.
Projection calculation is pure so batches can be validated before persistence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aidm.const import MAX_CONDITIONS, MAX_TRAITS
from aidm.domain import Entity
from aidm.store.entities import load_entity

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from aidm.domain import Event


def _first_target(event: Event) -> str:
    if not event.targets:
        msg = f"Event {event.type} requires a target entity"
        raise ValueError(msg)
    return event.targets[0]


def _entity_created(entity: Entity | None, event: Event) -> Entity:
    return Entity.model_validate(event.payload)


def _state_changed(entity: Entity, event: Event) -> Entity:
    data = entity.model_dump()
    field = event.payload["field"]
    if "value" in event.payload:
        data[field] = event.payload["value"]
    elif "delta" in event.payload:
        data[field] = (data.get(field) or 0) + event.payload["delta"]
    return Entity.model_validate(data)


def _moved(entity: Entity, event: Event) -> Entity:
    data = entity.model_dump()
    data["current_location"] = event.payload["to"]
    return Entity.model_validate(data)


def _relationship_changed(entity: Entity, event: Event) -> Entity:
    data = entity.model_dump()
    relationships: dict[str, Any] = dict(data.get("relationships") or {})
    target = event.payload["target"]
    relationship: dict[str, Any] = dict(relationships.get(target) or {})
    if "value" in event.payload:
        relationship["value"] = event.payload["value"]
    elif "delta" in event.payload:
        relationship["value"] = (relationship.get("value") or 0) + event.payload["delta"]
    if "note" in event.payload:
        relationship["note"] = event.payload["note"]
    relationships[target] = relationship
    data["relationships"] = relationships
    return Entity.model_validate(data)


def _simulated(entity: Entity, event: Event) -> Entity:
    """Apply a simulation step: set fields in ``changes`` and fold ``summary`` into memory."""
    data = entity.model_dump()
    data.update(event.payload.get("changes") or {})
    summary = event.payload.get("summary")
    if summary:
        existing = data.get("mindset") or ""
        entry = f"[t{event.turn}] {summary}"
        data["mindset"] = f"{existing}\n{entry}" if existing else entry
    return Entity.model_validate(data)


def _apply_tag(data: dict[str, Any], field: str, event: Event, cap: int) -> None:
    """Add/remove a tag in ``data[field]`` with case-insensitive dedupe and a cap."""
    current = [str(t) for t in (data.get(field) or []) if isinstance(t, str)]
    value = str(event.payload.get("value") or "").strip()
    op = event.payload.get("op")
    if not value or op not in {"add", "remove"}:
        msg = "A tag change requires a nonempty value and add/remove operation."
        raise ValueError(msg)
    lowered = {t.lower() for t in current}
    if op == "add" and value.lower() not in lowered and len(current) >= cap:
        msg = f"{field} has reached its limit of {cap}."
        raise ValueError(msg)
    if op == "add" and value.lower() not in lowered and len(current) < cap:
        current.append(value)
    elif op == "remove":
        current = [t for t in current if t.lower() != value.lower()]
    data[field] = current


def _trait_changed(entity: Entity, event: Event) -> Entity:
    data = entity.model_dump()
    _apply_tag(data, "traits", event, MAX_TRAITS)
    return Entity.model_validate(data)


def _condition_changed(entity: Entity, event: Event) -> Entity:
    data = entity.model_dump()
    _apply_tag(data, "conditions", event, MAX_CONDITIONS)
    return Entity.model_validate(data)


_HANDLERS: dict[str, Callable[[Entity, Event], Entity]] = {
    "state_changed": _state_changed,
    "moved": _moved,
    "relationship_changed": _relationship_changed,
    "simulated": _simulated,
    "trait_changed": _trait_changed,
    "condition_changed": _condition_changed,
}


PROJECTED_TYPES = frozenset(_HANDLERS) | {"entity_created"}


def project_event(save_dir: Path, event: Event, entity: Entity | None = None) -> Entity | None:
    """Calculate and validate the next entity state without writing any files."""
    if event.type == "entity_created":
        return _entity_created(None, event)
    handler = _HANDLERS.get(event.type)
    if handler is None:
        return None
    current = entity if entity is not None else load_entity(save_dir, _first_target(event))
    return handler(current, event)
