"""Git hook installation (PRD-CORE-231 FR01/FR02).

``_install_hooks`` copies bundled scripts into ``.claude/hooks/``, which is the
Claude Code TOOL-LIFECYCLE surface — it has no ``post-commit`` event, so a git
hook placed there never fires. This module installs the real thing:
``.git/hooks/post-commit``.

Two invariants:

* **Never clobber a user's hook.** An existing foreign ``post-commit`` is
  preserved and a guarded TRW dispatch block is APPENDED. Re-running replaces
  only the block between the markers, so install is idempotent.
* **Never break a commit.** The dispatch line is ``|| true``-guarded and the
  dispatched script exits 0 on every path, so a broken TRW install cannot make
  ``git commit`` fail.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"

#: Bundled source + its installed location inside the project.
BUNDLED_HOOK = _DATA_DIR / "git_hooks" / "trw-post-commit.sh"
INSTALLED_HOOK_REL = Path(".trw") / "hooks" / "trw-post-commit.sh"

MARKER_START = "# >>> trw post-commit (managed by trw-mcp) >>>"
MARKER_END = "# <<< trw post-commit (managed by trw-mcp) <<<"

_SHEBANG = "#!/bin/sh"

_DISPATCH_BODY = f"""{MARKER_START}
# PRD-CORE-231: re-seed the sha-keyed T2 hint sidecar and re-verify assertion/
# anchor staleness after each commit. Fail-open by construction — the dispatched
# script exits 0 on every path and this line is guarded, so a broken or absent
# TRW install can never make `git commit` fail.
if [ -x "$(git rev-parse --show-toplevel 2>/dev/null)/{INSTALLED_HOOK_REL.as_posix()}" ]; then
    "$(git rev-parse --show-toplevel)/{INSTALLED_HOOK_REL.as_posix()}" || true
fi
{MARKER_END}"""


def _make_executable(path: Path) -> None:
    """Add the executable bit for user/group/other (pip strips it)."""
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def render_post_commit_shim(existing: str | None) -> str:
    """Return the full ``.git/hooks/post-commit`` content.

    Args:
        existing: Current hook content, or ``None`` when no hook exists.

    Returns:
        A fresh minimal shim when there is no prior hook; otherwise the prior
        content with the TRW block replaced in place (idempotent) or appended
        (chained after the user's own logic).
    """
    if existing is None or not existing.strip():
        return f"{_SHEBANG}\n#\n# Created by trw-mcp. Non-TRW content is preserved on update.\n\n{_DISPATCH_BODY}\n"

    if MARKER_START in existing and MARKER_END in existing:
        start = existing.index(MARKER_START)
        end = existing.index(MARKER_END) + len(MARKER_END)
        return existing[:start] + _DISPATCH_BODY + existing[end:]

    # Foreign hook: chain, never clobber.
    return f"{existing.rstrip()}\n\n{_DISPATCH_BODY}\n"


def install_git_post_commit_hook(
    target_dir: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """Install the TRW ``post-commit`` hook into *target_dir*.

    Copies the bundled script to ``.trw/hooks/`` and writes (or updates) the
    guarded dispatch block in ``.git/hooks/post-commit``.

    Args:
        target_dir: Repository root.
        force: Overwrite the installed script even when it already exists.
        dry_run: Report what would change without touching the filesystem.

    Returns:
        Dict with ``created``/``updated``/``skipped``/``errors`` lists. Fail-open:
        problems are reported, never raised — a hook-install failure must not
        abort bootstrap.
    """
    result: dict[str, list[str]] = {"created": [], "updated": [], "skipped": [], "errors": []}

    git_dir = target_dir / ".git"
    if not git_dir.exists():
        result["skipped"].append(f"{git_dir} not found (not a git repository)")
        return result
    if not BUNDLED_HOOK.is_file():
        result["errors"].append(f"bundled hook missing: {BUNDLED_HOOK}")
        return result

    script_dest = target_dir / INSTALLED_HOOK_REL
    try:
        if script_dest.exists() and not force:
            result["skipped"].append(str(script_dest))
        elif dry_run:
            result["created"].append(str(script_dest))
        else:
            script_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(BUNDLED_HOOK, script_dest)
            _make_executable(script_dest)
            result["created"].append(str(script_dest))
    except OSError as exc:
        result["errors"].append(f"Failed to install {script_dest}: {exc}")
        return result

    # ``core.hooksPath`` redirects git away from .git/hooks; honour it so the
    # shim lands where git will actually look for it.
    # dry_run must perform NO subprocess work; the reported path is advisory,
    # so the core.hooksPath probe is skipped rather than run for a preview.
    hooks_dir = _resolve_hooks_dir(target_dir, git_dir, probe=not dry_run)
    hook_path = hooks_dir / "post-commit"
    try:
        existing = hook_path.read_text(encoding="utf-8") if hook_path.is_file() else None
        rendered = render_post_commit_shim(existing)
        if existing == rendered:
            result["skipped"].append(str(hook_path))
            return result
        if dry_run:
            result["updated" if existing else "created"].append(str(hook_path))
            return result
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(rendered, encoding="utf-8")
        _make_executable(hook_path)
        result["updated" if existing else "created"].append(str(hook_path))
        logger.info("git_post_commit_hook_installed", path=str(hook_path), chained=bool(existing))
    except OSError as exc:
        result["errors"].append(f"Failed to write {hook_path}: {exc}")

    return result


def _resolve_hooks_dir(target_dir: Path, git_dir: Path, *, probe: bool = True) -> Path:
    """Return the directory git reads hooks from, honouring ``core.hooksPath``.

    ``probe=False`` skips the ``git config`` subprocess and falls back to the
    default hooks directory — used on the dry-run path, which must not shell out.
    """
    configured = os.environ.get("GIT_CONFIG_HOOKS_PATH", "").strip()
    if not configured and probe:
        try:
            import subprocess

            # S607: `git` is resolved via PATH by design — the same contract every
            # other git call in bootstrap uses; argv is fixed, no shell, no input.
            completed = subprocess.run(
                ("git", "config", "--get", "core.hooksPath"),  # noqa: S607
                cwd=target_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            configured = completed.stdout.strip() if completed.returncode == 0 else ""
        except Exception:  # justified: fail-open, default to .git/hooks
            configured = ""
    if configured:
        candidate = Path(configured)
        return candidate if candidate.is_absolute() else target_dir / candidate
    # A worktree/submodule .git is a FILE pointing at the real dir.
    return (git_dir if git_dir.is_dir() else target_dir) / "hooks"


__all__ = [
    "BUNDLED_HOOK",
    "INSTALLED_HOOK_REL",
    "MARKER_END",
    "MARKER_START",
    "install_git_post_commit_hook",
    "render_post_commit_shim",
]
