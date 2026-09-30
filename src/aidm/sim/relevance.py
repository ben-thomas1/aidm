"""Derive which entities are "near"/relevant to the player each tick (§8.4).

An entity is relevant if it shares the player's location, is referenced by an active
quest, or the player holds a relationship with it at or above ``RELEVANCE_THRESHOLD``.
Relevant entities are progressed each tick (never frozen), so they never need catch-up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aidm import store
from aidm.const import RELEVANCE_THRESHOLD

if TYPE_CHECKING:
    from pathlib import Path


def near_entities(save_dir: Path) -> set[str]:
    """Return slugs near/relevant to the player (excluding the player)."""
    scenario = store.load_scenario(save_dir)
    try:
        player = store.load_entity(save_dir, scenario.player)
    except FileNotFoundError:
        return set()
    player_data = player.model_dump()
    location = player_data.get("current_location")
    near: set[str] = set()
    for entity in store.list_entities(save_dir):
        if entity.slug == scenario.player:
            continue
        if isinstance(location, str) and entity.model_dump().get("current_location") == location:
            near.add(entity.slug)
    near |= _active_quest_slugs(save_dir)
    near |= _close_relationship_slugs(player_data)
    near.discard(scenario.player)
    return near


def _active_quest_slugs(save_dir: Path) -> set[str]:
    involved: set[str] = set()
    for entity in store.list_entities(save_dir):
        if entity.type != "quest":
            continue
        data = entity.model_dump()
        if data.get("status") != "active":
            continue
        involves = data.get("involves")
        if isinstance(involves, list):
            involved.update(slug for slug in involves if isinstance(slug, str))
    return involved


def _close_relationship_slugs(player_data: dict[str, Any]) -> set[str]:
    relationships = player_data.get("relationships")
    if not isinstance(relationships, dict):
        return set()
    close: set[str] = set()
    for slug, rel in relationships.items():
        value = rel.get("value") if isinstance(rel, dict) else None
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= RELEVANCE_THRESHOLD
        ):
            close.add(slug)
    return close
