"""Regression checks using only copied public game fixtures."""

import asyncio
import json
from pathlib import Path

import pytest

from aidm import saveload, sim, store
from aidm.frontends.cli.app import handle_line
from aidm.frontends.cli.screens import build_welcome_registry
from aidm.mcp import tools
from aidm.orchestrator.assembler import build_context
from aidm.session import Context, Screen, Session

GAMES = Path(__file__).resolve().parents[1] / "games"


def test_delete_cannot_escape_save_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "saves").mkdir()
    valuable = tmp_path / "valuable"
    valuable.mkdir()
    (valuable / "keep.txt").write_text("keep")
    with pytest.raises(ValueError):
        saveload.delete_save("../valuable")
    assert (valuable / "keep.txt").read_text() == "keep"


def test_entity_slug_cannot_read_outside_world(world, tmp_path):
    (tmp_path / "outside.json").write_text(
        '{"slug":"outside","type":"item","name":"Private fixture"}'
    )
    with pytest.raises(ValueError):
        store.load_entity(world, "../../outside")


def test_create_cannot_override_identity(world):
    result = tools.propose_create_entity(
        world, "item", "new_item", {"slug": "other", "name": "New item"}
    )
    assert not result["ok"]
    assert store.event_count(world) == 0


def test_rejected_batch_does_not_partially_write(world):
    before = store.load_entity(world, "player")
    result = tools.propose_state_change(world, "player", {"money": 42, "traits": 7})
    assert not result["ok"]
    assert store.load_entity(world, "player") == before
    assert store.event_count(world) == 0


@pytest.mark.parametrize("location", ["missing", "garrick"])
def test_move_requires_existing_location(world, location):
    result = tools.propose_move(world, "player", location)
    assert not result["ok"]
    assert store.load_entity(world, "player").model_dump()["current_location"] == "square"


def test_world_event_cannot_invoke_entity_projection(world):
    result = tools.propose_world_event(
        world, "entity_created", {"slug": "unvalidated", "type": "item", "name": "Oops"}, "x"
    )
    assert not result["ok"]
    assert store.event_count(world) == 0


def test_no_resurrection_rule_applies_to_health(world):
    assert tools.propose_state_change(world, "garrick", {"health": 0})["ok"]
    assert not tools.propose_state_change(world, "garrick", {"health": 90})["ok"]


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "rich"])
def test_invalid_money_is_rejected(world, value):
    assert not tools.propose_state_change(world, "player", {"money": value})["ok"]


def test_context_uses_npcs_feelings_toward_player(world):
    assert tools.propose_relationship_change(world, "mira", "player", {"value": -25})["ok"]
    assert "toward you: -25" in build_context(world)


def test_background_failure_still_advances_turn_once(world):
    scenario = world / "scenario.json"
    data = json.loads(scenario.read_text())
    data["background_event_chance"] = 1
    scenario.write_text(json.dumps(data))

    async def fail(_prompt):
        message = "fixture provider failure"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="fixture provider failure"):
        asyncio.run(sim.tick(world, fail))
    assert store.load_meta(world).current_turn == 1
    assert store.load_entity(world, "mira").model_dump()["last_simulated_turn"] == 1


def test_empty_slash_command_is_not_a_crash():
    ctx = Context(Session(Screen.WELCOME))
    result = asyncio.run(handle_line("/", build_welcome_registry(None), ctx))
    assert result.is_error


def test_zero_event_limit_is_empty(world):
    assert tools.propose_state_change(world, "player", {"money": 12})["ok"]
    assert store.read_events(world, limit=0) == []
    assert tools.query_events(world, limit=0) == []


def test_arrival_catches_up_the_full_simulation_gap(world):
    store.save_meta(world, store.load_meta(world).model_copy(update={"current_turn": 5}))
    assert tools.propose_move(world, "player", "forge")["ok"]
    data = json.loads((world / "scenario.json").read_text())
    data["background_event_chance"] = 0
    (world / "scenario.json").write_text(json.dumps(data))

    async def unused(_prompt):
        return ""

    asyncio.run(sim.tick(world, unused))
    garrick = store.load_entity(world, "garrick").model_dump()
    assert garrick["stamina"] == 22
    assert garrick["last_simulated_turn"] == 6


def test_trait_cap_rejection_does_not_record_false_success(world):
    for index in range(12):
        assert tools.add_trait(world, "player", f"trait-{index}")["ok"]
    count = store.event_count(world)
    assert not tools.add_trait(world, "player", "overflow")["ok"]
    assert store.event_count(world) == count


@pytest.mark.parametrize("weights", [[-1, 2], [float("nan"), 1], [float("inf"), 1]])
def test_invalid_outcome_weights_are_rejected(weights):
    result = tools.resolve_outcome(
        [
            {"label": "first", "weight": weights[0]},
            {"label": "second", "weight": weights[1]},
        ]
    )
    assert "error" in result
