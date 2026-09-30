"""Per-action world tick (§8.1): near regen, a chance-gated background event, turn bump.

Runs after each player action. Near/relevant entities advance deterministically and are
watermarked to the new turn so they never need catch-up; distant entities are left frozen
for catch-up on re-encounter. A background development may fire silently.
"""

from __future__ import annotations

import json
import random
from typing import TYPE_CHECKING, Any

from aidm import store
from aidm.domain import Entity
from aidm.sim.prompts import BACKGROUND_PROMPT
from aidm.sim.regen import regen_changes
from aidm.sim.relevance import near_entities

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    Generate = Callable[[str], Awaitable[str]]


async def tick(save_dir: Path, generate: Generate) -> None:
    """Advance the world one turn: regen near entities, maybe fire a background event, bump the turn."""
    near = near_entities(save_dir)
    _regen_near(save_dir, near)
    _advance_turn(save_dir, near)
    await _maybe_background(save_dir, generate)


def _regen_near(save_dir: Path, near: set[str]) -> None:
    current_turn = store.load_meta(save_dir).current_turn
    for slug in sorted(near):
        data = store.load_entity(save_dir, slug).model_dump()
        last = data.get("last_simulated_turn", current_turn)
        if not isinstance(last, int) or isinstance(last, bool):
            last = current_turn
        changes = regen_changes(data, max(0, current_turn + 1 - last))
        if changes:
            changes["last_simulated_turn"] = current_turn + 1
            event = store.new_event(
                save_dir, "simulated", targets=[slug], payload={"changes": changes}
            )
            store.append_event(save_dir, event)


async def _maybe_background(save_dir: Path, generate: Generate) -> None:
    scenario = store.load_scenario(save_dir)
    chance = float(getattr(scenario, "background_event_chance", 0.0) or 0.0)
    if random.random() < chance:  # noqa: S311 (gameplay randomness, not security)
        await fire_background(save_dir, generate)


async def fire_background(save_dir: Path, generate: Generate) -> str:
    """Generate and silently record one off-screen world development. Returns its description."""
    from aidm.mcp import tools  # noqa: PLC0415 (lazy: avoid importing the MCP package at sim load)

    world = tools.get_world_context(save_dir)
    prompt = (
        f"{BACKGROUND_PROMPT}\n\nCurrent world state:\n{json.dumps(world, indent=2, default=str)}"
    )
    description = (await generate(prompt)).strip()
    event = store.new_event(
        save_dir,
        "world_event",
        payload={"narrative": description, "source": "background"},
    )
    store.append_event(save_dir, event)
    return description


def _advance_turn(save_dir: Path, near: set[str]) -> None:
    meta = store.load_meta(save_dir)
    new_turn = meta.current_turn + 1
    store.save_meta(save_dir, meta.model_copy(update={"current_turn": new_turn}))
    scenario = store.load_scenario(save_dir)
    try:
        location = store.load_entity(save_dir, scenario.player).model_dump().get("current_location")
    except FileNotFoundError:
        location = None
    for entity in store.list_entities(save_dir):
        if entity.slug == scenario.player:
            continue
        data = entity.model_dump()
        updates: dict[str, Any] = {}
        if entity.slug in near:
            updates["last_simulated_turn"] = new_turn
        if isinstance(location, str) and data.get("current_location") == location:
            updates["last_seen_turn"] = new_turn
        if updates:
            store.save_entity(save_dir, Entity.model_validate({**data, **updates}))
