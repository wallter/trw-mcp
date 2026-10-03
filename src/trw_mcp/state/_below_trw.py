"""Create a directory only below a ``.trw`` that already exists (UNINSTALL-DISTILL-RACE).

Belongs to the TRW background writers (the post-commit worker, the detached sidecar rebuild, the opt-in incremental
run). Uninstall removes ``.trw``; a writer that starts afterwards used to ``mkdir(parents=True)`` its lock or stamp
directory and so recreate ``.trw`` from nothing. :func:`ensure_dir_below_trw` creates only the missing levels BELOW the
``.trw`` directory, one non-recursive ``mkdir`` at a time, so a ``.trw`` removed at any moment makes the next level
fail with ``FileNotFoundError`` instead of being recreated. ``False`` means "uninstalled": the caller stops.
"""

from __future__ import annotations

from pathlib import Path

TRW_DIR_NAME = ".trw"


def ensure_dir_below_trw(path: Path, *, root: Path | None = None) -> bool:
    """Create *path* (and its missing parents below the root) when the root ``.trw`` exists; else do nothing.

    *root* defaults to the nearest ancestor of *path* named ``.trw``. A path with no such ancestor is not TRW-owned
    state and is created as before. Returns ``False`` when the root is gone, including when it vanishes mid-call.
    """
    base = root if root is not None else next((p for p in (path, *path.parents) if p.name == TRW_DIR_NAME), None)
    if base is None:
        path.mkdir(parents=True, exist_ok=True)
        return True
    if not base.is_dir():
        return False
    current = base
    for part in path.relative_to(base).parts:
        current = current / part
        try:
            current.mkdir()
        except FileExistsError:  # trw-fail-silent-allow: the level is already there, which is the goal
            continue
        except (
            FileNotFoundError
        ):  # trw-fail-silent-allow: the root went away under us: report uninstalled, create nothing
            return False
    return True
