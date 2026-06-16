"""File/dir permission hardening (PRD-QUAL-110-FR02).

Belongs to the ``_paths.py`` facade. Re-exported there for back-compat so
callers keep a single import point.

``.trw`` state/secret directories are created mode ``0700`` and secret-bearing
files mode ``0600``, consistent with the existing ``pins.json`` 0600 hardening
(``_pin_store.py:390``). Best-effort: on non-POSIX platforms (which ignore
POSIX mode bits) ``chmod`` raises and we degrade to a WARNING rather than
failing the artifact-creation operation (NFR02).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

_SECRET_DIR_MODE = 0o700
_SECRET_FILE_MODE = 0o600


def _runtime_logger() -> Any:
    """Return a fresh logger so structlog test capture sees late-bound events."""
    return structlog.get_logger(__name__)


def harden_dir_mode(path: Path, *, create: bool = False) -> None:
    """Set *path* to mode 0700, best-effort (PRD-QUAL-110-FR02).

    When *create* is True the directory (and parents) is created first.
    A chmod failure (e.g. Windows, or an exotic filesystem) logs
    ``path_chmod_failed`` at WARNING and returns — never raises — so artifact
    creation is never blocked by permission tightening (NFR02).
    """
    if create:
        path.mkdir(parents=True, exist_ok=True)
    _chmod_best_effort(path, _SECRET_DIR_MODE)


# Standard top-level subdirectories created under ``.trw/`` that hold session
# state, learnings, logs, and runtime artifacts. These are hardened to 0700
# alongside the ``.trw`` root so a fresh install matches the README security
# claim (PRD-QUAL-110-FR02 follow-up). The list is intentionally a superset of
# the config-default dir names — creating one that does not exist yet is a
# best-effort no-op-friendly mkdir, never an error.
_TRW_STATE_SUBDIRS: tuple[str, ...] = (
    "runs",
    "learnings",
    "logs",
    "runtime",
    "memory",
    "context",
    "reflections",
    "knowledge",
    "security",
)


def harden_trw_tree(trw_dir: Path, *, create_subdirs: bool = False) -> None:
    """Harden the ``.trw`` root and its standard state subdirs to 0700.

    PRD-QUAL-110-FR02 follow-up: the original FR02 hardened only
    ``.trw/memory`` + ``memory.db``. The ``.trw`` root and its top-level
    state subdirectories (``runs/``, ``learnings/``, ``logs/`` …) were
    created at the default umask, which contradicted the README security
    claim that "``.trw/`` dirs are 0700". This walks the well-known tree and
    tightens every directory that EXISTS to 0700, best-effort.

    Only directories that already exist are hardened unless *create_subdirs*
    is True, in which case each standard subdir is created first. A missing
    ``.trw`` root is a no-op (nothing to harden yet). chmod failures degrade
    to a WARNING per :func:`_chmod_best_effort` and never raise (NFR02).
    """
    if not trw_dir.exists() and not create_subdirs:
        return
    harden_dir_mode(trw_dir, create=create_subdirs)
    for name in _TRW_STATE_SUBDIRS:
        sub = trw_dir / name
        if create_subdirs:
            harden_dir_mode(sub, create=True)
        elif sub.is_dir():
            harden_dir_mode(sub, create=False)


def harden_secret_file_mode(path: Path) -> None:
    """Set a secret-bearing *path* to mode 0600, best-effort (PRD-QUAL-110-FR02).

    A missing file is a no-op. A chmod failure logs ``path_chmod_failed`` at
    WARNING and returns without raising (mirrors pins.json behavior).
    """
    if not path.exists():
        return
    _chmod_best_effort(path, _SECRET_FILE_MODE)


def _chmod_best_effort(path: Path, mode: int) -> None:
    """chmod *path* to *mode*, swallowing + WARN-logging any OSError."""
    try:
        os.chmod(path, mode)
    except OSError as exc:
        _runtime_logger().warning(
            "path_chmod_failed",
            path=str(path),
            mode=oct(mode),
            error=type(exc).__name__,
        )
