"""The Python interpreter TRW's shell hooks run their maintenance with (PRD-FIX-156 s2).

Responsibility: record, at install and update time, the interpreter trw-mcp is
installed into, so the hooks that start Python from a shell (the git post-commit
maintenance, the edit-hint hooks) run code that can actually import it.

Why: those hooks read ``.trw/channels/cc03-python.txt`` and otherwise fall back
to ``python3`` on PATH. Nothing ever wrote the file (learning L-7zca), and in a
user project PATH's ``python3`` is not the venv trw-mcp lives in, so the
post-commit sweep ran under an interpreter that could not do the work and
reported nothing.

Interface: :func:`record_hook_interpreter`, called by
``install_git_post_commit_hook``, which both ``init-project`` and
``update-project`` go through. The hook side of the contract (a loud line on
stderr and a ``python_unavailable=1`` hook-log event when the recorded
interpreter cannot import ``trw_mcp``) lives in
``data/git_hooks/trw-post-commit.sh``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import structlog

from trw_mcp.bootstrap._user_file_edit import atomic_write_text, guard_refusal

logger = structlog.get_logger(__name__)

#: Shared by the post-commit hook and every edit-hint library's ``_get_python_path``.
HOOK_INTERPRETER_REL = Path(".trw") / "channels" / "cc03-python.txt"


def record_hook_interpreter(target_dir: Path, *, dry_run: bool = False, executable: str | None = None) -> Path | None:
    """Record the running interpreter for *target_dir*'s hooks.

    Args:
        target_dir: Project root.
        dry_run: Report the path without writing it.
        executable: Interpreter to record; defaults to ``sys.executable``, the
            one running trw-mcp and therefore able to import it.

    Returns:
        The file path when it was (or, on a dry run, would be) written; ``None``
        when it already names this interpreter.

    Raises:
        ValueError: When there is no absolute interpreter path to record (an
            embedded Python reports an empty ``sys.executable``).
        OSError: When the file cannot be written, or when it or a directory
            between it and *target_dir* is a symlink (nothing is read or
            written through one). Callers on a bootstrap path report it as a
            warning; they never abort the install.
    """
    interpreter = executable if executable is not None else sys.executable
    if not interpreter or not Path(interpreter).is_absolute():
        raise ValueError(f"no absolute interpreter path to record for the hooks (got {interpreter!r})")
    path = target_dir / HOOK_INTERPRETER_REL
    _refuse_unsafe(path, target_dir)  # a checkout may ship .trw/channels as a link out
    if path.is_file() and path.read_text(encoding="utf-8").strip() == interpreter:
        return None
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        _refuse_unsafe(path, target_dir)  # mkdir may have walked a component planted meanwhile
        atomic_write_text(path, interpreter + "\n")
        logger.info("hook_interpreter_recorded", path=str(path), interpreter=interpreter)
    return path


def _refuse_unsafe(path: Path, root: Path) -> None:
    refusal = guard_refusal(path, root)
    if refusal is not None:
        raise OSError(f"{path}: {refusal}")


__all__ = ["HOOK_INTERPRETER_REL", "record_hook_interpreter"]
