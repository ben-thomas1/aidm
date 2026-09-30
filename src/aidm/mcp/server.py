"""The single stdio MCP server: wraps the store with validation, over an active save.

`from __future__ import annotations` is intentionally NOT used here: FastMCP
introspects tool signatures at runtime to build JSON schemas, so annotations must
be real objects. All diagnostics go to stderr — stdout is the JSON-RPC channel.
"""

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from aidm import saveload
from aidm.mcp import tools

mcp = FastMCP("aidm", log_level="WARNING")

_NO_SAVE: dict[str, Any] = {"ok": False, "reason": "no active save"}


class _State:
    active_save: Path | None = None


def _require_save() -> Path | None:
    return _State.active_save


# ---- admin tools (control ops; excluded from the agent's tool list later) ----


@mcp.tool()
def set_active_save(path: str) -> dict[str, Any]:
    """[admin] Set the save directory all world tools operate on."""
    _State.active_save = None
    try:
        save_dir = saveload.save_path(Path(path).name)
        if Path(path).absolute() != save_dir.absolute() or not save_dir.is_dir():
            return {"ok": False, "reason": "Expected a save directly under saves/."}
        saveload.validate_save(save_dir)
    except OSError, ValueError:
        return {"ok": False, "reason": "Invalid or unreadable save."}
    _State.active_save = save_dir
    return {"ok": True}


@mcp.tool()
def clear_active_save() -> dict[str, Any]:
    """[admin] Clear the active save."""
    _State.active_save = None
    return {"ok": True}


@mcp.tool()
def get_active_save() -> dict[str, Any]:
    """[admin] Return the active save path."""
    save = _State.active_save
    return {"active_save": str(save) if save is not None else None}


# ---- read tools -------------------------------------------------------------


@mcp.tool()
def get_entity(slug: str) -> dict[str, Any]:
    """Return all of an entity's fields."""
    save = _require_save()
    return _NO_SAVE if save is None else tools.get_entity(save, slug)


@mcp.tool()
def get_relationship(holder: str, target: str) -> dict[str, Any]:
    """Return the holder's directional relationship toward target."""
    save = _require_save()
    return _NO_SAVE if save is None else tools.get_relationship(save, holder, target)


@mcp.tool()
def get_relationships(holder: str) -> dict[str, Any]:
    """Return all of the holder's directional relationships."""
    save = _require_save()
    return _NO_SAVE if save is None else tools.get_relationships(save, holder)


@mcp.tool()
def find_entities(
    type: str | None = None,
    location: str | None = None,
    status: str | None = None,
    name: str | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    """Find entity slugs by type, location, status, name (substring), or owner (for items)."""
    save = _require_save()
    if save is None:
        return _NO_SAVE
    criteria = {
        key: value
        for key, value in (
            ("type", type),
            ("current_location", location),
            ("status", status),
            ("name", name),
            ("owner", owner),
        )
        if value is not None
    }
    return {"slugs": tools.find_entities(save, criteria)}


@mcp.tool()
def get_location_occupants(location: str) -> dict[str, Any]:
    """Return slugs of entities currently at the given location."""
    save = _require_save()
    if save is None:
        return _NO_SAVE
    return {"slugs": tools.get_location_occupants(save, location)}


@mcp.tool()
def query_events(
    type: str | None = None,
    actor: str | None = None,
    target: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Return recent events (last ``limit``), optionally filtered by type, actor, or target."""
    save = _require_save()
    if save is None:
        return _NO_SAVE
    criteria = {
        key: value
        for key, value in (("type", type), ("actor", actor), ("target", target))
        if value is not None
    }
    return {"events": tools.query_events(save, criteria, limit)}


@mcp.tool()
def get_world_context() -> dict[str, Any]:
    """Assemble the current situation: player, location, occupants, recent events."""
    save = _require_save()
    return _NO_SAVE if save is None else tools.get_world_context(save)


# ---- write tools ------------------------------------------------------------


@mcp.tool()
def propose_state_change(
    slug: str,
    changes: dict[str, Any],
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Set one or more dynamic fields on an entity."""
    save = _require_save()
    return _NO_SAVE if save is None else tools.propose_state_change(save, slug, changes, outcome)


@mcp.tool()
def propose_create_entity(
    slug: str,
    type: str,
    name: str,
    location: str | None = None,
    description: str | None = None,
    owner: str | None = None,
    extra: dict[str, Any] | None = None,
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Introduce a new entity.

    ``slug`` is its unique id, ``type`` one of character/location/item/creature/quest,
    ``name`` its display name. ``location`` sets where it is; ``description`` is authored
    flavour; ``owner`` (for items) is the slug of the holder; ``extra`` holds any other
    fields (e.g. health, money, status).
    """
    save = _require_save()
    if save is None:
        return _NO_SAVE
    fields: dict[str, Any] = {"name": name, **(extra or {})}
    if description is not None:
        fields["description"] = description
    if location is not None:
        fields["current_location"] = location
    if owner is not None:
        fields["owner"] = owner
    return tools.propose_create_entity(save, type, slug, fields, outcome)


@mcp.tool()
def propose_relationship_change(
    holder: str,
    target: str,
    value: float | None = None,
    note: str | None = None,
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Set ``holder``'s feeling toward ``target``: ``value`` (a number) and/or a short ``note``."""
    save = _require_save()
    if save is None:
        return _NO_SAVE
    changes = {key: val for key, val in (("value", value), ("note", note)) if val is not None}
    return tools.propose_relationship_change(save, holder, target, changes, outcome)


@mcp.tool()
def propose_world_event(
    narrative: str,
    type: str = "world_event",
    extra: dict[str, Any] | None = None,
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a background/world development described by ``narrative``."""
    save = _require_save()
    if save is None:
        return _NO_SAVE
    return tools.propose_world_event(save, type, extra or {}, narrative, outcome)


@mcp.tool()
def propose_move(slug: str, location: str, outcome: dict[str, Any] | None = None) -> dict[str, Any]:
    """Move an entity to a new location."""
    save = _require_save()
    return _NO_SAVE if save is None else tools.propose_move(save, slug, location, outcome)


@mcp.tool()
def add_trait(slug: str, trait: str) -> dict[str, Any]:
    """Give an entity a durable personality trait (e.g. shy, thrifty, intelligent)."""
    save = _require_save()
    return _NO_SAVE if save is None else tools.add_trait(save, slug, trait)


@mcp.tool()
def remove_trait(slug: str, trait: str) -> dict[str, Any]:
    """Remove a durable personality trait from an entity."""
    save = _require_save()
    return _NO_SAVE if save is None else tools.remove_trait(save, slug, trait)


@mcp.tool()
def add_condition(slug: str, condition: str) -> dict[str, Any]:
    """Give an entity a transient condition (e.g. drunk, injured, frightened)."""
    save = _require_save()
    return _NO_SAVE if save is None else tools.add_condition(save, slug, condition)


@mcp.tool()
def remove_condition(slug: str, condition: str) -> dict[str, Any]:
    """Remove a transient condition from an entity (clear it once it no longer applies)."""
    save = _require_save()
    return _NO_SAVE if save is None else tools.remove_condition(save, slug, condition)


# ---- outcome ----------------------------------------------------------------


@mcp.tool()
def resolve_outcome(labels: list[str], weights: list[float] | None = None) -> dict[str, Any]:
    """Roll one weighted outcome and return its label.

    ``labels`` are the possible results; ``weights`` (same length, optional) are their
    relative likelihoods — omit for equal odds.
    """
    if weights is not None and len(weights) != len(labels):
        return {"error": "labels and weights must have the same length."}
    if weights is not None:
        outcomes = [
            {"label": label, "weight": weight}
            for label, weight in zip(labels, weights, strict=True)
        ]
    else:
        outcomes = [{"label": label} for label in labels]
    return tools.resolve_outcome(outcomes)


def main() -> None:
    """Run the stdio MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
