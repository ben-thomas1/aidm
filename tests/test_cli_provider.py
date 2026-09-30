"""Installed CLI and real OpenAI-compatible HTTP adapter smoke checks."""

import asyncio
import json
import shutil
import subprocess
import sys
import threading
from contextlib import AsyncExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from aidm.config import LLMConfig, init_config
from aidm.llm import build_model
from tests.test_regressions import GAMES


def test_installed_cli_lifecycle(tmp_path):
    shutil.copytree(GAMES, tmp_path / "games")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"llm": {"base_url": "http://127.0.0.1:1/v1", "model": "fixture"}})
    )
    commands = "/games\n/new example demo\n/status\n/look\n/inventory\n/quests\n/\n/menu\n/load demo\n/menu\n/delete demo --confirm\n/quit\n"
    result = subprocess.run(  # noqa: S603 (fixed CLI, generated input)
        [str(Path(sys.executable).with_name("aidm"))],
        cwd=tmp_path,
        input=commands,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for text in (
        "Started 'demo'",
        "health: 100",
        "Empty command",
        "Loaded 'demo'",
        "Deleted save 'demo'",
        "Goodbye",
    ):
        assert text in result.stdout
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "saves" / "demo").exists()
    assert (tmp_path / "games" / "example" / "scenario.json").read_bytes() == (
        GAMES / "example" / "scenario.json"
    ).read_bytes()


@pytest.mark.parametrize("raw", ["[]", "null", '{"llm":{"model":"fixture","base_url":"invalid"}}'])
def test_cli_invalid_config_exits_nonzero_without_echoing_input(tmp_path, raw):
    config = tmp_path / "invalid.json"
    config.write_text(raw)
    result = subprocess.run(  # noqa: S603
        [str(Path(sys.executable).with_name("aidm")), "--config", str(config)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert "Failed to load config" in result.stdout
    assert "Traceback" not in result.stderr


def test_openai_http_requests_streaming_and_client_cleanup(tmp_path):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 (stdlib signature)
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, data))
            self.send_response(200)
            self.send_header(
                "Content-Type", "text/event-stream" if data.get("stream") else "application/json"
            )
            self.end_headers()
            if data.get("stream"):
                for text in ("A quiet ", "clearing."):
                    chunk = {
                        "id": "fixture",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": "fixture",
                        "choices": [
                            {"index": 0, "delta": {"content": text}, "finish_reason": None}
                        ],
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                response = {
                    "id": "fixture",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "fixture",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "A quiet clearing."},
                            "finish_reason": "stop",
                        }
                    ],
                }
                self.wfile.write(json.dumps(response).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "llm": {
                    "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                    "model": "fixture",
                    "max_tokens": 64,
                    "timeout_s": 5,
                }
            }
        )
    )
    init_config(config_path=config)

    async def run():
        async with AsyncExitStack() as stack:
            model = build_model(stack)
            assert isinstance(model, OpenAIChatModel)
            agent = Agent(model)
            assert (await agent.run("Describe the public fixture.")).output == "A quiet clearing."
            async with agent.run_stream("Describe it again.") as stream:
                assert await stream.get_output() == "A quiet clearing."
            client = model.client
        assert client.is_closed()

    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert len(requests) == 2
    assert all(path == "/v1/chat/completions" for path, _ in requests)
    assert all(
        data.get("max_completion_tokens", data.get("max_tokens")) == 64 for _, data in requests
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("timeout_s", 0),
        ("timeout_s", float("inf")),
        ("max_tokens", -1),
        ("base_url", "file:///tmp/model"),
    ],
)
def test_invalid_model_settings(field, value):
    data = {"model": "fixture", "base_url": "http://localhost:8080/v1", field: value}
    with pytest.raises(ValueError):
        LLMConfig.model_validate(data)
