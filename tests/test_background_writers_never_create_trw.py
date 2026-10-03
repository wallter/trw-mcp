"""A TRW background writer never creates ``.trw`` from nothing (UNINSTALL-DISTILL-RACE, C1 re-check 21:12Z).

Uninstall removes ``.trw``. A post-commit worker or a detached distill build that starts afterwards used to run
``mkdir(parents=True)`` on its lock's directory, which recreated ``.trw/runtime`` (or ``.trw/distill``) after uninstall
had reported "Removed". Each writer now creates only the lock's own directory under a ``.trw`` that already exists;
with no ``.trw`` it does nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trw_mcp.server._uninstall_corpus import remove_trw_dir
from trw_mcp.tools import _distill_spawn, _post_commit, _post_commit_distill

pytestmark = pytest.mark.integration


def _display(path: Path, _target: Path) -> str:
    return str(path)


def test_a_delayed_post_commit_worker_after_the_root_removal_creates_nothing(tmp_path: Path) -> None:
    """C1's exact repro: the real uninstall removes .trw, then the real lock helper runs."""
    trw = tmp_path / ".trw"
    (trw / "runtime").mkdir(parents=True)

    assert remove_trw_dir(trw, tmp_path, _display) == (1, 0)
    assert not trw.exists()

    state, fd = _post_commit._acquire_lock(trw / "runtime" / "post-commit.lock", "deadbeef")

    assert (state, fd) == ("uninstalled", -1)
    assert not trw.exists(), "a background writer recreated .trw after uninstall reported it removed"


def test_run_post_commit_with_no_trw_does_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_post_commit, "_sweep_trw_dir", lambda _repo: tmp_path / ".trw")

    receipt = _post_commit.run_post_commit(tmp_path, {})

    assert receipt.lock_state == "uninstalled"
    assert not (tmp_path / ".trw").exists() and not os.listdir(tmp_path), os.listdir(tmp_path)


def test_the_incremental_run_does_not_create_trw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_post_commit_distill, "resolve_distill_cli", lambda _env: "/fake/trw-distill")

    status = _post_commit_distill.spawn_incremental_run(tmp_path, {"PATH": "/usr/bin"}, enabled=True)

    assert status == "lock_unavailable"
    assert not (tmp_path / ".trw").exists()


def _cache_dir(repo: Path) -> Path:
    """The shared sidecar cache the rebuild writes; its lock sits beside it."""
    return repo / ".trw" / "distill" / "cache"


def test_the_sidecar_rebuild_lock_does_not_create_trw(tmp_path: Path) -> None:
    assert _distill_spawn._take_rebuild_lock(_cache_dir(tmp_path)) == _distill_spawn._UNINSTALLED
    assert not (tmp_path / ".trw").exists()


def test_with_a_trw_present_the_writers_still_create_their_own_directory(tmp_path: Path) -> None:
    (tmp_path / ".trw").mkdir()

    state, fd = _post_commit._acquire_lock(tmp_path / ".trw" / "runtime" / "post-commit.lock", "deadbeef")
    rebuild = _distill_spawn._take_rebuild_lock(_cache_dir(tmp_path))

    assert state == "acquired" and (tmp_path / ".trw" / "runtime").is_dir()
    assert rebuild >= 0 and (tmp_path / ".trw" / "distill").is_dir()
    os.close(fd)
    os.close(rebuild)


def test_without_flock_a_delayed_post_commit_worker_still_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows has no fcntl: the absent-.trw check runs before the platform branch, so the sweep never recreates it."""
    monkeypatch.setattr(_post_commit, "fcntl", None)
    trw = tmp_path / ".trw"
    (trw / "runtime").mkdir(parents=True)
    assert remove_trw_dir(trw, tmp_path, _display) == (1, 0)

    assert _post_commit._acquire_lock(trw / "runtime" / "post-commit.lock", "deadbeef") == ("uninstalled", -1)
    assert not trw.exists()


def test_the_rebuild_request_is_refused_when_trw_is_gone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools._distill_spawn import SpawnPorts, request_rebuild_if_due

    monkeypatch.setenv("TRW_HINT_SIDECAR_AUTO_REFRESH_ENABLED", "true")  # the conftest pins it off

    class _Lookup:
        status = "sidecar_missing"
        ancestor = None
        repo_root = tmp_path

    class _NoPopen:
        def __call__(self, *a: object, **k: object) -> None:
            raise AssertionError("a build was spawned with no .trw")

    outcome = request_rebuild_if_due(
        _Lookup(),  # type: ignore[arg-type]
        cache_dir=None,
        trigger="post-commit",
        source_env={},
        ports=SpawnPorts(popen=_NoPopen(), which=lambda *_a: "/x", clock=lambda: 0.0),
    )

    assert outcome.status == "no_trw_dir"
    assert not (tmp_path / ".trw").exists()


def test_trw_removed_between_the_check_and_the_open_is_uninstalled_not_unlocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C1's remove-between-check-and-lock repro for the post-commit worker: the open fails, and that is NOT 'unlocked'."""
    import shutil

    from trw_mcp.state import _below_trw

    trw = tmp_path / ".trw"
    trw.mkdir()
    real = _below_trw.ensure_dir_below_trw

    def check_then_uninstall(path: Path, *, root: Path | None = None) -> bool:
        ok = real(path, root=root)
        shutil.rmtree(trw)  # uninstall lands right after the check passed
        return ok

    monkeypatch.setattr("trw_mcp.state._below_trw.ensure_dir_below_trw", check_then_uninstall)

    assert _post_commit._acquire_lock(trw / "runtime" / "post-commit.lock", "deadbeef") == ("uninstalled", -1)
    assert not trw.exists()


def test_the_helper_never_recreates_a_root_that_vanishes_mid_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state._below_trw import ensure_dir_below_trw

    trw = tmp_path / ".trw"
    trw.mkdir()
    real_mkdir = Path.mkdir
    calls: list[Path] = []

    def vanish_after_first_level(self: Path, *args: object, **kwargs: object) -> None:
        calls.append(self)
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]
        if len(calls) == 1:
            import shutil

            shutil.rmtree(trw)  # the root disappears after the first level was made

    monkeypatch.setattr(Path, "mkdir", vanish_after_first_level)

    assert ensure_dir_below_trw(trw / "distill" / "map-cache") is False
    assert not trw.exists()
