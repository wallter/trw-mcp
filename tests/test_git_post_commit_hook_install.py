"""PRD-CORE-231 FR01/FR02: the post-commit hook is INSTALLED and actually fires.

The original FR01 implementation shipped a bundled ``post-commit`` script with
no installation path at all: ``_install_hooks`` only copies into
``.claude/hooks/`` (the Claude tool-lifecycle surface, which has no
``post-commit`` event), so the script was dead weight and deleting it would have
broken zero tests. These tests close that delivered!=wired gap end to end:
install into a scratch repo, make a REAL commit, and assert the maintenance
receipt exists.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap._git_hooks import (
    BUNDLED_HOOK,
    INSTALLED_HOOK_REL,
    MARKER_END,
    MARKER_START,
    install_git_post_commit_hook,
    render_post_commit_shim,
)
from trw_mcp.tools._post_commit import RECEIPT_REL_PATH, read_receipt, run_post_commit

_REPO_SRC = Path(__file__).resolve().parent.parent / "src"
_MEMORY_SRC = Path(__file__).resolve().parent.parent.parent / "trw-memory" / "src"


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / ".trw").mkdir()
    return tmp_path


def _hook_path(repo: Path) -> Path:
    return repo / ".git" / "hooks" / "post-commit"


def _commit(repo: Path, name: str, *, sync_hook: bool = True) -> None:
    """Make a real commit, running the hook synchronously so it is observable."""
    (repo / name).write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(_REPO_SRC), str(_MEMORY_SRC), env.get("PYTHONPATH", "")])
    env["TRW_PROJECT_DIR"] = str(repo)
    if sync_hook:
        env["TRW_POST_COMMIT_SYNC"] = "1"
    subprocess.run(["git", "commit", "-qm", f"add {name}"], cwd=repo, check=True, env=env, timeout=120)


# --- installation --------------------------------------------------------


def test_installer_creates_both_the_script_and_the_shim(git_repo: Path) -> None:
    """The bundled script lands in .trw/hooks/ and .git/hooks/post-commit dispatches to it."""
    result = install_git_post_commit_hook(git_repo)

    assert not result["errors"]
    script = git_repo / INSTALLED_HOOK_REL
    assert script.is_file()
    assert os.access(script, os.X_OK), "installed script must be executable"

    hook = _hook_path(git_repo)
    assert hook.is_file()
    assert os.access(hook, os.X_OK), "post-commit shim must be executable"
    body = hook.read_text(encoding="utf-8")
    assert MARKER_START in body
    assert INSTALLED_HOOK_REL.as_posix() in body


def test_install_is_idempotent(git_repo: Path) -> None:
    """Re-running never duplicates the dispatch block."""
    install_git_post_commit_hook(git_repo)
    first = _hook_path(git_repo).read_text(encoding="utf-8")

    install_git_post_commit_hook(git_repo, force=True)
    second = _hook_path(git_repo).read_text(encoding="utf-8")

    assert first == second
    assert second.count(MARKER_START) == 1
    assert second.count(MARKER_END) == 1


def test_existing_user_hook_is_chained_not_clobbered(git_repo: Path) -> None:
    """A foreign post-commit hook survives; TRW appends a guarded block."""
    hook = _hook_path(git_repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho 'user hook ran'\n", encoding="utf-8")

    install_git_post_commit_hook(git_repo)

    body = hook.read_text(encoding="utf-8")
    assert "echo 'user hook ran'" in body
    assert MARKER_START in body
    assert body.index("echo 'user hook ran'") < body.index(MARKER_START)


def test_reinstall_over_a_chained_hook_replaces_only_the_trw_block(git_repo: Path) -> None:
    """User content stays byte-identical across updates."""
    hook = _hook_path(git_repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho 'user hook ran'\n", encoding="utf-8")
    install_git_post_commit_hook(git_repo)

    install_git_post_commit_hook(git_repo, force=True)

    body = hook.read_text(encoding="utf-8")
    assert body.count("echo 'user hook ran'") == 1
    assert body.count(MARKER_START) == 1


def test_render_is_pure_and_idempotent() -> None:
    """f(f(x)) == f(x) for every shim shape."""
    for existing in (None, "", "#!/bin/sh\necho hi\n"):
        once = render_post_commit_shim(existing)
        assert render_post_commit_shim(once) == once


def test_non_git_directory_is_skipped_not_an_error(tmp_path: Path) -> None:
    """Installing outside a repo reports a skip, never an exception."""
    result = install_git_post_commit_hook(tmp_path)

    assert not result["errors"]
    assert result["skipped"]


def test_dry_run_touches_nothing(git_repo: Path) -> None:
    result = install_git_post_commit_hook(git_repo, dry_run=True)

    assert not _hook_path(git_repo).exists()
    assert not (git_repo / INSTALLED_HOOK_REL).exists()
    assert result["created"]


# --- bootstrap wiring ----------------------------------------------------


def test_install_hooks_bootstrap_path_installs_the_git_hook(git_repo: Path) -> None:
    """_install_hooks (the real bootstrap seam) must reach the git-hook installer."""
    from trw_mcp.bootstrap._init_project import _install_hooks

    result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}
    _install_hooks(git_repo, False, result)

    assert _hook_path(git_repo).is_file()
    assert (git_repo / INSTALLED_HOOK_REL).is_file()


# --- end-to-end firing ---------------------------------------------------


def test_hook_fires_on_a_real_commit(git_repo: Path) -> None:
    """The whole point: install, commit for real, and observe the receipt.

    Proves the sidecar refresh (FR01) AND maintain-verify (FR02) ran from the
    git post-commit event — the latency bound NFR02 derives rests on this.
    """
    install_git_post_commit_hook(git_repo)
    assert read_receipt(git_repo) is None, "no receipt should exist before the first commit"

    _commit(git_repo, "a.py")

    receipt = read_receipt(git_repo)
    assert receipt is not None, f"hook did not fire; {RECEIPT_REL_PATH} absent"
    assert receipt["ran_at"]
    assert receipt["head_sha"], "receipt must record the committed sha"
    # Both maintenance halves reported, even when each is a no-op here.
    assert "sidecar_skipped_reason" in receipt
    assert "verify_entries_processed" in receipt


def test_hook_never_fails_the_commit(git_repo: Path) -> None:
    """NFR02 fail-open: a hostile dispatch target must not break `git commit`."""
    install_git_post_commit_hook(git_repo)
    # Simulate a broken/partial install: the dispatched script exits non-zero.
    (git_repo / INSTALLED_HOOK_REL).write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")

    _commit(git_repo, "b.py", sync_hook=False)

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True, text=True, check=True)
    assert head.stdout.strip(), "commit must have landed despite the failing hook"


def test_user_hook_still_runs_after_chaining(git_repo: Path) -> None:
    """Chaining must not silently disable the user's own hook."""
    hook = _hook_path(git_repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    marker = git_repo / "user-hook-ran.txt"
    hook.write_text(f"#!/bin/sh\necho ran > {marker}\n", encoding="utf-8")
    install_git_post_commit_hook(git_repo)

    _commit(git_repo, "c.py", sync_hook=False)

    assert marker.is_file(), "the pre-existing user hook stopped running"


# --- entry point ---------------------------------------------------------


def test_run_post_commit_writes_a_receipt_even_when_everything_no_ops(git_repo: Path) -> None:
    """The receipt is the observability contract — always written."""
    receipt = run_post_commit(git_repo, {"PATH": os.environ.get("PATH", "")})

    assert receipt.ran_at
    written = json.loads((git_repo / RECEIPT_REL_PATH).read_text(encoding="utf-8"))
    assert written["ran_at"] == receipt.ran_at


def test_bundled_hook_is_posix_sh() -> None:
    completed = subprocess.run(["sh", "-n", str(BUNDLED_HOOK)], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
