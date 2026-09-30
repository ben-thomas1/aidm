"""Deterministic decay/regen over a top-level ``regen`` field convention.

A regen spec lives in an entity's top-level ``regen`` block::

    {"stamina": {"rate": 5, "min": 0, "max": 100}}

Each tick the field moves by ``rate`` (negative = decay), clamped to ``[min, max]``
when given. Over a gap of ``ticks`` turns the field moves by ``rate * ticks``. This is
the only deterministic, no-LLM part of near-progression and catch-up.
"""

from __future__ import annotations

import math
from typing import Any


def _as_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def regen_changes(entity_data: dict[str, Any], ticks: int) -> dict[str, float]:
    """Return new field values produced by advancing the ``regen`` spec by ``ticks``.

    Only fields whose value actually changes are returned. Fields without a numeric
    current value or a numeric ``rate`` are skipped.
    """
    if ticks <= 0:
        return {}
    specs = entity_data.get("regen")
    if not isinstance(specs, dict):
        return {}
    changes: dict[str, float] = {}
    for field, spec in specs.items():
        if field == "health" and entity_data.get("health", 1) <= 0:
            continue  # passive regeneration must not resurrect dead characters
        if not isinstance(spec, dict):
            continue
        rate = _as_number(spec.get("rate"))
        current = _as_number(entity_data.get(field))
        if rate is None or current is None:
            continue
        new_value = current + rate * ticks
        low = _as_number(spec.get("min"))
        high = _as_number(spec.get("max"))
        if low is not None:
            new_value = max(new_value, low)
        if high is not None:
            new_value = min(new_value, high)
        if field == "money":
            new_value = max(new_value, 0)
        if math.isfinite(new_value) and new_value != current:
            changes[field] = new_value
    return changes
