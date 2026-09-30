"""Lazy/observer-driven world simulation (§8): near progression, background events, catch-up."""

from __future__ import annotations

from aidm.sim.catchup import catch_up, catch_up_present
from aidm.sim.relevance import near_entities
from aidm.sim.tick import fire_background, tick

__all__ = ["catch_up", "catch_up_present", "fire_background", "near_entities", "tick"]
