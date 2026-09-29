"""Doctor row for the interpreter the hooks start (PRD-FIX-155).

Belongs to the ``_subcommands_doctor.py`` facade (kept out of that file for the
eLOC gate).

The hint and post-commit hooks start Python through ``_get_python_path``. Before
PRD-FIX-155 nothing wrote the file that function reads first, so every hook fell
back to bare ``python3``, which usually cannot import trw_mcp. In the TRW repo,
every recorded edit (1,876 on 2026-09-25) got the fallback beacon, and no error
was visible anywhere. This row runs the bundled ``_get_python_path`` (the bundled lib only READS the
pointer; it executes nothing the checkout names), so no part of the resolution
is reimplemented here. It then compares the named interpreter with the one
running trw-mcp, which imports trw_mcp by construction and is exactly what
``init-project``/``update-project`` record. It never starts the named
interpreter: ``.trw/channels/cc03-python.txt`` is checkout-controlled, and a
doctor run in a fresh clone must not execute a program the clone chose
(PRD-FIX-156 boundary; review dist-int-merge-2 r1). A deployed hook whose copy
of the function differs from the bundled one also fails the row, because it
still resolves the old way. Deployed hooks are compared, never sourced.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

__all__ = ["hook_python_row"]

_DEPLOYED = (
    ".claude/hooks/lib-distill-hint.sh",
    ".cursor/hooks/lib-distill-hint.sh",
    ".github/hooks/lib-copilot-distill-hint.sh",
    ".trw/hooks/trw-post-commit.sh",
)
_BUNDLED_LIB = Path(__file__).resolve().parent.parent / "data" / "claude_code" / "hooks" / "lib-distill-hint.sh"
_FUNCTION_RE = re.compile(r"^_get_python_path\(\) \{\n.*?^\}\n", re.DOTALL | re.MULTILINE)
_REMEDY = "Remedy: trw-mcp update-project (outside a git repository: trw-mcp init-project)."
# An interpreter entry point, never a sibling script that merely starts with "python" (PRD-CORE-336-FR05,
# KI dist-doctor-env: "python-evil" in the same directory used to false-PASS on the startswith("python") check).
_ENTRY_POINT_RE = re.compile(r"^python3?(\.\d+)?$")


def _resolver(path: Path) -> str | None:
    try:
        match = _FUNCTION_RE.search(path.read_text(encoding="utf-8"))
    # trw-fail-silent-allow: None never equals the bundled resolver, so an unreadable hook reads as stale and FAILs
    except (OSError, UnicodeDecodeError):
        return None
    return match.group(0) if match else None


def _run(argv: list[str], target: Path, timeout: float) -> tuple[int, str, str]:
    env = {**os.environ, "TRW_PROJECT_DIR": str(target)}
    try:
        done = subprocess.run(  # noqa: S603 -- the bundled lib via sh, then the interpreter it names
            argv, cwd=target, env=env, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"
    return done.returncode, done.stdout.strip(), done.stderr.strip()


def _same_environment(py: str) -> bool:
    """Whether the interpreter *py* names is the environment running trw-mcp, judged WITHOUT running it.

    A bare name (the resolver's ``python3`` fallback) is located on PATH with
    ``shutil.which``. Two interpreters are the same environment when they sit in
    the same directory, compared unresolved: Python finds a venv through the
    ``pyvenv.cfg`` next to the path it was STARTED from, so a symlink elsewhere to
    a venv's python starts the base interpreter, which cannot import trw_mcp.
    Resolving symlinks would make that base interpreter look identical.

    Same directory is necessary but not sufficient: a sibling script whose name merely
    starts with "python" (e.g. "python-evil") is not an interpreter entry point, so the
    basename must additionally match one this environment actually publishes
    (``python``, ``python3``, ``python3.NN``) (PRD-CORE-336-FR05, KI dist-doctor-env).
    """
    located = py if os.sep in py or (os.altsep and os.altsep in py) else shutil.which(py)
    if not located:
        return False
    candidate, running = os.path.abspath(located), os.path.abspath(sys.executable)
    if candidate == running:
        return True
    return os.path.dirname(candidate) == os.path.dirname(running) and bool(
        _ENTRY_POINT_RE.fullmatch(os.path.basename(candidate))
    )


def hook_python_row(target: Path) -> tuple[Literal["PASS", "FAIL", "SKIP"], str]:
    """FAIL when the hooks' interpreter cannot ``import trw_mcp``, or a deployed hook resolves the old way."""
    sh = shutil.which("sh")
    if not (target / ".trw").is_dir() or sh is None:
        return "SKIP", "no .trw/ directory or no POSIX sh; no hook starts Python here."
    bundled = _resolver(_BUNDLED_LIB)
    stale = [rel for rel in _DEPLOYED if (target / rel).is_file() and _resolver(target / rel) != bundled]
    problems = [f"{', '.join(stale)} still resolve(s) the interpreter the old way"] if stale else []
    _rc, py, _err = _run([sh, "-c", '. "$0" && _get_python_path "$1"', str(_BUNDLED_LIB), str(target)], target, 10)
    if not py:
        problems.append("the hooks find no interpreter")
    elif not _same_environment(py):
        # Compared, never executed: the pointer is checkout-controlled.
        problems.append(f"the hooks start {py}, not the interpreter running trw-mcp ({sys.executable})")
    if problems:
        return "FAIL", f"{'; '.join(problems)}. {_REMEDY}"
    return "PASS", f"the hooks start {py}, which imports trw_mcp."
