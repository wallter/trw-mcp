"""Plant the WAL-checkpoint timestamp markers a doctor test reads.

The daemon writes these markers (``trw_memory.storage._wal_checkpoint.stamp_checkpoint_markers``); trw-mcp only
reads them, so the writers that used to live in ``state/_wal_triggers`` are gone (UF-MEM-26). A test that needs a
marker of a given age plants the file itself, in the one format the readers parse: epoch seconds and a newline.
"""

from __future__ import annotations

import time
from pathlib import Path

from trw_mcp.state._wal_triggers import (
    checkpoint_marker_path,
    effective_checkpoint_marker_path,
    reset_checkpoint_marker_path,
)


def _plant(marker: Path, now: float | None) -> bool:
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{time.time() if now is None else now:.3f}\n", encoding="utf-8")
    return True


def record_checkpoint_attempt(db_path: Path, *, now: float | None = None) -> bool:
    return _plant(checkpoint_marker_path(db_path), now)


def record_effective_checkpoint(db_path: Path, *, now: float | None = None) -> bool:
    return _plant(effective_checkpoint_marker_path(db_path), now)


def record_reset_checkpoint(db_path: Path, *, now: float | None = None) -> bool:
    return _plant(reset_checkpoint_marker_path(db_path), now)
