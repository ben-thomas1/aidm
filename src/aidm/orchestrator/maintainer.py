"""Pass-2 housekeeping: the maintainer updates world memory after the DM acts (v2 §6).

The same model that just played the turn continues the conversation and emits a *structured*
``Housekeeping`` update — the running story summary, outline-beat status, and per-NPC
``current_goal``/``mindset``. It runs as a no-toolset agent (structured output rides an
implicit output tool, so it cannot call the MCP tools) fed the DM run's messages as history.

This module imports ``pydantic_ai`` eagerly, so the engine imports it lazily.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel
from pydantic_ai import Agent

from aidm import store
from aidm.mcp import tools

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from pydantic_ai.models import Model

    from aidm.domain import Scenario, Story


class BeatStatus(BaseModel):
    """An outline beat's progress."""

    id: str
    status: Literal["pending", "active", "done"]


class NpcInner(BaseModel):
    """An update to one character's inner state."""

    slug: str
    current_goal: str | None = None
    mindset: str | None = None


class Housekeeping(BaseModel):
    """The structured post-turn world-memory update."""

    summary: str = ""
    beats: list[BeatStatus] = []
    npcs: list[NpcInner] = []


MAINTAINER_PROMPT = """\
You are the silent bookkeeper for a text RPG. After each turn you update the world's \
memory based on what just happened (shown earlier in this conversation). Return ONLY the \
structured update — never narrate.

- summary: the running "story so far". Evolve the existing summary with what just \
happened; keep it to a short paragraph. Do not restart it from scratch.
- beats: for each listed story beat whose progress changed, set its status to "pending", \
"active", or "done". Omit beats that did not change.
- npcs: update the inner state of characters affected this turn.
  - current_goal: what the character is now trying to do.
  - mindset: their present thoughts, feelings, and disposition toward the player — a \
state of mind, NOT a recap of events.
  Always update the characters marked must-update. For others merely present in the \
scene, update only the ones the events plausibly affected; leave the rest out.
Never invent characters; use only the slugs given. Return only what actually changed.
"""


def build_maintainer(model: Model) -> Agent[None, Housekeeping]:
    """Build the no-tools, structured-output maintainer agent over ``model``."""
    return Agent[None, Housekeeping](
        model,
        output_type=Housekeeping,
        instructions=MAINTAINER_PROMPT,
    )


def seed_beats(story: Story, scenario: Scenario) -> None:
    """Initialise beat tracking from the scenario outline if it hasn't been seeded yet."""
    if story.beats or not scenario.outline:
        return
    story.beats = [
        {"id": beat["id"], "status": "pending"}
        for beat in scenario.outline
        if isinstance(beat, dict) and beat.get("id")
    ]


def _snapshot(save_dir: Path, slug: str) -> str:
    data = tools.get_entity(save_dir, slug)
    if data.get("error"):
        return f"- {slug} (unknown)"
    return (
        f"- {slug} ({data.get('name')}): goal={data.get('current_goal')!r} "
        f"mindset={data.get('mindset')!r} status={data.get('status')!r}"
    )


def build_context(
    save_dir: Path,
    involved: Iterable[str],
    present: Iterable[str],
    story: Story,
) -> str:
    """Assemble the pass-2 user prompt (outline + beats + NPC snapshots + current summary)."""
    scenario = store.load_scenario(save_dir)
    outline = {b["id"]: b for b in scenario.outline if isinstance(b, dict) and b.get("id")}
    must = list(dict.fromkeys(involved))
    bystanders = [slug for slug in dict.fromkeys(present) if slug not in set(must)]

    beat_lines = []
    for beat in story.beats:
        if not isinstance(beat, dict):
            continue
        meta = outline.get(beat.get("id"), {})
        desc = meta.get("description", "")
        hint = f"  (hint: {meta['hint']})" if meta.get("hint") else ""
        beat_lines.append(f"- {beat.get('id')} [{beat.get('status')}]: {desc}{hint}")

    parts = [
        "Update the world memory after this turn.",
        "\nStory summary so far:\n" + (story.summary or "(none yet)"),
        "\nStory beats:\n" + ("\n".join(beat_lines) if beat_lines else "(none)"),
        "\nMust-update these characters:\n"
        + ("\n".join(_snapshot(save_dir, s) for s in must) if must else "(none)"),
        "\nAlso present in the scene (update only if affected):\n"
        + ("\n".join(_snapshot(save_dir, s) for s in bystanders) if bystanders else "(none)"),
    ]
    return "\n".join(parts)


def apply(save_dir: Path, update: Housekeeping, candidates: set[str], story: Story) -> int:
    """Persist the update: summary + beat statuses to ``story.json``, NPC inner state via tools.

    Only NPC slugs in ``candidates`` are applied. Returns the number of NPCs updated.
    """
    by_id = {b["id"]: b for b in story.beats if isinstance(b, dict) and b.get("id")}
    for beat in update.beats:
        if beat.id in by_id:
            by_id[beat.id]["status"] = beat.status
    if update.summary:
        story.summary = update.summary
    store.save_story(save_dir, story)

    updated = 0
    for npc in update.npcs:
        if npc.slug not in candidates:
            continue
        changes = {
            key: value
            for key, value in (("current_goal", npc.current_goal), ("mindset", npc.mindset))
            if value is not None
        }
        if changes and tools.propose_state_change(save_dir, npc.slug, changes).get("ok"):
            updated += 1
    return updated


__all__ = [
    "MAINTAINER_PROMPT",
    "Housekeeping",
    "apply",
    "build_context",
    "build_maintainer",
    "seed_beats",
]
