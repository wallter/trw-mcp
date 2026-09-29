"""Run the CC-03 distill-hint hook from the layout init_project deploys.

Installed, ``pre-tool-distill-hint.sh`` sits in ``.claude/hooks`` beside
``lib-distill-hint.sh`` and the shared ``lib-trw.sh`` whose ``_json_get`` reads
its payload. The package keeps those in two data directories, so the tests copy
all three into the project first.

Isolation (learning L-CUAX): every caller of the hook subprocess used to
inherit the pytest process's own cwd -- the ENCLOSING checkout -- instead of
the throwaway ``tmp_path`` project. The hook's own inline Python calls
``compute_before_edit_hint(file_path=target)`` with no ``repo_root``, so
``resolve_repo_root(None)`` (trw_mcp/tools/_sidecar_substrate.py) falls back to
``git rev-parse --show-toplevel`` at the SUBPROCESS's cwd, and
``resolve_project_root()`` (trw_mcp/state/_paths.py) falls back to
``Path.cwd()`` -- both of which resolved the real checkout when no ``cwd=``
was passed to ``subprocess.run``. Reproduced non-destructively: calling
``compute_before_edit_hint`` with the enclosing checkout as cwd logs
``memory_recall_store_unavailable`` naming that checkout's own path; with cwd
pointed at an isolated throwaway repo it names that repo instead. Every hook
invocation below now pins ``cwd`` to a throwaway ``git init`` repo under
``tmp_path`` and ``HOME`` to ``tmp_path``, so neither fallback can ever resolve
outside the test's own sandbox.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_DATA = Path(__file__).resolve().parents[3] / "src" / "trw_mcp" / "data"
# The hook runs ``cc03-python.txt``'s interpreter (the tests write ``sys.executable``), and that
# child imports ``trw_mcp``. Without this checkout's src on its path it imports whatever the
# interpreter has installed -- in the shared venv, the main checkout's editable copy -- so a
# test run from a worktree or an int checkout would grade another tree's hint program
# (INT-RED-CC03; learning L-rSOW). Every hook subprocess env includes it.
_ROOT = Path(__file__).resolve().parents[3]
CHECKOUT_PYTHONPATH = os.pathsep.join(
    str(src) for src in (_ROOT / "src", _ROOT.parent / "trw-memory" / "src") if src.is_dir()
)

_FILES = (
    _DATA / "claude_code" / "hooks" / "pre-tool-distill-hint.sh",
    _DATA / "claude_code" / "hooks" / "lib-distill-hint.sh",
    _DATA / "hooks" / "lib-trw.sh",
)


def deploy_distill_hint(project: Path) -> Path:
    """Copy the hook and its two libraries into *project*/.claude/hooks; return the hook."""
    hooks = project / ".claude" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    for source in _FILES:
        shutil.copy(source, hooks / source.name)
    return hooks / _FILES[0].name


def init_isolated_repo(project: Path) -> Path:
    """``git init`` *project* in place (idempotent) so it resolves as its own repo root.

    L-CUAX: a ``git rev-parse --show-toplevel`` run with cwd inside *project*
    must find *project*'s own ``.git``, never climb out to the enclosing
    checkout. Safe to call once per test; ``git init`` on an already-initialized
    directory is a no-op that still exits 0.
    """
    project.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q"],
        cwd=project,
        check=True,
        capture_output=True,
        timeout=10,
    )
    return project


def run_distill_hint_hook(
    payload: str,
    project: Path,
    *,
    timeout: int = 8,
    checkout_pythonpath: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the deployed CC-03 hook against *project*, fully isolated from the enclosing checkout.

    L-CUAX: pins ``cwd`` to *project* (a throwaway ``git init`` repo, per
    :func:`init_isolated_repo`) and ``HOME`` to *project*'s parent (``tmp_path``)
    so neither the hook's own ``git rev-parse`` fallback nor
    ``compute_before_edit_hint``'s ``Path.cwd()`` fallback can ever resolve the
    real checkout running the test suite.

    ``checkout_pythonpath=False``: the interpreter must see only what it has
    installed (the import-failure case needs a Python that cannot import
    ``trw_mcp`` at all).
    """
    from trw_memory.testing.daemon_reaper import daemon_env_passthrough

    init_isolated_repo(project)
    env = {
        **daemon_env_passthrough(),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(project.parent),
        "TRW_PROJECT_DIR": str(project),
        **({"PYTHONPATH": CHECKOUT_PYTHONPATH} if checkout_pythonpath else {}),
        **(extra_env or {}),
    }
    return subprocess.run(
        ["sh", str(deploy_distill_hint(project))],
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=project,
        env=env,
    )
