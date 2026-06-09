"""Machine-local (user-space) memory path resolution -- PRD-CORE-185 FR01.

The user-space memory tier lives OUTSIDE any project's ``.trw`` directory so a
single machine-local store is shared by every repo on the box. This module is
the single source of truth for resolving that directory.

Resolution precedence (highest wins), per PRD-CORE-185-FR01 (D1):
  1. ``TRW_USER_DIR`` env var      -> ``<TRW_USER_DIR>/memory``
  2. ``XDG_DATA_HOME`` env var     -> ``<XDG_DATA_HOME>/trw/memory``
  3. fallback                      -> ``<home>/.trw/memory``

The resolver is cross-platform: it relies only on ``os.environ`` and
``Path.home()`` and introduces no ``fcntl``-only locking path (NFR03). It
creates the directory lazily (``mkdir(parents=True, exist_ok=True)``) and never
raises on a missing directory, mirroring the project path's lazy-create
behavior in ``_memory_connection.get_backend``.

This is a focused sibling of ``_paths.py`` (which is at the 350 effective-LOC
gate); all user-space path logic lives here.
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Subdirectory (under the resolved user base) that holds the memory store,
# mirroring the project layout ``<trw_dir>/memory/memory.db``.
_MEMORY_SUBDIR = "memory"
# XDG application directory under ``$XDG_DATA_HOME``.
_XDG_APP_DIR = "trw"
# Home fallback base directory name.
_HOME_TRW_DIR = ".trw"


def resolve_user_memory_dir(*, create: bool = True) -> Path:
    """Resolve the machine-local user-space memory directory.

    Precedence: ``TRW_USER_DIR`` > ``$XDG_DATA_HOME`` > ``~/.trw``.

    Args:
        create: When True (default) ensure the directory exists
            (``mkdir(parents=True, exist_ok=True)``). When False, resolve the
            path without touching the filesystem (used by presence probes).

    Returns:
        Absolute path to the user-space ``memory`` directory. The user-space
        ``memory.db`` lives at ``<returned>/memory.db``.
    """
    user_dir = os.environ.get("TRW_USER_DIR")
    if user_dir:
        base = Path(user_dir) / _MEMORY_SUBDIR
        source = "trw_user_dir"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            base = Path(xdg) / _XDG_APP_DIR / _MEMORY_SUBDIR
            source = "xdg_data_home"
        else:
            base = Path.home() / _HOME_TRW_DIR / _MEMORY_SUBDIR
            source = "home_fallback"

    resolved = base.resolve()
    if create:
        resolved.mkdir(parents=True, exist_ok=True)
    logger.debug("user_memory_dir_resolved", path=str(resolved), source=source, created=create)
    return resolved
