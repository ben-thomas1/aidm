"""The entity model: one file per world entity."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aidm.const import MAX_CONDITIONS, MAX_TRAITS
from aidm.paths import identifier


class Entity(BaseModel):
    """A world entity, stored flat (v2: no nested ``static`` block).

    Only ``slug``/``type``/``name`` are required. Everything else — authored traits
    (``race``, ``description``, ``connections``…), dynamic gameplay state, and the v2
    memory fields below — lives at the top level and round-trips unchanged. Authored
    identity is kept immutable by the write tools (protected keys), not by the schema.
    """

    model_config = ConfigDict(extra="allow", allow_inf_nan=False)

    slug: str
    type: str
    name: str
    traits: list[str] = Field(default_factory=list, max_length=MAX_TRAITS)
    conditions: list[str] = Field(default_factory=list, max_length=MAX_CONDITIONS)
    current_goal: str | None = None
    mindset: str | None = None

    @field_validator("slug", "type")
    @classmethod
    def _identifier(cls, value: str) -> str:
        return identifier(value)

    @model_validator(mode="after")
    def _core_fields(self) -> Entity:
        if self.type == "event":
            msg = "The entity type 'event' is reserved for the event log."
            raise ValueError(msg)
        data = self.model_dump()
        for field in ("health", "money", "stamina"):
            value = data.get(field)
            if field in data and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                msg = f"{field} must be a finite number."
                raise ValueError(msg)
        if data.get("money", 0) < 0:
            msg = "money cannot be negative."
            raise ValueError(msg)
        for field in ("current_location", "owner"):
            value = data.get(field)
            if value is not None:
                if not isinstance(value, str):
                    msg = f"{field} must be an entity slug or null."
                    raise ValueError(msg)
                identifier(value)
        relationships = data.get("relationships", {})
        if not isinstance(relationships, dict):
            msg = "relationships must be an object."
            raise ValueError(msg)  # noqa: TRY004 (Pydantic validators use ValueError)
        for slug, relationship in relationships.items():
            identifier(slug)
            if not isinstance(relationship, dict):
                msg = "Each relationship must be an object."
                raise ValueError(msg)  # noqa: TRY004
            value = relationship.get("value", 0)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                msg = "Relationship values must be finite numbers."
                raise ValueError(msg)
        return self
