"""The scenario model: narrative setup for a game."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aidm.paths import identifier


class Scenario(BaseModel):
    """Narrative setup for a game: goal, hard rules, and framing."""

    model_config = ConfigDict(extra="allow")

    title: str
    synopsis: str = ""
    goal: dict[str, Any] | None = None
    player: str = "player"
    hard_rules: list[dict[str, Any]] = Field(default_factory=list)
    background_event_chance: float = Field(default=0.0, ge=0, le=1, allow_inf_nan=False)
    # Optional authored story beats the DM may realise at its discretion (v2).
    # Each beat: {"id": str, "description": str, "hint"?: str}.
    outline: list[dict[str, Any]] = Field(default_factory=list)
    # Optional extra protected keys per entity type, merged with the code defaults.
    protected: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("player")
    @classmethod
    def _player_slug(cls, value: str) -> str:
        return identifier(value)

    @field_validator("hard_rules")
    @classmethod
    def _supported_rules(cls, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for rule in rules:
            if rule.get("kind") != "forbid_event_type" or not isinstance(rule.get("type"), str):
                msg = "Only forbid_event_type rules with a string type are supported."
                raise ValueError(msg)
        return rules
