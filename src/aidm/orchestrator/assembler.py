"""Per-turn context assembler (v2 §2): build the DM prompt from the maintained memory.

Replaces the old JSON world-dump + raw message window. Produces a single, sectioned,
plain-text payload from the scenario, the long-term story memory, the recent transcript,
and the current scene. Entity/turn counts are limited; text lengths have no hard token
budget. Pure (store/tools reads, no LLM), so assembly is deterministic each turn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aidm import store
from aidm.const import MAX_PRESENT_IN_CONTEXT, TRANSCRIPT_TURNS
from aidm.mcp import tools

if TYPE_CHECKING:
    from pathlib import Path


def _fmt_list(values: object) -> str:
    if isinstance(values, list) and values:
        return ", ".join(str(v) for v in values)
    return "none"


def _load(save_dir: Path, slug: str) -> dict[str, Any] | None:
    try:
        return store.load_entity(save_dir, slug).model_dump()
    except FileNotFoundError:
        return None


def _world(scenario: Any) -> str:
    lines = ["## WORLD", scenario.title]
    if scenario.synopsis:
        lines.append(scenario.synopsis)
    if isinstance(scenario.goal, dict) and scenario.goal.get("description"):
        lines.append(f"Goal: {scenario.goal['description']}")
    return "\n".join(lines)


def _story_so_far(story: Any) -> str:
    return "## STORY SO FAR\n" + (story.summary or "(nothing yet)")


def _beats(scenario: Any, story: Any) -> str:
    status_by_id = {
        b["id"]: b.get("status", "pending")
        for b in story.beats
        if isinstance(b, dict) and b.get("id")
    }
    lines = ["## STORY BEATS"]
    outline = [b for b in scenario.outline if isinstance(b, dict) and b.get("id")]
    if not outline:
        lines.append("(none)")
        return "\n".join(lines)
    for beat in outline:
        status = status_by_id.get(beat["id"], "pending")
        hint = f"  (hint: {beat['hint']})" if beat.get("hint") else ""
        lines.append(f"- [{status}] {beat.get('description', '')}{hint}")
    return "\n".join(lines)


def _recent(save_dir: Path) -> str:
    records = store.read_transcript(save_dir, limit=TRANSCRIPT_TURNS)
    lines = ["## RECENT"]
    if not records:
        lines.append("(the story has just begun)")
        return "\n".join(lines)
    for record in records:
        lines.append(f"You: {record.get('input', '')}")
        lines.append(f"DM: {record.get('narration', '')}")
    return "\n".join(lines)


def _location(save_dir: Path, location: object) -> str:
    lines = ["## LOCATION"]
    if not isinstance(location, str):
        lines.append("(nowhere in particular)")
        return "\n".join(lines)
    try:
        data = store.load_entity(save_dir, location).model_dump()
    except FileNotFoundError:
        lines.append(location)
        return "\n".join(lines)
    header = str(data.get("name") or location)
    if data.get("status"):
        header += f" ({data['status']})"
    lines.append(header)
    if data.get("description"):
        lines.append(str(data["description"]))
    lines.append(f"Exits: {_fmt_list(data.get('connections'))}")
    return "\n".join(lines)


def _npc_block(data: dict[str, Any], toward: dict[str, Any]) -> str:
    header = str(data.get("name") or data.get("slug"))
    if data.get("role"):
        header += f" ({data['role']})"
    if data.get("status"):
        header += f" — {data['status']}"
    lines = [header]
    lines.append(f"  traits: {_fmt_list(data.get('traits'))}")
    lines.append(f"  conditions: {_fmt_list(data.get('conditions'))}")
    if data.get("current_goal"):
        lines.append(f"  goal: {data['current_goal']}")
    if data.get("mindset"):
        lines.append(f"  mindset: {data['mindset']}")
    if isinstance(toward, dict) and toward:
        value = toward.get("value")
        note = toward.get("note")
        rel = " ".join(
            part for part in (f"{value}" if value is not None else "", str(note or "")) if part
        )
        lines.append(f"  toward you: {rel.strip()}")
    return "\n".join(lines)


def _present(save_dir: Path, location: object, player: str) -> str:
    lines = ["## PRESENT"]
    if not isinstance(location, str):
        lines.append("(no one)")
        return "\n".join(lines)
    occupants = [s for s in tools.get_location_occupants(save_dir, location) if s != player]
    if not occupants:
        lines.append("(no one)")
        return "\n".join(lines)
    shown = occupants[:MAX_PRESENT_IN_CONTEXT]
    for slug in shown:
        data = _load(save_dir, slug)
        if data is not None:
            lines.append(_npc_block(data, tools.get_relationship(save_dir, slug, player)))
    extra = len(occupants) - len(shown)
    if extra > 0:
        lines.append(f"(+{extra} others present)")
    return "\n".join(lines)


def _you(save_dir: Path, player: str) -> str:
    lines = ["## YOU"]
    try:
        data = store.load_entity(save_dir, player).model_dump()
    except FileNotFoundError:
        lines.append("(unknown)")
        return "\n".join(lines)
    header = str(data.get("name") or player)
    vitals = [
        f"{f}: {data[f]}"
        for f in ("health", "money", "stamina", "status")
        if data.get(f) is not None
    ]
    lines.append(header + (" — " + ", ".join(vitals) if vitals else ""))
    if data.get("current_location"):
        lines.append(f"location: {data['current_location']}")
    inventory = tools.find_entities(save_dir, {"type": "item", "owner": player})
    names = [tools.get_entity(save_dir, s).get("name") or s for s in inventory]
    lines.append(f"inventory: {_fmt_list(names)}")
    return "\n".join(lines)


def build_context(save_dir: Path) -> str:
    """Assemble the sectioned, plain-text world context for the current turn (no LLM)."""
    scenario = store.load_scenario(save_dir)
    story = store.load_story(save_dir)
    player = scenario.player
    try:
        location = store.load_entity(save_dir, player).model_dump().get("current_location")
    except FileNotFoundError:
        location = None
    sections = [
        _world(scenario),
        _story_so_far(story),
        _beats(scenario, story),
        _recent(save_dir),
        _location(save_dir, location),
        _present(save_dir, location, player),
        _you(save_dir, player),
    ]
    return "\n\n".join(sections)


def build_prompt(save_dir: Path, action: str) -> str:
    """The resolver prompt: assembled context followed by the player's action."""
    return f"{build_context(save_dir)}\n\n## PLAYER ACTION\n{action}"


def build_narration_prompt(save_dir: Path, action: str, digest: str) -> str:
    """The narrator prompt: post-mutation context + the action + this turn's effects digest.

    Call this *after* the resolver's writes so ``build_context`` reflects the new state.
    """
    changes = digest.strip() or "(no mechanical changes this turn)"
    return (
        f"{build_context(save_dir)}\n\n"
        f"## PLAYER ACTION\n{action}\n\n"
        f"## CHANGES THIS TURN\n{changes}"
    )
