"""Shared utility helpers for state modules.

Centralizes common patterns that were duplicated across analytics.py,
tiers.py, consolidation.py, dedup.py, and other state modules.

This module should NOT import from tools/ or other state modules to
avoid circular dependencies. It only depends on models/ and persistence.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterator

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Safe type extraction from dict[str, object] values
# ---------------------------------------------------------------------------


def safe_int(data: dict[str, object], key: str, default: int = 0) -> int:
    """Safely extract an integer from a dict with heterogeneous values.

    Handles str, int, float, and None values without raising.

    Args:
        data: Dictionary with mixed-type values.
        key: Key to extract.
        default: Fallback value if key is missing or conversion fails.

    Returns:
        Integer value, or default on any failure.
    """
    try:
        return int(str(data.get(key, default)))
    except (ValueError, TypeError):
        return default


def safe_float(data: dict[str, object], key: str, default: float = 0.0) -> float:
    """Safely extract a float from a dict with heterogeneous values.

    Args:
        data: Dictionary with mixed-type values.
        key: Key to extract.
        default: Fallback value if key is missing or conversion fails.

    Returns:
        Float value, or default on any failure.
    """
    try:
        return float(str(data.get(key, default)))
    except (ValueError, TypeError):
        return default


def safe_str(data: dict[str, object], key: str, default: str = "") -> str:
    """Safely extract a string from a dict with heterogeneous values.

    Args:
        data: Dictionary with mixed-type values.
        key: Key to extract.
        default: Fallback value if key is missing.

    Returns:
        String value, or default if missing.
    """
    val = data.get(key, default)
    return str(val) if val is not None else default


# ---------------------------------------------------------------------------
# Entry file iteration
# ---------------------------------------------------------------------------


def iter_yaml_entry_files(entries_dir: Path) -> Iterator[Path]:
    """Iterate over YAML entry files in a directory, skipping index.yaml.

    This is the canonical way to iterate learning entries. Yields paths
    sorted by name for deterministic ordering.

    Args:
        entries_dir: Directory containing YAML entry files.

    Yields:
        Path objects for each .yaml file (excluding index.yaml).
    """
    if not entries_dir.is_dir():
        return
    for yaml_file in sorted(entries_dir.glob("*.yaml")):
        if yaml_file.name == "index.yaml":
            continue
        yield yaml_file


def is_active_entry(data: dict[str, object]) -> bool:
    """Check if a learning entry dict has active status.

    The default status is 'active' for entries that don't have an
    explicit status field.

    Args:
        data: Entry dict loaded from YAML.

    Returns:
        True if the entry is active.
    """
    return str(data.get("status", "active")) == "active"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_project_config(trw_dir: Path) -> "TRWConfig":
    """Load a target project's config.yaml into a TRWConfig instance.

    This is the canonical way to load project config. Consolidates the
    duplicate implementations that existed in audit.py and export.py.

    Args:
        trw_dir: Path to the .trw directory.

    Returns:
        TRWConfig instance (defaults if config.yaml is missing).
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader

    config_path = trw_dir / "config.yaml"
    if config_path.exists():
        reader = FileStateReader()
        data = reader.read_yaml(config_path)
        return TRWConfig(**{k: v for k, v in data.items() if v is not None})  # type: ignore[arg-type]
    return TRWConfig()
