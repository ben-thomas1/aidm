"""The save metadata model."""

from __future__ import annotations

from pydantic import BaseModel


class Meta(BaseModel):
    """Per-save bookkeeping: when it began, the turn counter, and its source."""

    created_at: str | None = None
    current_turn: int = 0
    source_game: str | None = None
