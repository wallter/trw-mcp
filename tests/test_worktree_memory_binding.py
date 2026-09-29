"""A linked git worktree reaches its main checkout's memory (``trw_memory.namespaces.worktree``).

A worktree's ``.trw`` is its own and untracked, so it holds neither the
``project_namespace`` pin nor the grant token. ``selected_store`` borrows the main
checkout's pin and grant only for a genuine linked worktree of the same repository
whose canonical namespace equals that pin; everything else fails closed as before.

Real ``git init`` / ``git worktree add`` / ``git clone`` in ``tmp_path`` with
``HOME`` in ``tmp_path``. The daemon is a fake at the ``DaemonClient`` port, so the
real ``daemon_store_for`` reads the real grant file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from pydantic_core import to_jsonable_python
from trw_memory.daemon import write_checkout_grant
from trw_memory.models.config import MemoryConfig, daemon_wide_security
from trw_memory.namespaces.identity import resolve_project_namespace

from tests._anchor_daemon_fake import AnchoredDaemon, lesson
from tests._path_isolation import set_current_root
from trw_mcp.models.config import reload_config
from trw_mcp.state import _daemon_store
from trw_mcp.state._daemon_store import DaemonMemoryStore
from trw_mcp.state._store_selection import StoreUnavailableError, selected_store

pytestmark = pytest.mark.unit

_MAIN_TOKEN = "main-checkout-token"
_FILE = "src/mod.py"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


class Repo(NamedTuple):
    main: Path
    worktree: Path
    pin: str


def _snapshot(*roots: Path) -> dict[str, bytes]:
    """Every file under *roots* (``.git`` included) with its bytes: proves nothing was written."""
    return {str(p): p.read_bytes() for root in roots for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Repo]:
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key)
    for key in ("TRW_PROJECT_NAMESPACE", "TRW_SURFACE_ROLE", "TRW_DISPATCH_CHILD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    main = (tmp_path / "main").resolve()
    main.mkdir()
    _git(main, "init", "-q", "-b", "trunk")
    (main / "src").mkdir()
    (main / _FILE).write_text("x = 1\n", encoding="utf-8")
    _git(main, "add", _FILE)
    _git(main, "commit", "-q", "-m", "init")
    pin = resolve_project_namespace(main)
    (main / ".trw").mkdir()
    (main / ".trw" / "config.yaml").write_text(f"project_namespace: {pin}\n", encoding="utf-8")
    write_checkout_grant(main / ".trw", _MAIN_TOKEN)
    worktree = (tmp_path / "wt").resolve()
    _git(main, "worktree", "add", "-q", "-b", "lane", str(worktree))
    (worktree / ".trw").mkdir()
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(worktree))
    set_current_root(worktree)  # the suite's path isolation resolves the project root here
    monkeypatch.setattr(_daemon_store, "_clients", {})
    reload_config()
    yield Repo(main, worktree, pin)
    reload_config()


class _FakeDaemon(AnchoredDaemon):
    """A daemon at the ``DaemonClient`` port: answers the security check; records every token and tool served."""

    tokens: list[str] = []
    served: list[tuple[str, str]] = []
    rows_for_next: list[Any] = []

    def __init__(self, token: str, **_: Any) -> None:
        super().__init__(self.rows_for_next)
        self.calls = type(self).served  # one record across the clients selected_store builds
        type(self).tokens.append(token)

    async def retire(self) -> None:
        """A replaced client closes nothing: the fake holds no session."""

    async def call_tool(self, name: str, _args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, ""))
        local = to_jsonable_python(daemon_wide_security(MemoryConfig()))
        return {"security_settings": local, "daemon": [1, "fake"]}


@pytest.fixture
def daemon(monkeypatch: pytest.MonkeyPatch) -> type[_FakeDaemon]:
    fake = type("_Daemon", (_FakeDaemon,), {"tokens": [], "served": [], "rows_for_next": []})
    monkeypatch.setattr("trw_memory.daemon.client.DaemonClient", fake)
    return fake


# -- the borrow ------------------------------------------------------------------------------


def test_a_linked_worktree_borrows_the_main_checkouts_pin_and_grant(repo: Repo, daemon: type[_FakeDaemon]) -> None:
    before = _snapshot(repo.main, repo.worktree)

    store, namespace = selected_store(repo.worktree / ".trw")

    assert (type(store), namespace) == (DaemonMemoryStore, repo.pin)
    assert namespace == resolve_project_namespace(repo.worktree)
    assert set(daemon.tokens) == {_MAIN_TOKEN}
    assert _snapshot(repo.main, repo.worktree) == before, "nothing may be written into either checkout"


def test_the_main_checkout_still_routes_by_its_own_pin_and_grant(repo: Repo, daemon: type[_FakeDaemon]) -> None:
    before = _snapshot(repo.main)

    _store, namespace = selected_store(repo.main / ".trw")

    assert (namespace, set(daemon.tokens)) == (repo.pin, {_MAIN_TOKEN})
    assert _snapshot(repo.main) == before


def test_a_worktree_with_its_own_pin_keeps_it(repo: Repo, daemon: type[_FakeDaemon]) -> None:
    """The borrow is only for an unpinned worktree: its own pin (and missing grant) still decide."""
    (repo.worktree / ".trw" / "config.yaml").write_text(f"project_namespace: {repo.pin}\n", encoding="utf-8")

    with pytest.raises(StoreUnavailableError, match="trw-mcp memory token"):
        selected_store(repo.worktree / ".trw")
    assert daemon.tokens == []


def test_a_worktree_pinned_elsewhere_keeps_its_own_pin_and_grant_never_mains(
    repo: Repo, daemon: type[_FakeDaemon]
) -> None:
    """Invariant: the worktree's own pin is never merged with or overridden by the main checkout's."""
    (repo.worktree / ".trw" / "config.yaml").write_text("project_namespace: project:own-1a2b3c4d\n")
    write_checkout_grant(repo.worktree / ".trw", "worktree-own-token")

    _store, namespace = selected_store(repo.worktree / ".trw")

    assert (namespace, set(daemon.tokens)) == ("project:own-1a2b3c4d", {"worktree-own-token"})


# -- invariant 1: only a genuine linked worktree of the same repository borrows ---------------


def test_a_separate_clone_does_not_borrow(repo: Repo, tmp_path: Path, daemon: type[_FakeDaemon]) -> None:
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(repo.main), str(clone))
    (clone / ".trw").mkdir()

    with pytest.raises(StoreUnavailableError, match="has no project_namespace; run `trw-mcp update-project`"):
        selected_store(clone / ".trw")
    assert daemon.tokens == []


def test_a_subdirectory_of_the_main_checkout_does_not_borrow(repo: Repo, daemon: type[_FakeDaemon]) -> None:
    """A parent-walk would find the main checkout's token here; git says it is not a worktree."""
    nested = repo.main / "src" / "nested"
    (nested / ".trw").mkdir(parents=True)

    with pytest.raises(StoreUnavailableError, match="has no project_namespace; run `trw-mcp update-project`"):
        selected_store(nested / ".trw")
    assert daemon.tokens == []


def test_a_subdirectory_of_the_worktree_does_not_borrow(repo: Repo, daemon: type[_FakeDaemon]) -> None:
    nested = repo.worktree / "src" / "nested"
    (nested / ".trw").mkdir(parents=True)

    with pytest.raises(StoreUnavailableError, match="run `trw-mcp update-project`"):
        selected_store(nested / ".trw")
    assert daemon.tokens == []


def test_a_non_git_directory_does_not_borrow(repo: Repo, tmp_path: Path, daemon: type[_FakeDaemon]) -> None:
    plain = tmp_path / "plain"
    (plain / ".trw").mkdir(parents=True)

    with pytest.raises(StoreUnavailableError, match="has no project_namespace; run `trw-mcp update-project`"):
        selected_store(plain / ".trw")
    assert daemon.tokens == []


def test_an_inherited_git_dir_does_not_make_a_directory_a_worktree(
    repo: Repo, tmp_path: Path, daemon: type[_FakeDaemon], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook's GIT_DIR names the worktree's repo; git is still asked about the directory itself."""
    plain = tmp_path / "plain"
    (plain / ".trw").mkdir(parents=True)
    monkeypatch.setenv("GIT_DIR", str(repo.main / ".git" / "worktrees" / "wt"))
    monkeypatch.setenv("GIT_WORK_TREE", str(repo.worktree))

    with pytest.raises(StoreUnavailableError, match="run `trw-mcp update-project`"):
        selected_store(plain / ".trw")
    assert daemon.tokens == []


# -- invariant 1b: the link must be registered both ways, as git keeps it -------------------


def test_a_copied_git_pointer_in_an_unrelated_directory_does_not_borrow(
    repo: Repo, tmp_path: Path, daemon: type[_FakeDaemon]
) -> None:
    """git follows the copied pointer, but the admin dir records the real worktree's path."""
    impostor = tmp_path / "impostor"
    (impostor / ".trw").mkdir(parents=True)
    shutil.copy(repo.worktree / ".git", impostor / ".git")

    with pytest.raises(StoreUnavailableError, match="is not registered as the worktree"):
        selected_store(impostor / ".trw")
    assert daemon.tokens == []


def test_a_worktree_moved_without_repair_does_not_borrow(repo: Repo, tmp_path: Path, daemon: type[_FakeDaemon]) -> None:
    moved = tmp_path / "moved"
    shutil.move(str(repo.worktree), str(moved))

    with pytest.raises(StoreUnavailableError, match="git worktree repair"):
        selected_store(moved / ".trw")
    assert daemon.tokens == []


def test_a_symlink_to_a_registered_worktree_borrows_as_the_worktree_does(
    repo: Repo, tmp_path: Path, daemon: type[_FakeDaemon]
) -> None:
    """Documented choice: the path is realpath'd before the registration check, as identity does."""
    link = tmp_path / "link"
    link.symlink_to(repo.worktree, target_is_directory=True)

    _store, namespace = selected_store(link / ".trw")

    assert (namespace, set(daemon.tokens)) == (repo.pin, {_MAIN_TOKEN})


def test_an_unreadable_registration_fails_closed(repo: Repo, daemon: type[_FakeDaemon]) -> None:
    registration = repo.main / ".git" / "worktrees" / "wt" / "gitdir"
    registration.chmod(0)
    try:
        with pytest.raises(StoreUnavailableError, match="git worktree link could not be read"):
            selected_store(repo.worktree / ".trw")
    finally:
        registration.chmod(0o644)
    assert daemon.tokens == []


def test_a_second_git_failure_in_the_identity_check_fails_closed(
    repo: Repo, daemon: type[_FakeDaemon], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The canonical identity must come from git itself, not the on-disk fallback it uses when git fails."""
    from trw_memory.namespaces import identity

    monkeypatch.setattr(
        identity, "_git_common_dir", lambda _root: identity._CommonDirProbe(None, "unavailable", "TimeoutExpired")
    )

    with pytest.raises(StoreUnavailableError, match="established by: filesystem"):
        selected_store(repo.worktree / ".trw")
    assert daemon.tokens == []


def test_a_symlinked_git_pointer_in_an_unrelated_directory_does_not_borrow(
    repo: Repo, tmp_path: Path, daemon: type[_FakeDaemon]
) -> None:
    """Realpaths on both sides: a symlink onto the registered .git file does not make its directory the worktree."""
    impostor = tmp_path / "impostor"
    (impostor / ".trw").mkdir(parents=True)
    (impostor / ".git").symlink_to(repo.worktree / ".git")

    with pytest.raises(StoreUnavailableError, match="is a symlink"):
        selected_store(impostor / ".trw")
    assert daemon.tokens == []


def test_a_symlinked_registration_does_not_borrow(repo: Repo, tmp_path: Path, daemon: type[_FakeDaemon]) -> None:
    registration = repo.main / ".git" / "worktrees" / "wt" / "gitdir"
    decoy = tmp_path / "decoy-gitdir"
    decoy.write_text(registration.read_text(encoding="utf-8"), encoding="utf-8")
    registration.unlink()
    registration.symlink_to(decoy)

    with pytest.raises(StoreUnavailableError, match="symlink"):
        selected_store(repo.worktree / ".trw")
    assert daemon.tokens == []


@pytest.mark.parametrize("registration", ["", "  \n\t\n"], ids=["empty", "whitespace-only"])
def test_an_empty_registration_fails_closed(repo: Repo, daemon: type[_FakeDaemon], registration: str) -> None:
    """A gitdir back-pointer that names no path (``registered_at`` is None) never matches the worktree."""
    (repo.main / ".git" / "worktrees" / "wt" / "gitdir").write_text(registration, encoding="utf-8")

    with pytest.raises(StoreUnavailableError, match="not registered"):
        selected_store(repo.worktree / ".trw")
    assert daemon.tokens == []


def test_a_main_checkout_whose_git_is_a_pointer_file_lends_nothing(
    tmp_path: Path, repo: Repo, daemon: type[_FakeDaemon]
) -> None:
    """Invariant: the common dir's parent must hold a real .git DIRECTORY; a separate git dir's owner lends nothing."""
    main = tmp_path / "sep-main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "trunk", "--separate-git-dir", str(tmp_path / "vault.git"))
    (main / "f").write_text("x\n", encoding="utf-8")
    _git(main, "add", "f")
    _git(main, "commit", "-q", "-m", "init")
    (main / ".trw").mkdir()
    (main / ".trw" / "config.yaml").write_text(f"project_namespace: {resolve_project_namespace(main)}\n")
    write_checkout_grant(main / ".trw", "sep-main-token")
    worktree = tmp_path / "sep-wt"
    _git(main, "worktree", "add", "-q", "-b", "lane2", str(worktree))
    (worktree / ".trw").mkdir()

    with pytest.raises(StoreUnavailableError, match="run `trw-mcp update-project`"):
        selected_store(worktree / ".trw")
    assert daemon.tokens == []


# -- invariant 2: the borrowed pin must equal the canonical namespace -----------------------


def test_a_main_pin_that_is_not_the_canonical_namespace_fails_closed_naming_both(
    repo: Repo, daemon: type[_FakeDaemon]
) -> None:
    (repo.main / ".trw" / "config.yaml").write_text("project_namespace: project:elsewhere-0badc0de\n")

    with pytest.raises(StoreUnavailableError) as raised:
        selected_store(repo.worktree / ".trw")

    message = str(raised.value)
    assert "project:elsewhere-0badc0de" in message and repo.pin in message
    assert "trw-mcp memory token" in message
    assert daemon.tokens == []


def test_an_unpinned_main_checkout_lends_nothing(repo: Repo, daemon: type[_FakeDaemon]) -> None:
    (repo.main / ".trw" / "config.yaml").write_text("telemetry: false\n")

    with pytest.raises(StoreUnavailableError, match="run `trw-mcp update-project`"):
        selected_store(repo.worktree / ".trw")
    assert daemon.tokens == []


def test_an_unreadable_main_config_fails_closed(repo: Repo, daemon: type[_FakeDaemon]) -> None:
    (repo.main / ".trw" / "config.yaml").write_text("project_namespace: [unclosed\n")

    with pytest.raises(StoreUnavailableError, match="main checkout's project_namespace is unreadable"):
        selected_store(repo.worktree / ".trw")
    assert daemon.tokens == []


def test_a_missing_main_grant_fails_closed_naming_the_mint(repo: Repo, daemon: type[_FakeDaemon]) -> None:
    (repo.main / ".trw" / "runtime" / "memory-token").unlink()

    with pytest.raises(StoreUnavailableError, match="trw-mcp memory token"):
        selected_store(repo.worktree / ".trw")
    assert daemon.tokens == []


# -- invariant 5: git failure keeps today's refusal and names the fix -----------------------


def test_git_that_cannot_run_fails_closed_naming_the_fix(
    repo: Repo, tmp_path: Path, daemon: type[_FakeDaemon], monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "nogit"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    with pytest.raises(StoreUnavailableError) as raised:
        selected_store(repo.worktree / ".trw")

    message = str(raised.value)
    assert "git could not run" in message and "run `trw-mcp update-project`" in message
    assert daemon.tokens == []


def test_a_worktree_whose_git_admin_dir_is_gone_fails_closed(repo: Repo, daemon: type[_FakeDaemon]) -> None:
    shutil.rmtree(repo.main / ".git" / "worktrees" / "wt")

    with pytest.raises(StoreUnavailableError, match="run `trw-mcp update-project`"):
        selected_store(repo.worktree / ".trw")
    assert daemon.tokens == []


# -- end to end: recall and the pre-edit hint, from the worktree ----------------------------


def test_recall_from_a_worktree_returns_the_main_checkouts_lessons(repo: Repo, daemon: type[_FakeDaemon]) -> None:
    from trw_mcp.state._memory_recall import pop_store_error
    from trw_mcp.state.memory_adapter import recall_learnings

    daemon.rows_for_next = [lesson("L-wt1", "gizmoflux handler needs the lock", namespace=repo.pin)]
    before = _snapshot(repo.main)
    pop_store_error()  # as trw_recall does: an earlier test in this worker may have left one

    rows = recall_learnings(repo.worktree / ".trw", "gizmoflux", status="active")

    assert pop_store_error() is None
    assert [row["id"] for row in rows] == ["L-wt1"]
    assert set(daemon.tokens) == {_MAIN_TOKEN}
    assert _snapshot(repo.main) == before


def test_the_pre_edit_hint_in_a_worktree_carries_the_main_checkouts_lesson(
    repo: Repo, daemon: type[_FakeDaemon]
) -> None:
    """The production call site: ``compute_before_edit_hint`` (``trw_code(mode="hint")`` and the edit hooks)."""
    from trw_mcp.tools._before_edit_hint_core import compute_before_edit_hint

    daemon.rows_for_next = [lesson("L-wt2", "close the transport", anchors=(_FILE,), namespace=repo.pin)]

    result = compute_before_edit_hint(file_path=str(repo.worktree / _FILE), repo_root=str(repo.worktree))

    assert [item.id for item in result.learnings] == ["L-wt2"]
    assert set(daemon.tokens) == {_MAIN_TOKEN}


# -- invariant 4: the reviewer role gains nothing ------------------------------------------


def test_a_reviewer_in_a_worktree_reads_the_same_rows_and_writes_nothing(
    repo: Repo, daemon: type[_FakeDaemon], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state.memory_adapter import recall_learnings

    daemon.rows_for_next = [lesson("L-wt3", "gizmoflux reviewer row", namespace=repo.pin)]
    as_worker = [row["id"] for row in recall_learnings(repo.worktree / ".trw", "gizmoflux")]
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    before = _snapshot(repo.main, repo.worktree)

    as_reviewer = [row["id"] for row in recall_learnings(repo.worktree / ".trw", "gizmoflux")]

    assert as_reviewer == as_worker == ["L-wt3"]
    assert _snapshot(repo.main, repo.worktree) == before
    assert ("memory_recall", repo.pin) in daemon.served
    assert {name for name, _ in daemon.served} <= {"memory_status", "memory_recall"}, "recall only reads"
