"""The ONE resolver of the TRW project a shared-server verb or proxy acts on (PROXY-PROJECT-ROOT).

Belongs to the shared-server package; used by ``_cli`` (swap, status, env, drain) and ``_proxy`` (the client
entry), so the record a swap writes is the record the proxy's server is found through. Two resolvers once
gave one project two roots, and every canary swap went to a record no server used.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_GIT_TIMEOUT_SECONDS = 5.0


def trw_project_root() -> Path | None:
    """The TRW project for this process, or ``None`` when it is not inside one.

    ``resolve_project_root`` (install binding > TRW_PROJECT_ROOT > CWD), mapped to its git toplevel: a
    subdirectory resolves to its project and a linked worktree to its OWN toplevel. With no usable git (absent,
    hung, or not a repository) the parents are walked to the first TRW project, stopping at $HOME or the root. It must carry ``<trw_dir>/config.yaml`` (init-project
    always writes it; a stray ``.trw`` from a hook leak does not count), read with the root's own config.
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
    # No git (INC-126 e): walk parents to the FIRST TRW project, never above $HOME nor past the filesystem root.
    home = Path.home().resolve()
    for candidate in (start, *start.parents):
        if (found := _trw_project_at(candidate)) is not None:
            return found
        if candidate == home or candidate.parent == candidate:
            break
    return None


def _trw_project_at(root: Path) -> Path | None:
    """*root* when it carries ``<trw_dir>/config.yaml`` (read with the root's own config), else ``None``."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._project_root_binding import project_bound

    with project_bound(root):
        marker = root / str(get_config().trw_dir) / "config.yaml"
    return root if marker.is_file() else None
