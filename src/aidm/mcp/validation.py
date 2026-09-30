"""Hard-rule validation for proposed events (universal + scenario rules)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aidm import store
from aidm.const import PROTECTED_FIELDS_BASE, PROTECTED_FIELDS_BY_TYPE
from aidm.store.projections import project_event

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from aidm.domain import Entity, Event

_log = logging.getLogger("aidm.mcp.validation")

# Fields that may never go negative.
_CONSERVED_FIELDS = {"money"}
# Event types allowed to touch a non-living entity.
_LIVENESS_EXEMPT_TYPES = {"entity_created"}


def validate(save_dir: Path, event: Event) -> tuple[bool, str | None, str | None]:
    """Validate a proposed event. Returns ``(ok, rule, reason)``."""
    try:
        projected = project_event(save_dir, event)
        if projected is not None:
            data = projected.model_dump()
            for field in ("current_location", "owner"):
                slug = data.get(field)
                if slug is None:
                    continue
                target = _load_or_none(save_dir, slug)
                if target is None or (field == "current_location" and target.type != "location"):
                    return (
                        False,
                        "entity_reference",
                        f"{field} must reference an existing {'location' if field == 'current_location' else 'entity'}",
                    )
            for slug in data.get("relationships", {}):
                if slug != projected.slug and not _exists(save_dir, slug):
                    return False, "entity_reference", "Relationship target does not exist."
    except ValueError, TypeError, KeyError, FileNotFoundError:
        return False, "invalid_change", "Invalid entity, field value, or event payload."
    for rule, check in (
        ("entity_existence", _check_existence),
        ("protected_field", _check_protected),
        ("resource_conservation", _check_resources),
        ("liveness", _check_liveness),
    ):
        reason = check(save_dir, event)
        if reason is not None:
            return False, rule, reason
    scenario_rule, scenario_reason = _check_scenario(save_dir, event)
    if scenario_reason is not None:
        return False, scenario_rule, scenario_reason
    return True, None, None


def _referenced_slugs(event: Event) -> list[str]:
    slugs = list(event.targets)
    target = event.payload.get("target")
    if isinstance(target, str):
        slugs.append(target)
    return slugs


def _load_or_none(save_dir: Path, slug: str) -> Entity | None:
    try:
        return store.load_entity(save_dir, slug)
    except FileNotFoundError:
        return None


def _exists(save_dir: Path, slug: str) -> bool:
    return _load_or_none(save_dir, slug) is not None


def _check_existence(save_dir: Path, event: Event) -> str | None:
    if event.type == "entity_created":
        slug = event.payload.get("slug")
        if isinstance(slug, str) and _exists(save_dir, slug):
            return f"entity already exists: {slug}"
        return None
    for slug in _referenced_slugs(event):
        if not _exists(save_dir, slug):
            return f"no such entity: {slug}"
    return None


def _protected_fields(save_dir: Path, entity_type: str) -> frozenset[str]:
    """Protected keys for ``entity_type``: code defaults + optional scenario overrides."""
    fields = set(PROTECTED_FIELDS_BASE) | set(
        PROTECTED_FIELDS_BY_TYPE.get(entity_type, frozenset())
    )
    try:
        scenario = store.load_scenario(save_dir)
    except FileNotFoundError:
        return frozenset(fields)
    extra = scenario.protected.get(entity_type) if isinstance(scenario.protected, dict) else None
    if isinstance(extra, list):
        fields |= {f for f in extra if isinstance(f, str)}
    return frozenset(fields)


def _check_protected(save_dir: Path, event: Event) -> str | None:
    if event.type != "state_changed" or not event.targets:
        return None
    field = event.payload.get("field")
    if not isinstance(field, str):
        return None
    entity = _load_or_none(save_dir, event.targets[0])
    if entity is None:
        return None  # existence check reports this
    if field in _protected_fields(save_dir, entity.type):
        return f"'{field}' is an immutable (protected) field and cannot be changed"
    return None


def _check_resources(save_dir: Path, event: Event) -> str | None:
    field = event.payload.get("field")
    if event.type != "state_changed" or field not in _CONSERVED_FIELDS or not event.targets:
        return None
    entity = _load_or_none(save_dir, event.targets[0])
    if entity is None:
        return None  # existence check reports this
    current = entity.model_dump().get(field, 0)
    if not isinstance(current, int | float):
        current = 0
    if "value" in event.payload:
        new_value = event.payload["value"]
    elif "delta" in event.payload:
        delta = event.payload["delta"]
        new_value = current + delta if isinstance(delta, int | float) else None
    else:
        return None
    if not isinstance(new_value, int | float):
        return f"{field} must be numeric"
    if new_value < 0:
        return f"{field} cannot go negative (would be {new_value})"
    return None


def _check_liveness(save_dir: Path, event: Event) -> str | None:
    if event.type in _LIVENESS_EXEMPT_TYPES:
        return None
    if event.type == "state_changed" and event.payload.get("field") == "health":
        return None  # allow any health change (revive / heal / finalize a death)
    for slug in _referenced_slugs(event):
        entity = _load_or_none(save_dir, slug)
        if entity is None:
            continue
        health = entity.model_dump().get("health")
        if isinstance(health, int | float) and health <= 0:
            return f"entity is not alive: {slug}"
    return None


def _forbid_event_type(rule: dict[str, Any], event: Event) -> str | None:
    if rule.get("type") == event.type:
        return f"event type '{event.type}' is forbidden by the scenario"
    return None


_SCENARIO_CHECKERS = {"forbid_event_type": _forbid_event_type}


def _check_scenario(save_dir: Path, event: Event) -> tuple[str | None, str | None]:
    try:
        scenario = store.load_scenario(save_dir)
    except FileNotFoundError:
        return None, None
    for rule in scenario.hard_rules:
        kind = rule.get("kind")
        if not isinstance(kind, str):
            return "invalid_rule", "Scenario rule requires a string kind."
        checker = _SCENARIO_CHECKERS.get(kind)
        if checker is None:
            return "invalid_rule", "Unsupported scenario rule kind."
        if (
            rule.get("type") == "revive"
            and event.type == "state_changed"
            and event.payload.get("field") == "health"
            and event.targets
        ):
            entity = _load_or_none(save_dir, event.targets[0])
            if entity is not None:
                before = entity.model_dump().get("health", 1)
                after = event.payload.get("value", before + event.payload.get("delta", 0))
                if before <= 0 < after:
                    return "scenario:forbid_event_type", "Revival is forbidden by the scenario."
        reason = checker(rule, event)
        if reason is not None:
            return f"scenario:{kind}", reason
    return None, None
