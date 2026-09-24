"""Server-observed Git identity for build receipts; dirty trees never bind to HEAD."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_GIT_SHA = re.compile(r"[0-9a-f]{40,64}\Z")


def clean_git_sha(project_root: Path) -> str | None:
    """Return a clean HEAD commit or no binding; never trust a caller SHA."""
    try:
        head = subprocess.run(  # noqa: S603 - fixed argv, no shell or caller-supplied command
            ["git", "-C", str(project_root), "rev-parse", "--verify", "HEAD^{commit}"],  # noqa: S607 - git is resolved from PATH
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        status = subprocess.run(  # noqa: S603 - fixed argv, no shell or caller-supplied command
            ["git", "-C", str(project_root), "status", "--porcelain", "--untracked-files=all"],  # noqa: S607 - git is resolved from PATH
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (
        OSError,
        subprocess.TimeoutExpired,
    ):  # trw-fail-silent-allow: unavailable Git is an explicit unbound receipt, not a successful merge gate
        return None
    sha = head.stdout.strip()
    if head.returncode or status.returncode or status.stdout or _GIT_SHA.fullmatch(sha) is None:
        return None
    return sha
