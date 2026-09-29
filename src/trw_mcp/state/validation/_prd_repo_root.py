"""Resolve the root a PRD's cited paths are grounded against (PRD-CORE-317).

CONTAINMENT (can this caller read ``prd_path`` at all) is UNCHANGED by this
PRD: it stays exactly the server's configured project root
(``resolve_project_root()``), with no git lookup and no caller-supplied
override. Three review rounds each found a different way a git-aware
containment check could be widened past the operator's configured boundary
(a caller-controlled override; an arbitrary unrelated git repo; a configured
root that is itself a subdirectory of a larger enclosing repo). The design
that closes all three BY CONSTRUCTION, rather than patching each one, is to
never let containment consult git or a caller at all --- see
``tools/_prd_validate_tool.py::run_prd_validate``.

GROUNDING (where a PRD's CITED paths are searched) is the only thing this
PRD changes, via :func:`resolve_prd_grounding_root`: prefer the git toplevel
containing the PRD file over the configured project root, but ONLY when
that toplevel already resolves inside the configured root (a plain
``Path.is_relative_to`` check, not a git-identity comparison). A scratchpad
worktree under ``.claude/worktrees/<name>`` or ``.trw/worktrees/<name>``
already lives inside the main checkout, so this covers the R8 swarm's
actual layout without ever widening past the configured root. An external
git repo, a git repo outside the configured root, or no git repository at
all, all degrade to the configured root unchanged.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

__all__ = ["resolve_prd_grounding_root"]

_GIT_TIMEOUT_SECONDS = 5.0


def _git_toplevel(prd_dir: Path, *, timeout: float = _GIT_TIMEOUT_SECONDS) -> Path | None:
    """Best-effort ``git rev-parse --show-toplevel`` for *prd_dir*; ``None`` on any failure."""
    try:
        completed = subprocess.run(  # noqa: S603 — shell=False (default); cmd is a git read-only command with validated static args
            ["git", "-C", str(prd_dir), "rev-parse", "--show-toplevel"],  # noqa: S607 — git is a well-known VCS tool
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (
        OSError,
        subprocess.SubprocessError,
    ):  # trw-fail-silent-allow: best-effort git lookup; a missing git binary, a timeout, or any other launch failure degrades to the configured project root
        return None
    if completed.returncode != 0:
        return None
    candidate = completed.stdout.strip()
    if not candidate:
        return None
    return Path(candidate).resolve()


def resolve_prd_grounding_root(prd_path: Path, *, project_root: Path, deadline: float | None = None) -> Path:
    """Resolve the root a PRD's cited paths should be grounded against.

    Never widens past *project_root*: the git toplevel containing *prd_path*
    is preferred only when it resolves to *project_root* itself or a
    subdirectory of it (e.g. a linked worktree nested under
    ``.claude/worktrees/`` or ``.trw/worktrees/``); anything else --- an
    external git repo, one outside *project_root*, or none at all --- falls
    back to *project_root* unchanged. This is a plain path-containment
    check, never a git-identity comparison, so it cannot itself become a
    trust boundary: the widest it can ever resolve to is *project_root*.

    Args:
        prd_path: the PRD markdown file (need not exist yet).
        project_root: the server's configured, already-verified project
            root --- the same value containment used to admit *prd_path*.
        deadline: optional ``time.monotonic()`` budget deadline (PRD-FIX-112).
            The git lookup's timeout never outlasts it, and an already-spent
            budget skips the lookup and returns *project_root* --- every
            repo-grounded check is then skipped as partial anyway.
    """
    timeout = _GIT_TIMEOUT_SECONDS
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            return project_root
    git_root = _git_toplevel(prd_path.parent, timeout=timeout)
    if git_root is not None and git_root.is_relative_to(project_root):
        return git_root
    return project_root
