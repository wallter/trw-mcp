"""PRD-CORE-280 FR06 -- a checkout with nothing to move starts migrated: pinned and granted."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("trw_memory.daemon")

from trw_memory.daemon import DaemonPaths
from trw_memory.daemon._grants import CHECKOUT_TOKEN_RELPATH, granted_namespaces

from trw_mcp.state._store_migration import _pin

pytestmark = pytest.mark.integration


#: This test's own commits run no git hooks: init_project installs TRW's post-commit hook, whose
#: background worker auto-starts a memory daemon after the test has returned (rc9 C2 FR07 leaks).
_NO_HOOKS = ("-c", "core.hooksPath=/dev/null")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
    monkeypatch.delenv("TRW_PROJECT_ROOT", raising=False)
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    return root


def _derived(root: Path) -> str:
    from trw_memory.namespaces.identity import resolve_project_namespace

    return resolve_project_namespace(root)


def _grant(root: Path) -> frozenset[str] | None:
    token = (root / CHECKOUT_TOKEN_RELPATH).read_text(encoding="utf-8").strip()
    return granted_namespaces(DaemonPaths.resolve(), token)


def _unpinned(repo: Path) -> Path:
    """A checkout installed before FR06: initialised, then its pin and grant removed."""
    from trw_mcp.bootstrap import init_project
    from trw_mcp.state._store_migration import _set_pin

    assert not init_project(repo, ide="claude-code")["errors"]
    _set_pin(repo / ".trw", None)
    (repo / CHECKOUT_TOKEN_RELPATH).unlink()
    return repo


def test_init_project_pins_the_derived_namespace_and_grants_it_with_user_local(repo: Path) -> None:
    from trw_mcp.bootstrap import init_project

    assert not init_project(repo, ide="claude-code")["errors"]

    config = (repo / ".trw" / "config.yaml").read_text(encoding="utf-8")
    assert _pin(repo / ".trw") == _derived(repo)
    assert "memory_store" not in config
    assert "# TRW Framework Configuration" in config, "the pin keeps the template's comments"
    assert _grant(repo) == frozenset({_derived(repo), "user:local"})


@pytest.mark.parametrize("store_bytes", [None, b""], ids=["absent", "zero-bytes"])
def test_update_project_pins_and_grants_a_checkout_with_nothing_to_move(repo: Path, store_bytes: bytes | None) -> None:
    from trw_mcp.bootstrap import update_project

    _unpinned(repo)
    if store_bytes is not None:
        (repo / ".trw" / "memory").mkdir(parents=True, exist_ok=True)
        (repo / ".trw" / "memory" / "memory.db").write_bytes(store_bytes)

    result = update_project(repo)

    assert not result["errors"]
    assert _pin(repo / ".trw") == _derived(repo)
    assert _grant(repo) == frozenset({_derived(repo), "user:local"})


def test_update_project_leaves_a_checkout_whose_store_holds_data_and_names_the_migration(repo: Path) -> None:
    from trw_mcp.bootstrap import update_project

    _unpinned(repo)
    _store_with_data(repo)
    config = repo / ".trw" / "config.yaml"
    before = config.read_bytes()

    result = update_project(repo)

    assert config.read_bytes() == before
    assert not (repo / CHECKOUT_TOKEN_RELPATH).exists()
    named = [line for line in result["warnings"] if "trw-mcp memory migrate --to user" in line]
    assert len(named) == 1


def test_update_project_dry_run_neither_pins_nor_mints(repo: Path) -> None:
    from trw_mcp.bootstrap import update_project

    _unpinned(repo)
    config = repo / ".trw" / "config.yaml"
    before = config.read_bytes()

    result = update_project(repo, dry_run=True)

    assert config.read_bytes() == before
    assert not (repo / CHECKOUT_TOKEN_RELPATH).exists()
    assert "memory_namespace_pin" in result["would_run"]


def _git(cwd: Path, *argv: str) -> None:
    import subprocess

    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *argv],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_a_git_worktree_of_a_pinned_checkout_gets_its_missing_grant(tmp_path: Path, repo: Path) -> None:
    from trw_mcp.bootstrap import init_project, update_project

    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q")
    assert not init_project(main, ide="claude-code")["errors"]
    _git(main, "add", "-A")
    _git(main, *_NO_HOOKS, "commit", "-q", "-m", "install")
    worktree = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(worktree))
    assert _pin(worktree / ".trw") == _derived(main), "the pin is tracked, so the worktree inherits it"
    assert not (worktree / CHECKOUT_TOKEN_RELPATH).exists(), "the token is not"
    before = (worktree / ".trw" / "config.yaml").read_bytes()

    result = update_project(worktree)

    assert not result["errors"]
    assert (worktree / ".trw" / "config.yaml").read_bytes() == before
    assert _derived(worktree) == _derived(main)
    assert _grant(worktree) == frozenset({_derived(main), "user:local"})


def _store_with_data(root: Path, *, canary_only: bool = False) -> None:
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    store = SQLiteBackend(root / ".trw" / "memory" / "memory.db")
    try:
        store.store(MemoryEntry(id="C-1", content="decoy", namespace="default", metadata={"system_canary": "true"}))
        if not canary_only:
            store.store(MemoryEntry(id="L-1", content="a learning", namespace="default"))
    finally:
        store.close()


def test_update_project_mints_no_grant_for_a_pinned_checkout_whose_store_holds_data(repo: Path) -> None:
    from trw_mcp.bootstrap import init_project, update_project

    assert not init_project(repo, ide="claude-code")["errors"]
    (repo / CHECKOUT_TOKEN_RELPATH).unlink()
    _store_with_data(repo)

    result = update_project(repo)

    assert not (repo / CHECKOUT_TOKEN_RELPATH).exists()
    assert [line for line in result["warnings"] if "trw-mcp memory migrate --to user" in line]


def test_init_project_over_a_store_holding_data_warns_and_neither_pins_nor_mints(repo: Path) -> None:
    from trw_mcp.bootstrap import init_project

    _store_with_data(repo)

    result = init_project(repo, ide="claude-code")

    assert not result["errors"]
    assert not _pin(repo / ".trw")
    assert not (repo / CHECKOUT_TOKEN_RELPATH).exists()
    assert [line for line in result["warnings"] if "trw-mcp memory migrate --to user" in line]
    assert (repo / ".claude" / "settings.json").is_file(), "the later install phases still ran"


def test_init_project_force_keeps_config_bytes_when_the_store_holds_data(repo: Path) -> None:
    from trw_mcp.bootstrap import init_project

    config = repo / ".trw" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("# mine\ntask_root: elsewhere\n", encoding="utf-8")
    _store_with_data(repo)
    before = config.read_bytes()

    result = init_project(repo, force=True, ide="claude-code")

    assert not result["errors"]
    assert config.read_bytes() == before


def test_a_pin_that_is_not_this_checkouts_namespace_is_never_granted(repo: Path) -> None:
    from trw_mcp.bootstrap._namespace_pin import pin_empty_checkout
    from trw_mcp.state._store_migration import _set_pin

    (repo / ".trw").mkdir()
    _set_pin(repo / ".trw", "project:elsewhere-22222222")

    pin_empty_checkout(repo, {"warnings": []})

    assert _pin(repo / ".trw") == "project:elsewhere-22222222"
    assert not (repo / CHECKOUT_TOKEN_RELPATH).exists()


def test_init_project_force_keeps_a_moved_checkouts_pin_and_mints_no_foreign_grant(repo: Path) -> None:
    from trw_mcp.bootstrap import init_project
    from trw_mcp.state._store_migration import _set_pin

    (repo / ".trw").mkdir()
    _set_pin(repo / ".trw", "project:moved-33333333")
    assert _derived(repo) != "project:moved-33333333"

    result = init_project(repo, force=True, ide="claude-code")

    assert not result["errors"]
    assert _pin(repo / ".trw") == "project:moved-33333333"
    assert not (repo / CHECKOUT_TOKEN_RELPATH).exists()
    assert "# TRW Framework Configuration" in (repo / ".trw" / "config.yaml").read_text(encoding="utf-8")


def test_update_project_pins_a_checkout_whose_store_holds_only_canary_decoys(repo: Path) -> None:
    from trw_mcp.bootstrap import update_project

    _unpinned(repo)
    _store_with_data(repo, canary_only=True)

    result = update_project(repo)

    assert not result["errors"]
    assert _pin(repo / ".trw") == _derived(repo), "decoys are not learnings; there is nothing to move"
    assert _grant(repo) == frozenset({_derived(repo), "user:local"})


def test_a_corrupt_config_fails_open_with_a_warning_and_neither_pins_nor_mints(repo: Path) -> None:
    from trw_mcp.bootstrap._namespace_pin import pin_empty_checkout

    config = repo / ".trw" / "config.yaml"
    config.parent.mkdir(exist_ok=True)
    config.write_text("{corrupt: [unclosed", encoding="utf-8")
    result: dict[str, list[str]] = {}

    pin_empty_checkout(repo, result)

    assert ["does not parse" in line for line in result["warnings"]] == [True]
    assert config.read_text(encoding="utf-8") == "{corrupt: [unclosed"
    assert not (repo / CHECKOUT_TOKEN_RELPATH).exists()
