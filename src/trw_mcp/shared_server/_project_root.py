"""The ONE resolver of the TRW project a shared-server verb or proxy acts on (PROXY-PROJECT-ROOT).

Belongs to the shared-server package; used by ``_cli`` (swap, status, env, drain) and ``_proxy`` (the client
entry), so the record a swap writes is the record the proxy's server is found through. Two resolvers once
gave one project two roots, and every canary swap went to a record no server used.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

_GIT_TIMEOUT_SECONDS = 5.0


def trw_project_root() -> Path | None:
    """The TRW project for this process, or ``None`` when it is not inside one.

    ``resolve_project_root`` (install binding > TRW_PROJECT_ROOT > CWD), mapped to its git toplevel: a
    subdirectory resolves to its project and a linked worktree to its OWN toplevel. With no usable git (absent,
    hung, or not a repository) the parents of a start under $HOME are walked to the first TRW project, never above
    $HOME. A project carries ``<trw_dir>/config.yaml`` in a real (unlinked) directory: init-project always writes
    it, and a stray ``.trw`` from a hook leak does not count. It is read with the root's own config.
    """
    from trw_mcp.state._paths import resolve_project_root

    start = resolve_project_root()
    try:
        top = subprocess.run(  # noqa: S603 -- fixed argv
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        toplevel = top.stdout.strip() if top.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):  # trw-fail-silent-allow: no usable git keeps the resolved root
        toplevel = ""
    if toplevel:
        return _trw_project_at(Path(toplevel).resolve())
    # No git (INC-126 e): walk parents to the FIRST TRW project, only from a start under a usable (absolute) $HOME
    # and never above it. Outside HOME, or with no usable HOME, only the start itself can be the project.
    home = _walk_ceiling()
    if home is None or not start.is_relative_to(home):
        return _trw_project_at(start)
    for candidate in (start, *start.parents):
        if (found := _trw_project_at(candidate)) is not None:
            return found
        if candidate == home:
            break
    return None


def _walk_ceiling() -> Path | None:
    """The resolved $HOME the walk stops at, or ``None`` (no walk) when HOME is unset, empty or relative."""
    raw = os.environ.get("HOME", "")
    return Path(raw).resolve() if raw and os.path.isabs(raw) else None


def _real_dir(path: Path) -> bool:
    """A directory that is not a symlink (a linked ``.trw`` would borrow another project's config and records)."""
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:  # trw-fail-silent-allow: absent or unreadable is not a project marker
        return False


def _trw_project_at(root: Path) -> Path | None:
    """*root* when it carries ``<trw_dir>/config.yaml`` in a real directory (read with the root's own config)."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._project_root_binding import project_bound

    default_dir = root / ".trw"  # the loader reads <root>/.trw/config.yaml: refuse a link BEFORE loading through it
    if os.path.lexists(default_dir) and not _real_dir(default_dir):
        return None
    with project_bound(root):
        trw_dir = root / str(get_config().trw_dir)
    return root if _real_dir(trw_dir) and (trw_dir / "config.yaml").is_file() else None
