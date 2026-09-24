"""Rollback must not rewind mutable TRW runtime state."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from trw_mcp.bootstrap._update_transaction import (
    _MANAGED_TRW_FILES,
    _restore_transaction_snapshot,
    _snapshot_transaction_paths,
)
from trw_mcp.canons.registry import install_view, load_registry
from trw_mcp.framework_deployment import DEPLOYMENT_RELATIVE_PATH


def test_transaction_covers_every_managed_framework_artifact() -> None:
    registry = load_registry()
    expected = {
        str(DEPLOYMENT_RELATIVE_PATH),
        *(dest for _, dest in install_view(registry) if dest.startswith(".trw/")),
    }
    assert expected <= set(_MANAGED_TRW_FILES)


def test_transaction_restore_preserves_runtime_writes_after_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "project"
    config = target / ".trw" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("version: before\n", encoding="utf-8")
    framework = target / ".trw" / "frameworks" / "FRAMEWORK.md"
    framework.parent.mkdir(parents=True)
    framework.write_text("before framework\n", encoding="utf-8")

    snapshot = _snapshot_transaction_paths(target)
    try:
        config.write_text("version: changed\n", encoding="utf-8")
        framework.write_text("changed framework\n", encoding="utf-8")
        custom_framework = framework.with_name("CUSTOM.md")
        custom_framework.write_text("concurrent custom framework\n", encoding="utf-8")
        learning = target / ".trw" / "learnings" / "entries" / "concurrent.yaml"
        learning.parent.mkdir(parents=True)
        learning.write_text("id: concurrent\n", encoding="utf-8")
        run = target / ".trw" / "runs" / "active" / "meta" / "run.yaml"
        run.parent.mkdir(parents=True)
        run.write_text("status: active\n", encoding="utf-8")
        wal = target / ".trw" / "memory" / "memory.db-wal"
        wal.parent.mkdir(parents=True)
        wal.write_bytes(b"live-wal")

        _restore_transaction_snapshot(target, snapshot)

        assert config.read_text(encoding="utf-8") == "version: before\n"
        assert framework.read_text(encoding="utf-8") == "before framework\n"
        assert custom_framework.read_text(encoding="utf-8") == "concurrent custom framework\n"
        assert learning.read_text(encoding="utf-8") == "id: concurrent\n"
        assert run.read_text(encoding="utf-8") == "status: active\n"
        assert wal.read_bytes() == b"live-wal"
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)


def test_transaction_snapshot_excludes_memory_database(tmp_path: Path) -> None:
    target = tmp_path / "project"
    database = target / ".trw" / "memory" / "memory.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"database")

    snapshot = _snapshot_transaction_paths(target)
    try:
        assert not (snapshot / ".trw" / "memory").exists()
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)


def test_transaction_restore_refuses_replaced_trw_symlink(tmp_path: Path) -> None:
    target = tmp_path / "project"
    config = target / ".trw" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("original\n", encoding="utf-8")
    snapshot = _snapshot_transaction_paths(target)

    external = tmp_path / "external"
    external.mkdir()
    external_config = external / "config.yaml"
    external_config.write_text("must survive\n", encoding="utf-8")
    shutil.rmtree(target / ".trw")
    (target / ".trw").symlink_to(external, target_is_directory=True)

    try:
        with pytest.raises(OSError, match="contains a symlink"):
            _restore_transaction_snapshot(target, snapshot)
        assert external_config.read_text(encoding="utf-8") == "must survive\n"
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)


@pytest.mark.parametrize(
    ("managed_path", "is_directory"),
    [
        (Path(".claude"), True),
        (Path(".claude/skills"), True),
        (Path("AGENTS.md"), False),
        (Path(".trw"), True),
    ],
)
def test_transaction_snapshot_rejects_managed_symlink_escape(
    tmp_path: Path,
    managed_path: Path,
    is_directory: bool,
) -> None:
    target = tmp_path / "project"
    target.mkdir()
    external = tmp_path / ("external-dir" if is_directory else "external-file")
    if is_directory:
        external.mkdir()
        (external / "sentinel.txt").write_text("untouched\n", encoding="utf-8")
    else:
        external.write_text("untouched\n", encoding="utf-8")
    link = target / managed_path
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(external, target_is_directory=is_directory)

    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*") if not path.is_symlink())
    with pytest.raises(OSError, match="symlink"):
        _snapshot_transaction_paths(target)
    after = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*") if not path.is_symlink())

    assert after == before
    sentinel = external / "sentinel.txt" if is_directory else external
    assert sentinel.read_text(encoding="utf-8") == "untouched\n"


# ---------------------------------------------------------------------------
# PRD-INFRA-190 FR05 (dirty bundle) and NFR01 (git unavailable)
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    import subprocess

    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True
    )


def _repo_with_vendored_bundle(tmp_path: Path) -> tuple[Path, Path]:
    """An installed repo whose bundle lives INSIDE its work tree (the editable-install case)."""
    from trw_mcp.bootstrap import _DATA_DIR, init_project

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    assert not init_project(repo, ide="claude-code")["errors"]
    bundle = repo / "vendor" / "data"
    shutil.copytree(_DATA_DIR, bundle)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "installed")
    return repo, bundle


def _surface_state(repo: Path) -> dict[str, bytes]:
    from trw_mcp.bootstrap._update_transaction import _surface_files

    return {rel: (repo / rel).read_bytes() for rel in _surface_files(repo)}


def test_dirty_bundle_inside_the_work_tree_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    from trw_mcp.bootstrap import update_project

    repo, bundle = _repo_with_vendored_bundle(tmp_path)
    hook = bundle / "hooks" / "session-start.sh"
    hook.write_text(hook.read_text(encoding="utf-8") + "\n# another lane's uncommitted edit\n", encoding="utf-8")
    before = _surface_state(repo)

    result = update_project(repo, data_dir=bundle)

    assert len(result["errors"]) == 1
    assert "vendor/data/hooks/session-start.sh" in result["errors"][0]
    assert _surface_state(repo) == before


def test_allow_dirty_bundle_projects_the_uncommitted_bundle(tmp_path: Path) -> None:
    from trw_mcp.bootstrap import update_project

    repo, bundle = _repo_with_vendored_bundle(tmp_path)
    hook = bundle / "hooks" / "session-start.sh"
    hook.write_text(hook.read_text(encoding="utf-8") + "\n# deliberate local change\n", encoding="utf-8")

    result = update_project(repo, data_dir=bundle, allow_dirty_bundle=True)

    assert not result["errors"], result["errors"]
    assert (repo / ".claude" / "hooks" / "session-start.sh").read_bytes() == hook.read_bytes()


def test_a_bundle_outside_the_work_tree_is_not_checked(tmp_path: Path) -> None:
    from trw_mcp.bootstrap import _DATA_DIR, update_project

    repo, _ = _repo_with_vendored_bundle(tmp_path)
    outside = tmp_path / "wheel-data"
    shutil.copytree(_DATA_DIR, outside)
    hook = outside / "hooks" / "session-start.sh"
    hook.write_text(hook.read_text(encoding="utf-8") + "\n# N+1\n", encoding="utf-8")

    result = update_project(repo, data_dir=outside)

    assert not result["errors"], result["errors"]
    assert ".claude/hooks/session-start.sh" in result["updated"]


def test_git_unavailable_warns_and_falls_back_to_the_manifest_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR01: a missing git binary is reported as unknown; the update still completes."""
    import subprocess

    from trw_mcp.bootstrap import update_project

    repo, _ = _repo_with_vendored_bundle(tmp_path)
    real_run = subprocess.run

    def no_git(args: list[str], *a: object, **kw: object) -> object:
        if args[:1] == ["git"] and "status" in args:
            raise FileNotFoundError("git")
        return real_run(args, *a, **kw)  # type: ignore[call-overload]

    monkeypatch.setattr(subprocess, "run", no_git)
    (repo / ".trw" / "frameworks" / "FRAMEWORK.md").write_text("stale\n", encoding="utf-8")

    result = update_project(repo)

    assert not result["errors"], result["errors"]
    assert any(w.startswith("git status unavailable") for w in result["warnings"])
    assert ".trw/frameworks/FRAMEWORK.md" in result["updated"]
