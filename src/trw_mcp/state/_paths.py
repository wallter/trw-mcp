"""Shared path resolution — single source of truth for project root and .trw dir.

All modules that need to resolve TRW_PROJECT_ROOT or the .trw directory
MUST use these functions instead of inline resolution logic.
"""

from __future__ import annotations

import os
from pathlib import Path

from trw_mcp.models.config import TRWConfig

_config = TRWConfig()


def resolve_project_root() -> Path:
    """Resolve the project root from environment or CWD.

    Resolution order:
    1. ``TRW_PROJECT_ROOT`` environment variable (if set)
    2. Current working directory

    Returns:
        Absolute path to the project root directory.
    """
    env_root = os.environ.get("TRW_PROJECT_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path.cwd().resolve()


def resolve_trw_dir() -> Path:
    """Resolve the .trw directory path.

    Returns:
        Absolute path to the .trw directory (project_root / config.trw_dir).
    """
    return resolve_project_root() / _config.trw_dir
