"""Non-LLM renderers for player-facing inspection commands and the new-game intro.

Each builder reads the save directly through the pure ``tools``/``store`` helpers (no MCP
server, no model) and returns plain text for the CLI to print.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aidm import store
from aidm.mcp import tools

if TYPE_CHECKING:
    from pathlib import Path


def _description(data: dict[str, Any]) -> str | None:
    description = data.get("description")
    return description if isinstance(description, str) and description else None


def scene(save_dir: Path) -> str:
    """Describe where the player is: location, exits, and who/what is present."""
    context = tools.get_world_context(save_dir)
    location = context.get("location")
    if not isinstance(location, dict) or location.get("error"):
        return "You are nowhere in particular."
    lines = [str(location.get("name") or "Somewhere")]
    description = _description(location)
    if description:
        lines.append(description)
    connections = location.get("connections")
    if isinstance(connections, list) and connections:
        lines.append(f"Exits: {', '.join(str(c) for c in connections)}")
    occupants = context.get("occupants")
    if isinstance(occupants, list) and occupants:
        here = []
        for occ in occupants:
            name = occ.get("name") or occ.get("slug")
            status = occ.get("status")
            here.append(f"{name} ({status})" if status else str(name))
        lines.append("Here: " + ", ".join(here))
    return "\n".join(lines)


def status(save_dir: Path) -> str:
    """Summarise the player's vital state."""
    player = store.load_scenario(save_dir).player
    data = tools.get_entity(save_dir, player)
    if data.get("error"):
        return "No player character found."
    parts = [str(data.get("name") or player)]
    for field in ("health", "money", "stamina", "status"):
        value = data.get(field)
        if value is not None:
            parts.append(f"{field}: {value}")
    line = " — ".join((parts[0], ", ".join(parts[1:]))) if len(parts) > 1 else parts[0]
    background = data.get("background")
    return f"{line}\n{background}" if isinstance(background, str) and background else line


def quests(save_dir: Path) -> str:
    """List quest entities with their status and description."""
    slugs = tools.find_entities(save_dir, {"type": "quest"})
    if not slugs:
        return "No quests yet."
    lines = ["Quests:"]
    for slug in slugs:
        data = tools.get_entity(save_dir, slug)
        name = data.get("name") or slug
        state = data.get("status") or "active"
        description = _description(data)
        entry = f"  {name} [{state}]"
        if description:
            entry += f" — {description}"
        lines.append(entry)
    return "\n".join(lines)


def inventory(save_dir: Path) -> str:
    """List the items the player owns."""
    player = store.load_scenario(save_dir).player
    slugs = tools.find_entities(save_dir, {"type": "item", "owner": player})
    if not slugs:
        return "Your pack is empty."
    lines = ["Inventory:"]
    for slug in slugs:
        data = tools.get_entity(save_dir, slug)
        name = data.get("name") or slug
        description = _description(data)
        lines.append(f"  {name} — {description}" if description else f"  {name}")
    return "\n".join(lines)
