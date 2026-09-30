"""Atomic single-file replacement using a same-directory temporary and fsync.

This protects file contents against interrupted writes. It is not a multi-file
transaction or a guarantee of directory metadata durability after power loss.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from aidm.paths import unlinked


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp file in the same dir + ``Path.replace``)."""
    unlinked(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
