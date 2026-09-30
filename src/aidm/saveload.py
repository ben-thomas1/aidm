"""Filesystem management of the games catalog and saves.

``games/`` holds committed initial games (read-only sources); ``saves/`` holds
playthroughs copied from a game. Paths resolve relative to the working directory.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from aidm.const import GAMES_DIR, SAVES_DIR
from aidm.domain import Meta
from aidm.paths import identifier, plain_tree, unlinked
from aidm.store.meta import save_meta


def games_root() -> Path:
    """Return the games-catalog directory."""
    return Path(GAMES_DIR)


def saves_root() -> Path:
    """Return the saves directory."""
    return Path(SAVES_DIR)


def _list_dirs(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    unlinked(root)
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
    )


def list_games() -> list[str]:
    """Return the names of available initial games."""
    return _list_dirs(games_root())


def list_saves() -> list[str]:
    """Return the names of existing saves."""
    return _list_dirs(saves_root())


def game_exists(name: str) -> bool:
    """Return whether an initial game with ``name`` exists."""
    return unlinked(games_root() / identifier(name)).is_dir()


def save_exists(name: str) -> bool:
    """Return whether a save with ``name`` exists."""
    return save_path(name).is_dir()


def save_path(name: str) -> Path:
    """Return the path to the save named ``name`` (whether or not it exists)."""
    return unlinked(saves_root() / identifier(name))


def create_save(game: str, save: str) -> Path:
    """Copy ``games/<game>`` to ``saves/<save>`` and return the new save path.

    Raises
    ------
    FileNotFoundError
        If the source game does not exist.
    FileExistsError
        If a save with that name already exists.
    """
    src = unlinked(games_root() / identifier(game))
    plain_tree(src)
    if not src.is_dir():
        msg = f"No such game: {game}"
        raise FileNotFoundError(msg)
    validate_save(src, recover=False)
    dst = save_path(save)
    if dst.exists():
        msg = f"Save already exists: {save}"
        raise FileExistsError(msg)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    save_meta(dst, Meta(created_at=datetime.now(UTC).isoformat(), source_game=game))
    return dst


def validate_save(path: Path, *, recover: bool = True) -> None:
    """Check the save before activation, recovering an interrupted event batch."""
    from aidm import store  # noqa: PLC0415 (keep listing/startup imports light)

    plain_tree(path)
    if recover:
        store.recover_pending(path)
    scenario = store.load_scenario(path)
    entities = store.list_entities(path)
    if not any(e.slug == scenario.player and e.type == "character" for e in entities):
        msg = "Scenario must reference an existing player character."
        raise ValueError(msg)
    store.load_meta(path)
    store.load_story(path)
    store.read_events(path)
    store.read_transcript(path)


def delete_save(name: str) -> None:
    """Delete the save directory named ``name``.

    Raises
    ------
    FileNotFoundError
        If no such save exists.
    """
    path = save_path(name)
    if not path.is_dir():
        msg = f"No such save: {name}"
        raise FileNotFoundError(msg)
    shutil.rmtree(path)
