"""The orchestrator engine: owns the LLM agent, the MCP connection, and the turn loop.

One :class:`Engine` is created at CLI startup and stored on the session
:class:`~aidm.session.Context`. It replaces the old hand-rolled MCP client: a
single ``MCPServerStdio`` (entered lazily on first use via an ``AsyncExitStack``
on the main task) is both the agent's toolset and the channel for lifecycle and
``/dev`` tool calls (``direct_call_tool``). Heavy imports (``pydantic_ai``,
``openai``) are deferred to :meth:`ensure_started` so the welcome screen stays
snappy.
"""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from aidm import sim, store
from aidm.config import get_config
from aidm.const import LLM_LOG_FILENAME, LLM_MD_FILENAME, TRACE_FILENAME

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic_ai import Agent
    from pydantic_ai.mcp import MCPToolset
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.run import AgentRunResult

    from aidm.orchestrator.agent import TurnDeps
    from aidm.orchestrator.maintainer import Housekeeping
    from aidm.orchestrator.trace import TurnTrace

_log = logging.getLogger("aidm.orchestrator")


@dataclass
class TurnResult:
    """Outcome of one player turn, rendered by the CLI layer."""

    narration: str | None = None
    error: str | None = None
    ended: bool = False


class Engine:
    """Owns the DM agent + MCP server and runs one turn at a time."""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._server: MCPToolset[TurnDeps] | None = None
        self._agent: Agent[TurnDeps, str] | None = None
        self._narrator: Agent[None, str] | None = None
        self._sim_agent: Agent[None, str] | None = None
        self._maintainer: Agent[None, Housekeeping] | None = None
        self._active_save: Path | None = None
        self._started = False
        self._tool_names: list[str] = []

    async def ensure_started(self) -> None:
        """Build and connect the agent/server once (idempotent, main-task)."""
        if self._started:
            return
        from aidm.llm import build_model  # noqa: PLC0415 (lazy: off startup path)
        from aidm.orchestrator.agent import (  # noqa: PLC0415
            build_agent,
            build_narrator,
            build_server,
        )
        from aidm.orchestrator.maintainer import build_maintainer  # noqa: PLC0415

        model = build_model(self._stack)
        self._server = build_server()
        self._agent = build_agent(model, self._server)
        self._narrator = build_narrator(model)
        self._maintainer = build_maintainer(model)
        from pydantic_ai import Agent  # noqa: PLC0415

        self._sim_agent = Agent(model)
        try:
            await self._stack.enter_async_context(self._agent)
        except BaseException:
            await self._stack.aclose()
            raise
        self._started = True
        try:
            tools = await self._server.list_tools()
            self._tool_names = [tool.name for tool in tools]
        except Exception:  # noqa: BLE001 (best-effort cache; surfaced on first real use)
            _log.warning("Failed to list MCP tools", exc_info=True)
            self._tool_names = []

    @property
    def is_ready(self) -> bool:
        """Whether the agent/server connection is live."""
        return self._started

    def tool_names(self) -> list[str]:
        """Return the cached MCP tool names (empty until started)."""
        return list(self._tool_names)

    async def list_tools(self) -> list[str]:
        """Return the server's tool names (for ``/dev tool list``)."""
        await self.ensure_started()
        assert self._server is not None  # noqa: S101 (ensured above)
        return [tool.name for tool in await self._server.list_tools()]

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Call a tool directly, bypassing the agent (for ``/dev tool call``)."""
        await self.ensure_started()
        assert self._server is not None  # noqa: S101 (ensured above)
        return await self._server.direct_call_tool(name, args)

    async def set_active_save(self, save_dir: Path) -> bool:
        """Point the server at ``save_dir``. Returns success."""
        ok = await self._safe_call("set_active_save", {"path": str(save_dir)})
        self._active_save = save_dir if ok else None
        return ok

    async def clear_active_save(self) -> bool:
        """Clear the server's active save. Returns success."""
        self._active_save = None
        if not self._started:
            return True
        return await self._safe_call("clear_active_save", {})

    async def generate(self, prompt: str) -> str:
        """Run a no-tools LLM text generation (for simulation); logged to the active save's trace."""
        await self.ensure_started()
        assert self._sim_agent is not None  # noqa: S101
        from pydantic_ai import capture_run_messages  # noqa: PLC0415

        with capture_run_messages() as messages:
            result = await self._sim_agent.run(prompt)
        if self._active_save is not None:
            turn = store.load_meta(self._active_save).current_turn
            first_line = prompt.splitlines()[0] if prompt else ""
            self._log_llm(
                self._active_save, turn, f"[sim] {first_line[:80]}", messages, output=result.output
            )
        return result.output

    async def run_turn(
        self,
        save_dir: Path,
        text: str,
        on_narration: Callable[[str], None] | None = None,
    ) -> TurnResult:
        """Drive one player turn: resolve+mutate (pass 1), narrate (pass 2), persist, tick.

        ``on_narration`` (if given) receives player-facing narration fragments as they
        stream, so the CLI can print them live. The full narration is still returned on
        the :class:`TurnResult`. All player-facing prose — including fallbacks — is routed
        through this sink, so callers display narration *only* via ``on_narration``.
        """
        from pydantic_ai import (  # noqa: PLC0415
            UnexpectedModelBehavior,
            capture_run_messages,
        )

        from aidm.orchestrator import assembler  # noqa: PLC0415
        from aidm.orchestrator.agent import TurnDeps  # noqa: PLC0415
        from aidm.orchestrator.prompts import ENGINE_FALLBACK  # noqa: PLC0415

        await self.ensure_started()
        if self._active_save != save_dir and not await self.set_active_save(save_dir):
            return TurnResult(error="Cannot activate this save; no turn was started.")
        assert self._agent is not None  # noqa: S101 (ensured above)
        assert self._narrator is not None  # noqa: S101 (ensured above)
        turn = store.load_meta(save_dir).current_turn
        trace = self._make_trace(save_dir, turn)
        trace.write("turn-start", text[:80])
        if self._player_dead(save_dir):  # already dead — the engine must not act or revive
            trace.write("turn-end", "player already dead")
            return TurnResult(ended=True)
        await self._catch_up(save_dir, trace)  # re-encounter drift, before the model sees context

        # Pass 1 — resolver: resolve the action and mutate state via tools (prose discarded).
        deps = TurnDeps(trace=trace)
        with capture_run_messages() as resolve_msgs:
            try:
                async with self._agent.iter(
                    assembler.build_prompt(save_dir, text),
                    message_history=None,
                    deps=deps,
                ) as run:
                    async for node in run:
                        self._trace_node(trace, node)
                resolve_result = run.result
            except UnexpectedModelBehavior as exc:
                trace.write("turn-error", str(exc)[:120])
                self._log_llm(save_dir, turn, text, resolve_msgs, error=str(exc))
                if on_narration is not None:
                    on_narration(ENGINE_FALLBACK)
                store.append_transcript(save_dir, turn, text, ENGINE_FALLBACK)
                meta = store.load_meta(save_dir)
                store.save_meta(save_dir, meta.model_copy(update={"current_turn": turn + 1}))
                return TurnResult(
                    narration=ENGINE_FALLBACK,
                    error="Resolver failed; earlier changes may be saved.",
                    ended=self._player_dead(save_dir),
                )
            except Exception as exc:  # noqa: BLE001 (transport/timeout: keep the REPL alive)
                trace.write("turn-error", str(exc))
                _log.warning("Resolver failed", exc_info=True)
                self._log_llm(save_dir, turn, text, resolve_msgs, error=str(exc))
                store.append_transcript(save_dir, turn, text, ENGINE_FALLBACK)
                meta = store.load_meta(save_dir)
                store.save_meta(save_dir, meta.model_copy(update={"current_turn": turn + 1}))
                return TurnResult(
                    error="Resolver failed; earlier changes may be saved. Use /look and /status to inspect them.",
                    ended=self._player_dead(save_dir),
                )
        if resolve_result is None:  # defensive: a clean iter always sets run.result
            return TurnResult(error="no result from resolver run")
        self._log_llm(save_dir, turn, text, resolve_msgs, output=resolve_result.output)

        # Pass 2 — narrator: render the resolved changes as player-facing prose (no tools,
        # so its full streamed text is the narration — nothing can be lost to a tool call).
        digest = "\n".join(f"- {line}" for line in deps.effects)
        prompt = assembler.build_narration_prompt(save_dir, text, digest)
        narration, narration_error = await self._stream_narration(
            save_dir, turn, text, prompt, trace, on_narration
        )

        store.append_transcript(save_dir, turn, text, narration)
        await self._housekeep(save_dir, resolve_result, turn, trace, narration)
        tick_error = await self._run_tick(save_dir, trace)
        trace.write("turn-end")
        return TurnResult(
            narration=narration,
            error=narration_error or tick_error,
            ended=self._player_dead(save_dir),
        )

    async def _stream_narration(
        self,
        save_dir: Path,
        turn: int,
        text: str,
        prompt: str,
        trace: TurnTrace,
        on_narration: Callable[[str], None] | None,
    ) -> tuple[str, str | None]:
        """Stream the narrator pass: filter reasoning, emit fragments live, return the prose.

        The narrator has no tools, so its full streamed text is the narration. On a
        mid-stream failure the partial is kept and marked incomplete (state is already
        mutated by the resolver); an empty result degrades to the engine fallback.
        """
        from pydantic_ai import capture_run_messages  # noqa: PLC0415 (lazy: off startup path)

        from aidm.orchestrator.prompts import ENGINE_FALLBACK  # noqa: PLC0415
        from aidm.orchestrator.streaming import ReasoningStripper  # noqa: PLC0415

        assert self._narrator is not None  # noqa: S101 (ensured by caller)
        stripper = ReasoningStripper()
        parts: list[str] = []

        def emit(visible: str) -> None:
            if visible:
                parts.append(visible)
                if on_narration is not None:
                    on_narration(visible)

        trace.write("narrate")
        with capture_run_messages() as messages:
            try:
                async with self._narrator.run_stream(prompt) as stream:
                    async for delta in stream.stream_text(delta=True):
                        emit(stripper.feed(delta))
                emit(stripper.flush())
            except Exception as exc:  # noqa: BLE001 (state already mutated; degrade prose only)
                emit(stripper.flush())
                trace.write("narrate-error", str(exc)[:120])
                _log.warning("Narrator failed", exc_info=True)
                self._log_llm(save_dir, turn, f"[narrate] {text}", messages, error=str(exc))
                narration = "".join(parts)
                if narration.strip():
                    note = "\n\n*(the telling falters)*"
                    emit(note)
                    return narration + note, "Narration was interrupted; state changes are saved."
                emit(ENGINE_FALLBACK)
                return ENGINE_FALLBACK, "Narration failed; state changes are saved."
        narration = "".join(parts)
        if not narration.strip():
            self._log_llm(save_dir, turn, f"[narrate] {text}", messages, output="")
            emit(ENGINE_FALLBACK)
            return ENGINE_FALLBACK, "Narrator returned no text; state changes are saved."
        self._log_llm(save_dir, turn, f"[narrate] {text}", messages, output=narration)
        return narration, None

    def _make_trace(self, save_dir: Path, turn: int) -> TurnTrace:
        from aidm.orchestrator.trace import TurnTrace  # noqa: PLC0415

        path = save_dir / TRACE_FILENAME if get_config().features.dev_mode else None
        return TurnTrace(path, turn)

    @staticmethod
    def _trace_node(trace: TurnTrace, node: Any) -> None:
        from pydantic_ai import Agent  # noqa: PLC0415

        if Agent.is_model_request_node(node):
            # Streaming seam: to stream tokens later, drive this node with `node.stream(run.ctx)`.
            trace.write("model-request")
        elif Agent.is_call_tools_node(node):
            parts = node.model_response.parts
            calls = [p.tool_name for p in parts if getattr(p, "part_kind", None) == "tool-call"]
            text_chars = sum(
                len(p.content) for p in parts if getattr(p, "part_kind", None) == "text"
            )
            trace.write("model-response", f"tools={calls} text={text_chars}")
        elif Agent.is_end_node(node):
            trace.write("model-end")

    async def _catch_up(self, save_dir: Path, trace: TurnTrace) -> None:
        """Catch up present, lagging entities; a sim failure must not break the player's turn."""
        trace.write("catch-up")
        caught: list[str] = []
        try:
            caught = sim.catch_up_present(save_dir)
        except Exception:  # noqa: BLE001 (sim is best-effort; gameplay continues)
            _log.warning("Catch-up failed", exc_info=True)
        trace.write("catch-up done", f"{len(caught)} entities")

    async def _run_tick(self, save_dir: Path, trace: TurnTrace) -> str | None:
        """Advance the world one turn; the turn still increments if a sim step fails."""
        trace.write("tick")
        try:
            await sim.tick(save_dir, self.generate)
        except Exception:  # noqa: BLE001 (sim is best-effort; gameplay continues)
            _log.warning("Tick failed", exc_info=True)
            return "World simulation was interrupted; earlier changes are saved."
        trace.write("tick done")
        return None

    async def _housekeep(
        self,
        save_dir: Path,
        run_result: AgentRunResult[str],
        turn: int,
        trace: TurnTrace,
        narration: str,
    ) -> None:
        """Pass 3: the maintainer updates story summary, beat status, and NPC inner state.

        Fed the resolver run's messages (the turn's state changes) plus the narration shown
        to the player. Best-effort — a failure here (including invalid structured output)
        must never break the player's turn.
        """
        from pydantic_ai import capture_run_messages  # noqa: PLC0415 (lazy: off startup path)

        from aidm.mcp import tools  # noqa: PLC0415
        from aidm.orchestrator import maintainer  # noqa: PLC0415 (imports pydantic_ai eagerly)

        if self._maintainer is None:
            return
        trace.write("housekeep")
        try:
            scenario = store.load_scenario(save_dir)
            player_slug = scenario.player
            involved = {
                slug
                for event in store.read_events(save_dir)
                if event.turn == turn
                for slug in ([event.actor, *event.targets])
                if isinstance(slug, str) and slug != player_slug
            }
            present: set[str] = set()
            try:
                location = (
                    store.load_entity(save_dir, player_slug).model_dump().get("current_location")
                )
            except FileNotFoundError:
                location = None
            if isinstance(location, str):
                present = {
                    slug
                    for slug in tools.get_location_occupants(save_dir, location)
                    if slug != player_slug
                }
            candidates = involved | present
            if not candidates and not scenario.outline:
                trace.write("housekeep done", "skipped")
                return
            story = store.load_story(save_dir)
            maintainer.seed_beats(story, scenario)
            store.save_story(save_dir, story)  # persist the seed even if the call below fails
            prompt = maintainer.build_context(save_dir, involved, present, story)
            prompt += f"\n\nNarration shown to the player this turn:\n{narration}"
            with capture_run_messages() as messages:
                result = await self._maintainer.run(
                    prompt, message_history=run_result.new_messages()
                )
            count = maintainer.apply(save_dir, result.output, candidates, story)
            self._log_llm(save_dir, turn, f"[maint] t{turn}", messages, output=str(result.output))
            trace.write("housekeep done", f"{count} npcs")
        except Exception:  # noqa: BLE001 (housekeeping is best-effort; gameplay continues)
            _log.warning("Housekeeping failed", exc_info=True)
            trace.write("housekeep done", "failed")

    async def aclose(self) -> None:
        """Tear down the agent/server connection (idempotent)."""
        await self._stack.aclose()
        self._started = False
        self._active_save = None
        self._sim_agent = None

    async def _safe_call(self, name: str, args: dict[str, Any]) -> bool:
        try:
            result = await self.call_tool(name, args)
        except Exception:  # noqa: BLE001 (best-effort lifecycle call)
            _log.warning("MCP call failed: %s", name, exc_info=True)
            return False
        return bool(isinstance(result, dict) and result.get("ok"))

    @staticmethod
    def _log_llm(
        save_dir: Path,
        turn: int,
        text: str,
        messages: list[ModelMessage],
        *,
        output: str | None = None,
        error: str | None = None,
    ) -> None:
        """In dev mode, append the full LLM exchange for one turn to ``llm.jsonl`` and ``llm.md``.

        Records everything the model saw and produced: the prompt context, tool calls,
        tool returns, reasoning, and the final output — so a turn can be replayed and
        debugged. ``llm.jsonl`` is the machine-readable record; ``llm.md`` is a readable
        Markdown rendering of the same exchange. Never raises into gameplay.
        """
        if not get_config().features.dev_mode:
            return
        try:
            from pydantic_ai.messages import ModelMessagesTypeAdapter  # noqa: PLC0415

            from aidm.orchestrator.llm_md import render_exchange  # noqa: PLC0415

            record = {
                "ts": datetime.now(UTC).isoformat(),
                "turn": turn,
                "input": text,
                "output": output,
                "error": error,
                "messages": json.loads(ModelMessagesTypeAdapter.dump_json(messages)),
            }
            with (save_dir / LLM_LOG_FILENAME).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
            with (save_dir / LLM_MD_FILENAME).open("a", encoding="utf-8") as handle:
                handle.write(render_exchange(turn, text, messages, error=error))
        except Exception:  # noqa: BLE001 (diagnostics must never break a turn)
            _log.warning("Failed to write LLM trace", exc_info=True)

    @staticmethod
    def _player_dead(save_dir: Path) -> bool:
        scenario = store.load_scenario(save_dir)
        try:
            player = store.load_entity(save_dir, scenario.player)
        except FileNotFoundError:
            return False
        health = player.model_dump().get("health")
        return isinstance(health, (int, float)) and not isinstance(health, bool) and health <= 0
