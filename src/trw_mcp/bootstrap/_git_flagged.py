"""Flagged managed files an update must not overwrite: ``--skip-worktree`` / ``--assume-unchanged`` entries whose bytes were edited.

Belongs to the ``_version_manifest`` facade (``git_dirty_paths`` adds this set to what ``git status`` reports). ``git status`` is silent about a flagged file
whatever its content, so the canon copies (FRAMEWORK.md, AARE-F-FRAMEWORK.md), which are kept through an update only because git lists an edited one as dirty,
were overwritten. The comparison is by bytes, as the retire fix does (UPDATE-PRESERVE-SKIPWT).
"""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def possibly_edited_paths(target_dir: Path, pathspecs: list[str]) -> set[str]:
    """The fail-safe answer when git cannot say which files hold local edits: every regular file under *pathspecs* (a directory is walked, ``.git`` skipped).

    Counting a file as possibly edited only makes ``preserve_uncommitted_changes`` look at it: that guard puts back a file an update changed only when
    TRW's own record does not cover its previous bytes, so over-approximating costs nothing for a file the user never touched.
    """
    found: set[str] = set()
    for spec in pathspecs:
        root = target_dir / spec
        candidates = [root] if root.is_file() else sorted(root.rglob("*")) if root.is_dir() else []
        found.update(
            path.relative_to(target_dir).as_posix()
            for path in candidates
            if path.is_file() and ".git" not in path.relative_to(target_dir).parts
        )
    return found


def _git_restores_bytes(target_dir: Path, rel: str, env: dict[str, str]) -> bytes | None:
    """What git would write for *rel*: the index blob through the smudge filter, or ``None`` when git cannot say."""
    import subprocess

    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "--literal-pathspecs", "-C", str(target_dir), "cat-file", "--filters", f":./{rel}"],  # noqa: S607
            capture_output=True,
            timeout=5,
            env=env,
            check=False,
        )
    except (
        OSError,
        subprocess.TimeoutExpired,
    ):  # trw-fail-silent-allow: git cannot answer, the caller treats the file as edited
        return None
    return proc.stdout if proc.returncode == 0 else None


def flagged_edited_paths(target_dir: Path, pathspecs: list[str], env: dict[str, str]) -> set[str]:
    """Paths flagged ``--skip-worktree`` / ``--assume-unchanged`` whose bytes differ from what git would restore.

    ``git status`` is silent about such a file whatever its content, so an edit to a flagged managed file (FRAMEWORK.md and the other
    canon copies are kept only because git lists them dirty) was overwritten by the update. The comparison is by bytes, as the retire fix
    does; a file git cannot compare counts as edited (fail safe). A flagged file that is absent or not a regular file holds nothing of
    the user's, and is left out.
    """
    import subprocess

    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(target_dir), "ls-files", "-v", "-z", "--", *pathspecs],  # noqa: S607
            capture_output=True,
            timeout=5,
            env=env,
            check=False,
        )
    except (
        OSError,
        subprocess.TimeoutExpired,
    ):  # trw-fail-silent-allow: not silent: no flag information, so every managed file is reported possibly edited (fail safe)
        logger.warning("git_flagged_check_unavailable", target=str(target_dir), exc_info=True)
        return possibly_edited_paths(target_dir, pathspecs)  # cannot tell which files are flagged: protect them all
    if proc.returncode != 0:
        logger.warning("git_flagged_check_failed", target=str(target_dir), returncode=proc.returncode)
        return possibly_edited_paths(target_dir, pathspecs)
    edited: set[str] = set()
    for entry in proc.stdout.split(b"\0"):
        if len(entry) < 3:
            continue
        tag, rel = entry[:1].decode("ascii", "replace"), entry[2:].decode("utf-8", "surrogateescape")
        if not (tag == "S" or tag.islower()):  # H cached; S skip-worktree; lowercase = assume-unchanged (s: both)
            continue
        path = target_dir / rel
        if path.is_symlink() or not path.is_file():
            continue
        held = _git_restores_bytes(target_dir, rel, env)
        try:
            on_disk: bytes | None = path.read_bytes()
        except (
            OSError
        ):  # trw-fail-silent-allow: a file that cannot be read cannot be shown unedited, so it is protected
            on_disk = None
        if held is None or held != on_disk:
            edited.add(rel)
    return edited
