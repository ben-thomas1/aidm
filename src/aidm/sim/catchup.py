"""Catch up entities the player re-encounters after they have drifted off-screen (§8.3).

A "distant" entity is frozen while the player is away. When the player meets it again, we
advance it once with **deterministic** regen over the gap, recorded as one ``simulated``
event with ``covers_turns``. Qualitative inner-state drift is now the pass-2 maintainer's
job (v2), so catch-up no longer makes an LLM call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aidm import store
from aidm.const import MAX_CATCHUP_ENTITIES
from aidm.sim.regen import regen_changes

if TYPE_CHECKING:
    from pathlib import Path


def _watermark(data: dict[str, Any], current_turn: int) -> int:
    last = data.get("last_simulated_turn")
    return last if isinstance(last, int) and not isinstance(last, bool) else current_turn


def catch_up_present(save_dir: Path) -> list[str]:
    """Catch up entities present at the player's location whose simulation lags. Returns their slugs."""
    scenario = store.load_scenario(save_dir)
    try:
        player = store.load_entity(save_dir, scenario.player)
    except FileNotFoundError:
        return []
    location = player.model_dump().get("current_location")
    if not isinstance(location, str):
        return []
    current_turn = store.load_meta(save_dir).current_turn
    lagging = [
        entity.slug
        for entity in store.list_entities(save_dir)
        if entity.slug != scenario.player
        and entity.model_dump().get("current_location") == location
        and _watermark(entity.model_dump(), current_turn) < current_turn
    ]
    caught: list[str] = []
    for slug in lagging[:MAX_CATCHUP_ENTITIES]:
        catch_up(save_dir, slug)
        caught.append(slug)
    return caught


def catch_up(save_dir: Path, slug: str) -> bool:
    """Advance one entity over its gap with deterministic regen. Returns whether anything was recorded."""
    current_turn = store.load_meta(save_dir).current_turn
    data = store.load_entity(save_dir, slug).model_dump()
    last = _watermark(data, current_turn)
    gap = current_turn - last
    if gap <= 0:
        return False
    changes: dict[str, Any] = regen_changes(data, gap)
    changes["last_simulated_turn"] = current_turn
    changes["last_seen_turn"] = current_turn
    event = store.new_event(
        save_dir,
        "simulated",
        targets=[slug],
        payload={"changes": changes},
        covers_turns=(last, current_turn),
    )
    store.append_event(save_dir, event)
    return True
