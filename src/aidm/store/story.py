"""Read/write per-save narrative memory (`story.json`).

The maintainer writes the running summary and beat status; the context assembler
reads this memory on subsequent turns.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from aidm.const import STORY_FILENAME
from aidm.domain import Story
from aidm.store.io import atomic_write_text

if TYPE_CHECKING:
    from pathlib import Path


def story_path(save_dir: Path) -> Path:
    """Return the path to the save's ``story.json``."""
    return save_dir / STORY_FILENAME


def load_story(save_dir: Path) -> Story:
    """Load the narrative memory, or an empty ``Story`` if the file is absent."""
    path = story_path(save_dir)
    if not path.is_file():
        return Story()
    return Story.model_validate_json(path.read_text(encoding="utf-8"))


def save_story(save_dir: Path, story: Story) -> None:
    """Write the narrative memory atomically."""
    atomic_write_text(story_path(save_dir), json.dumps(story.model_dump(), indent=2) + "\n")
