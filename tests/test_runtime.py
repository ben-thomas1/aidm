"""Real MCP subprocess and agent loop, with a deterministic in-process model."""

import asyncio
import json
import shutil

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from aidm import llm, saveload, store
from aidm.config import init_config
from aidm.orchestrator import Engine
from aidm.orchestrator.streaming import ReasoningStripper
from tests.test_regressions import GAMES


@pytest.fixture
def game(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shutil.copytree(GAMES, tmp_path / "games")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"llm": {"base_url": "http://127.0.0.1:1/v1", "model": "fixture"}})
    )
    init_config(config_path=config)
    save = saveload.create_save("hollowreach", "demo")
    scenario = json.loads((save / "scenario.json").read_text())
    scenario["background_event_chance"] = 0
    (save / "scenario.json").write_text(json.dumps(scenario))
    return save


@pytest.mark.parametrize("failure", [None, "resolver", "narrator"])
def test_full_turn_through_mcp(game, monkeypatch, failure):
    requests = 0

    async def respond(_messages, info):
        nonlocal requests
        if info.output_tools:
            return ModelResponse(
                [
                    ToolCallPart(
                        info.output_tools[0].name,
                        {"summary": "You paid five coins.", "beats": [], "npcs": []},
                    )
                ]
            )
        names = {tool.name for tool in info.function_tools}
        assert "set_active_save" not in names
        assert all(tool.sequential for tool in info.function_tools)
        assert all(
            "outcome" not in tool.parameters_json_schema.get("properties", {})
            for tool in info.function_tools
        )
        requests += 1
        if requests == 1:
            return ModelResponse(
                [ToolCallPart("resolve_outcome", {"labels": ["paid"], "weights": [1]})]
            )
        if requests == 2:
            return ModelResponse(
                [ToolCallPart("propose_state_change", {"slug": "player", "changes": {"money": 20}})]
            )
        if failure == "resolver":
            message = "injected model failure after write"
            raise RuntimeError(message)
        return ModelResponse([TextPart("Payment recorded.")])

    async def stream(_messages, _info):
        if failure == "narrator":
            message = "injected narrator failure"
            raise RuntimeError(message)
        for chunk in ["<thi", "nk>hidden reasoning</th", "ink>You pay ", "five coins."]:
            yield chunk

    model = FunctionModel(respond, stream_function=stream)
    monkeypatch.setattr(llm, "build_model", lambda _stack: model)

    async def play():
        engine = Engine()
        try:
            assert await engine.set_active_save(game)
            fragments = []
            result = await engine.run_turn(game, "Pay five coins", fragments.append)
            assert store.load_entity(game, "player").model_dump()["money"] == 20
            event = next(e for e in store.read_events(game) if e.type == "state_changed")
            assert event.outcome is not None
            assert event.outcome["result"] == "paid"
            assert store.load_meta(game).current_turn == 1
            assert len(store.read_transcript(game)) == 1
            if failure == "resolver":
                assert result.error and "earlier changes" in result.error
            elif failure == "narrator":
                assert result.error and "Narration failed" in result.error
                assert result.narration is not None
                assert "Some changes may already be saved" in result.narration
            else:
                assert result.error is None
                assert "".join(fragments) == result.narration == "You pay five coins."
                assert store.load_story(game).summary == "You paid five coins."
            assert not await engine.set_active_save(game.parent / "missing")
            assert not (await engine.call_tool("get_entity", {"slug": "player"}))["ok"]
        finally:
            await engine.aclose()
        assert not engine.is_ready

    asyncio.run(play())


@pytest.mark.parametrize("size", range(1, 15))
def test_reasoning_filter_at_every_chunk_boundary(size):
    stripper = ReasoningStripper()
    text = "Before<Think>private</tHiNk>after<think>unfinished"
    visible = "".join(stripper.feed(text[i : i + size]) for i in range(0, len(text), size))
    assert visible + stripper.flush() == "Beforeafter"
