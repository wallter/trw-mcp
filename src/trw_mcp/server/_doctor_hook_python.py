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

A stale copy FAILs only when ``update-project`` would rewrite it. Each deployed
file names the clients whose update writes it; when the install record
(``target_platforms``) holds clients and none of them is one of those, a bare
``update-project`` leaves the file alone by design, so the row WARNs, names the
client and the file, and gives the remedy that does work (sub_GOYE8ssHqS-5q3hE:
the row failed an install over a file its own remedy could not fix). A record
that is absent, empty or unreadable downgrades nothing: every file is then
checked as recorded, which is also what ``update-project`` does without one.
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

#: Each deployed hook file with the clients whose ``update-project`` writers rewrite it (the first is the
#: one the remedy names). ``()`` belongs to no client and is always checked. Sources: ``.claude`` is
#: ``sync_cc03_hook_files`` (the claude-code and codex update paths), ``.cursor`` is ``_IDE_HOOK_SCRIPTS``
#: (cursor-ide only; the cursor-cli script list does not carry the lib), ``.github`` is ``_C5_HOOK_NAMES``.
_DEPLOYED: tuple[tuple[str, tuple[str, ...]], ...] = (
    (".claude/hooks/lib-distill-hint.sh", ("claude-code", "codex")),
    (".cursor/hooks/lib-distill-hint.sh", ("cursor-ide",)),
    (".github/hooks/lib-copilot-distill-hint.sh", ("copilot",)),
    (".trw/hooks/trw-post-commit.sh", ()),
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


def _recorded_clients(target: Path) -> list[str]:
    """The install record's clients; ``[]`` when it holds none or cannot be read (both mean "check everything")."""
    from trw_mcp.bootstrap._template_claude_md import _recorded_targets

    return _recorded_targets(target)


def _orphan_note(orphans: list[tuple[str, tuple[str, ...]]]) -> tuple[str, str]:
    """``(what is wrong, the remedy sentence)`` for stale files of clients the install record does not hold.

    The remedy names only what was rehearsed (2026-10-10, temp projects): with the file deleted the row
    passes, and ``update-project --ide <client>`` records the client and writes a current copy. An
    ``init-project --force --ide <client>`` also rewrites the file but REPLACES the record with that one
    client, so it is not offered.
    """
    described = "; ".join(
        f"{rel} still resolves the interpreter the old way and belongs to {' or '.join(owners)}, which this "
        "project's install record (target_platforms in .trw/config.yaml) does not hold, so trw-mcp "
        "update-project leaves it alone"
        for rel, owners in orphans
    )
    steps = ", and ".join(
        f"delete {rel} if you do not use {owners[0]} in this project or else run trw-mcp update-project --ide "
        f"{owners[0]} to add that client to the install record"
        for rel, owners in orphans
    )
    return described, f"Remedy: {steps}."


def hook_python_row(target: Path) -> tuple[Literal["PASS", "WARN", "FAIL", "SKIP"], str]:
    """FAIL when the hooks' interpreter cannot ``import trw_mcp``, or a recorded client's hook resolves the old way.

    WARN when the only finding is a stale hook of a client the install record does not hold.
    """
    sh = shutil.which("sh")
    if not (target / ".trw").is_dir() or sh is None:
        return "SKIP", "no .trw/ directory or no POSIX sh; no hook starts Python here."
    bundled = _resolver(_BUNDLED_LIB)
    stale_files = [
        (rel, owners) for rel, owners in _DEPLOYED if (target / rel).is_file() and _resolver(target / rel) != bundled
    ]
    # Read only when something is stale. An empty answer (no record, or an unreadable one) keeps every file a FAIL.
    recorded = set(_recorded_clients(target)) if stale_files else set()
    orphans = [(rel, owners) for rel, owners in stale_files if owners and recorded and recorded.isdisjoint(owners)]
    stale = [rel for rel, owners in stale_files if (rel, owners) not in orphans]
    problems = [f"{', '.join(stale)} still resolve(s) the interpreter the old way"] if stale else []
    _rc, py, _err = _run([sh, "-c", '. "$0" && _get_python_path "$1"', str(_BUNDLED_LIB), str(target)], target, 10)
    if not py:
        problems.append("the hooks find no interpreter")
    elif not _same_environment(py):
        # Compared, never executed: the pointer is checkout-controlled.
        problems.append(f"the hooks start {py}, not the interpreter running trw-mcp ({sys.executable})")
    orphan_problem, orphan_remedy = _orphan_note(orphans) if orphans else ("", "")
    if problems:
        # One remedy sentence: the installer lifts the sentence that begins "Remedy:". The orphaned file is
        # still described, and the row's WARN (with its own remedy) is what remains once the FAIL is fixed.
        return "FAIL", f"{'; '.join([*problems, orphan_problem] if orphans else problems)}. {_REMEDY}"
    if orphans:
        return "WARN", f"{orphan_problem}. {orphan_remedy}"
    return "PASS", f"the hooks start {py}, which imports trw_mcp."
