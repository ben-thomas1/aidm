"""Constrain user/model supplied identifiers and reject linked save contents."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def identifier(value: str) -> str:
    """Accept a single portable filename component, never a path."""
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,99}", value):
        msg = "Names must be 1-100 letters, digits, underscores or hyphens, starting with a letter, digit or underscore."
        raise ValueError(msg)
    return value


def unlinked(path: Path) -> Path:
    """Reject symlinks in a path before reading or writing through it."""
    if any(p.is_symlink() for p in (path, *path.parents)):
        msg = "Symbolic links are not supported in game or save paths."
        raise ValueError(msg)
    return path


def plain_tree(root: Path) -> None:
    """Check an entire game/save before copying it or activating it."""
    unlinked(root)
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            msg = "Games and saves must contain only regular files and directories."
            raise ValueError(msg)
