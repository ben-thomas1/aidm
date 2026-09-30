"""The story model: per-save narrative memory (v2).

Holds the LLM-maintained "story so far" summary and the status of authored outline
beats. Populated by the pass-2 maintainer (v2 Phase 2); defined here as substrate.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Story(BaseModel):
    """Long-term narrative memory: a running summary and outline-beat progress."""

    model_config = ConfigDict(extra="allow")

    summary: str = ""
    # Each beat: {"id": str, "status": "pending"|"active"|"done"}.
    beats: list[dict[str, Any]] = Field(default_factory=list)
