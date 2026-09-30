"""Render an LLM exchange (pydantic-ai messages) as readable Markdown for dev review.

The ``llm.jsonl`` trace is the machine-readable record (escaped, one JSON object per
turn). This renders the same messages as Markdown — real newlines, fenced prompts, and
clearly labelled reasoning/tool/output sections — so a turn can be read at a glance.
Operates purely on message attributes via ``getattr``; imports no heavy deps.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value)
    return json.dumps(value, indent=2, default=str)


def _fence(text: str, lang: str = "") -> str:
    return f"```{lang}\n{text.strip()}\n```"


def _args(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value.strip()
    return json.dumps(value, indent=2, default=str)


def _subtitle(label: str) -> str:
    if label.startswith("[maint]"):
        return "_housekeeping pass_"
    if label.startswith("[sim]"):
        return f"_simulation:_ {label[len('[sim]') :].strip()}"
    if label.startswith("[narrate]"):
        return f"_narration:_ {label[len('[narrate]') :].strip()}"
    return f"**Player:** {label}"


def render_exchange(
    turn: int,
    label: str,
    messages: Sequence[ModelMessage],
    *,
    error: str | None = None,
) -> str:
    """Render one logged exchange (DM, maintainer, or sim) as a Markdown section."""
    lines: list[str] = [f"## Turn {turn}", "", _subtitle(label), ""]
    for message in messages:
        for part in getattr(message, "parts", []):
            kind = getattr(part, "part_kind", "")
            if kind == "user-prompt":
                lines += ["### Prompt", "", _fence(_text(part.content)), ""]
            elif kind == "thinking":
                lines += ["### Reasoning", "", _text(part.content).strip(), ""]
            elif kind == "tool-call":
                lines += [
                    f"### Tool call → `{part.tool_name}`",
                    "",
                    _fence(_args(part.args), "json"),
                    "",
                ]
            elif kind == "tool-return":
                lines += [
                    f"### Tool result ← `{part.tool_name}`",
                    "",
                    _fence(_text(part.content)),
                    "",
                ]
            elif kind == "retry-prompt":
                lines += ["### Retry", "", _fence(_text(part.content)), ""]
            elif kind == "text":
                lines += ["### Output", "", _text(part.content).strip(), ""]
    if error:
        lines += ["### Error", "", _fence(error), ""]
    lines += ["---", ""]
    return "\n".join(lines)
