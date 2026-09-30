"""Pure tool implementations over the store, wrapped by the MCP server.

Each takes an explicit ``save_dir`` so it is unit-testable without a live server;
the server passes its active save. Write tools validate, then append + project.
"""

from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING

from aidm import store
from aidm.mcp.validation import validate
from aidm.store.projections import PROJECTED_TYPES

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from aidm.domain import Event


# ---- read tools -------------------------------------------------------------


def _normalize_fields(fields: list[str] | str | None) -> list[str] | None:
    """Accept ``fields`` as a list or a comma-separated string (the model sends both)."""
    if isinstance(fields, str):
        return [part.strip() for part in fields.split(",") if part.strip()]
    if isinstance(fields, list):
        return [str(field) for field in fields]
    return None


def get_entity(
    save_dir: Path,
    slug: str,
    fields: list[str] | str | None = None,
) -> dict[str, Any]:
    """Return an entity's fields, optionally a subset (list or comma-separated string)."""
    try:
        entity = store.load_entity(save_dir, slug)
    except FileNotFoundError:
        return {"error": "not_found", "slug": slug}
    data = entity.model_dump()
    names = _normalize_fields(fields)
    if names:
        return {k: data[k] for k in names if k in data}
    return data


def get_relationship(save_dir: Path, holder: str, target: str) -> dict[str, Any]:
    """Return the holder's directional relationship toward target."""
    relationships = get_relationships(save_dir, holder)
    value = relationships.get(target)
    return value if isinstance(value, dict) else {}


def get_relationships(save_dir: Path, holder: str) -> dict[str, Any]:
    """Return all of the holder's directional relationships."""
    try:
        entity = store.load_entity(save_dir, holder)
    except FileNotFoundError:
        return {}
    relationships = entity.model_dump().get("relationships")
    return relationships if isinstance(relationships, dict) else {}


_FIND_KEYS = ("type", "current_location", "status", "name", "owner")


def _entity_matches_filters(data: dict[str, Any], criteria: dict[str, Any]) -> bool:
    for key, want in criteria.items():
        if key == "name":
            name = data.get("name")
            if not isinstance(name, str) or str(want).lower() not in name.lower():
                return False
        elif data.get(key) != want:
            return False
    return True


def find_entities(save_dir: Path, filters: dict[str, Any] | None = None) -> list[str]:
    """Return slugs of entities matching all of the given field filters.

    Supported keys: ``type``, ``current_location``, ``status`` (exact match) and
    ``name`` (case-insensitive substring). Unknown keys raise ``ValueError`` so a
    typo doesn't silently match every entity.
    """
    criteria = filters or {}
    unknown = set(criteria) - set(_FIND_KEYS)
    if unknown:
        msg = f"unknown filter keys: {sorted(unknown)} (supported: {list(_FIND_KEYS)})"
        raise ValueError(msg)
    return [
        entity.slug
        for entity in store.list_entities(save_dir)
        if _entity_matches_filters(entity.model_dump(), criteria)
    ]


def get_location_occupants(save_dir: Path, location: str) -> list[str]:
    """Return slugs of entities whose current_location is the given location."""
    return [
        entity.slug
        for entity in store.list_entities(save_dir)
        if entity.model_dump().get("current_location") == location
    ]


def _event_matches(event: Event, criteria: dict[str, Any]) -> bool:
    if "type" in criteria and event.type != criteria["type"]:
        return False
    if "actor" in criteria and event.actor != criteria["actor"]:
        return False
    return not ("target" in criteria and criteria["target"] not in event.targets)


def query_events(
    save_dir: Path,
    filters: dict[str, Any] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return the last ``limit`` events matching the filters (filter, then truncate)."""
    if limit < 0:
        msg = "limit must be nonnegative."
        raise ValueError(msg)
    if limit == 0:
        return []
    criteria = filters or {}
    if set(criteria) - {"type", "actor", "target"}:
        msg = "Unknown event filter."
        raise ValueError(msg)
    matches = [e for e in store.read_events(save_dir) if _event_matches(e, criteria)]
    return [e.model_dump() for e in matches[-limit:]]


def _location_detail(save_dir: Path, location: object) -> dict[str, Any] | None:
    """Return the current location's full entity (name, description, connections...)."""
    if not isinstance(location, str):
        return None
    try:
        entity = store.load_entity(save_dir, location)
    except FileNotFoundError:
        return {"slug": location, "error": "not_found"}
    return entity.model_dump()


def _present_at(save_dir: Path, location: object, exclude: str) -> list[dict[str, Any]]:
    """Return the entities present at ``location`` (NPCs, creatures, items) with key fields."""
    if not isinstance(location, str):
        return []
    present: list[dict[str, Any]] = []
    for entity in store.list_entities(save_dir):
        data = entity.model_dump()
        if data.get("current_location") == location and entity.slug != exclude:
            present.append(
                {
                    "slug": entity.slug,
                    "name": data.get("name"),
                    "type": entity.type,
                    "status": data.get("status"),
                    "mindset": data.get("mindset"),
                },
            )
    return present


def get_world_context(save_dir: Path) -> dict[str, Any]:
    """Assemble the current situation: player, location detail, who/what is here, recent events."""
    scenario = store.load_scenario(save_dir)
    context: dict[str, Any] = {
        "scenario": {
            "title": scenario.title,
            "synopsis": scenario.synopsis,
            "goal": scenario.goal,
        },
    }
    try:
        player = store.load_entity(save_dir, scenario.player)
    except FileNotFoundError:
        context["player"] = {"error": "not_found", "slug": scenario.player}
        return context
    player_data = player.model_dump()
    location = player_data.get("current_location")
    context["player"] = player_data
    context["location"] = _location_detail(save_dir, location)
    context["occupants"] = _present_at(save_dir, location, scenario.player)
    context["recent_events"] = [e.model_dump() for e in store.read_events(save_dir, limit=5)]
    return context


# ---- write tools (validate -> append_event -> projection) -------------------


def propose_state_change(
    save_dir: Path,
    slug: str,
    changes: dict[str, Any],
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Set one or more dynamic fields on an entity (validate-all-then-commit)."""
    events = []
    for index, (field, value) in enumerate(changes.items()):
        tentative = store.new_event(
            save_dir,
            "state_changed",
            targets=[slug],
            payload={"field": field, "value": value},
            outcome=outcome if index == 0 else None,
        )
        ok, rule, reason = validate(save_dir, tentative)
        if not ok:
            return {"ok": False, "rule": rule, "reason": reason}
        events.append(
            tentative.model_copy(
                update={"id": f"evt_{store.event_count(save_dir) + index + 1:04d}"}
            )
        )
    if not events:
        return {"ok": False, "reason": "No changes supplied."}
    store.append_events(save_dir, events)
    return {"ok": True, "events": [event.id for event in events]}


def propose_create_entity(
    save_dir: Path,
    type: str,
    slug: str,
    fields: dict[str, Any],
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Introduce a new entity."""
    if {"slug", "type"} & fields.keys():
        return {"ok": False, "reason": "fields cannot override slug or type."}
    payload = {**fields, "slug": slug, "type": type}
    event = store.new_event(
        save_dir, "entity_created", targets=[slug], payload=payload, outcome=outcome
    )
    return _validate_and_apply(save_dir, event)


def propose_relationship_change(
    save_dir: Path,
    holder: str,
    target: str,
    changes: dict[str, Any],
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update the holder's directional relationship toward target."""
    if set(changes) - {"value", "delta", "note"} or not changes:
        return {"ok": False, "reason": "Expected relationship value, delta or note."}
    event = store.new_event(
        save_dir,
        "relationship_changed",
        actor=holder,
        targets=[holder],
        payload={"target": target, **changes},
        outcome=outcome,
    )
    return _validate_and_apply(save_dir, event)


def propose_world_event(
    save_dir: Path,
    type: str,
    payload: dict[str, Any],
    narrative: str,
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a background/world development (may have no projection handler)."""
    if type in PROJECTED_TYPES:
        return {"ok": False, "reason": "Use the corresponding entity tool for state changes."}
    event = store.new_event(
        save_dir,
        type,
        payload={**payload, "narrative": narrative},
        outcome=outcome,
    )
    return _validate_and_apply(save_dir, event)


def propose_move(
    save_dir: Path,
    slug: str,
    location: str,
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Move an entity to a new location."""
    event = store.new_event(
        save_dir,
        "moved",
        actor=slug,
        targets=[slug],
        payload={"to": location},
        outcome=outcome,
    )
    return _validate_and_apply(save_dir, event)


def _change_tag(
    save_dir: Path,
    event_type: str,
    slug: str,
    op: str,
    value: str,
) -> dict[str, Any]:
    """Emit a trait/condition change event (dedupe + cap applied by the projection)."""
    event = store.new_event(
        save_dir, event_type, targets=[slug], payload={"op": op, "value": value}
    )
    return _validate_and_apply(save_dir, event)


def add_trait(save_dir: Path, slug: str, trait: str) -> dict[str, Any]:
    """Add a durable personality trait to an entity."""
    return _change_tag(save_dir, "trait_changed", slug, "add", trait)


def remove_trait(save_dir: Path, slug: str, trait: str) -> dict[str, Any]:
    """Remove a durable personality trait from an entity."""
    return _change_tag(save_dir, "trait_changed", slug, "remove", trait)


def add_condition(save_dir: Path, slug: str, condition: str) -> dict[str, Any]:
    """Add a transient condition to an entity."""
    return _change_tag(save_dir, "condition_changed", slug, "add", condition)


def remove_condition(save_dir: Path, slug: str, condition: str) -> dict[str, Any]:
    """Remove a transient condition from an entity."""
    return _change_tag(save_dir, "condition_changed", slug, "remove", condition)


def _validate_and_apply(save_dir: Path, event: Event) -> dict[str, Any]:
    ok, rule, reason = validate(save_dir, event)
    if not ok:
        return {"ok": False, "rule": rule, "reason": reason}
    store.append_event(save_dir, event)
    return {"ok": True, "events": [event.id]}


# ---- outcome ----------------------------------------------------------------


_WEIGHT_KEYS = ("p", "weight", "probability")


def _outcome_weight(outcome: dict[str, Any]) -> float:
    """Read an outcome's weight, accepting ``p``/``weight``/``probability`` (the model varies)."""
    for key in _WEIGHT_KEYS:
        value = outcome.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


def resolve_outcome(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll one weighted outcome and return its label."""
    if not outcomes:
        return {"error": "no outcomes given"}
    if any(not isinstance(o.get("label"), str) or not o["label"].strip() for o in outcomes):
        return {"error": "Every outcome needs a nonempty label."}
    labels = [str(o["label"]) for o in outcomes]
    weights = [_outcome_weight(o) for o in outcomes]
    if any(not math.isfinite(w) or w < 0 for w in weights) or not math.isfinite(sum(weights)):
        return {"error": "Weights must be finite and nonnegative."}
    if sum(weights) <= 0:
        weights = [1.0] * len(labels)
    return {"result": random.choices(labels, weights=weights, k=1)[0]}  # noqa: S311
