"""Read/write per-save metadata (`meta.json`)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from aidm.const import META_FILENAME
from aidm.domain import Meta
from aidm.store.io import atomic_write_text

if TYPE_CHECKING:
    from pathlib import Path


def meta_path(save_dir: Path) -> Path:
    """Return the path to the save's ``meta.json``."""
    return save_dir / META_FILENAME


def load_meta(save_dir: Path) -> Meta:
    """Load the save metadata, or defaults if the file is absent."""
    path = meta_path(save_dir)
    if not path.is_file():
        return Meta()
    return Meta.model_validate_json(path.read_text(encoding="utf-8"))


def save_meta(save_dir: Path, meta: Meta) -> None:
    """Write the save metadata."""
    atomic_write_text(meta_path(save_dir), json.dumps(meta.model_dump(), indent=2) + "\n")
