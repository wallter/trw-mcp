from __future__ import annotations

from pathlib import Path


def _trw_dir(tmp_path: Path) -> Path:
    """Create and return the .trw directory under tmp_path."""
    trw = tmp_path / ".trw"
    trw.mkdir(parents=True, exist_ok=True)
    return trw


def _state_file(trw_dir: Path) -> Path:
    return trw_dir / "context" / "ceremony-state.json"
