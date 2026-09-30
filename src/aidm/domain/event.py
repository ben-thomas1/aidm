"""The event envelope: append-only world history."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Event(BaseModel):
    """A single recorded world change.

    The envelope is fixed; ``type`` is an open string and ``payload`` carries the
    type-specific data. ``outcome`` records an outcome roll when one fed the
    action; ``covers_turns`` marks a compressed catch-up summary.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    turn: int
    ts: str
    type: str
    actor: str | None = None
    targets: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    outcome: dict[str, Any] | None = None
    caused_by: str | None = None
    covers_turns: tuple[int, int] | None = None
