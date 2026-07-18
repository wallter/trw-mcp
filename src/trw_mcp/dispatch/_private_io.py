"""Private atomic writes for prompt- and result-bearing dispatch artifacts."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_private_atomic(path: Path, text: str) -> None:
    """Atomically replace *path* with UTF-8 text readable only by its owner."""
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
