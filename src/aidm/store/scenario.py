"""Load the scenario file for a save."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aidm.const import SCENARIO_FILENAME
from aidm.domain import Scenario

if TYPE_CHECKING:
    from pathlib import Path


def load_scenario(save_dir: Path) -> Scenario:
    """Load the save's scenario."""
    path = save_dir / SCENARIO_FILENAME
    return Scenario.model_validate_json(path.read_text(encoding="utf-8"))
