"""An interrupted update-project must put parked symlinks back (Codex review 2, P1).

``park_surface_links`` unlinks every surface symlink before the writers run. A
``KeyboardInterrupt`` is a ``BaseException``: it skips ``except Exception``,
records no error, and used to skip the rollback — while the snapshot holding the
only copy of each link was deleted unconditionally.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from trw_mcp.bootstrap import _update_project


def _project_with_link(tmp_path: Path) -> tuple[Path, Path, Path]:
    external = tmp_path / "outside-settings.json"
    external.write_text("{}\n", encoding="utf-8")
    root = tmp_path / "project"
    (root / ".claude").mkdir(parents=True)
    link = root / ".claude" / "settings.json"
    link.symlink_to(external)
    return root, link, external


def _interrupt(*_args: object, **_kwargs: object) -> None:
    raise KeyboardInterrupt


def _apply(root: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {"errors": [], "warnings": [], "preserved": []}
    _update_project._apply_update(root, root, result, ide=None, on_progress=None, dirty=None, reprovision=None)
    return result


def test_interrupt_after_parking_restores_the_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, link, external = _project_with_link(tmp_path)
    snapshots: list[Path] = []
    real_snapshot = _update_project._snapshot_transaction_paths

    def _recording_snapshot(target: Path) -> Path:
        snapshots.append(real_snapshot(target))
        return snapshots[-1]

    monkeypatch.setattr(_update_project, "_snapshot_transaction_paths", _recording_snapshot)
    monkeypatch.setattr(_update_project, "_run_core_update_phases", _interrupt)

    with pytest.raises(KeyboardInterrupt):
        _apply(root)

    assert link.is_symlink(), "the parked symlink was lost on interrupt"
    assert link.readlink() == external
    assert external.read_bytes() == b"{}\n"
    assert not snapshots[0].exists(), "a successful rollback leaves no snapshot behind"


def test_failed_restore_keeps_the_snapshot_and_names_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _link, external = _project_with_link(tmp_path)

    def _broken_rollback(_target: Path, _snapshot: Path, _result: dict[str, list[str]]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(_update_project, "_run_core_update_phases", _interrupt)
    monkeypatch.setattr(_update_project, "_rollback", _broken_rollback)  # the rollback the interrupt really drives
    result: dict[str, list[str]] = {"errors": [], "warnings": [], "preserved": []}

    with pytest.raises(KeyboardInterrupt):
        _update_project._apply_update(root, root, result, ide=None, on_progress=None, dirty=None, reprovision=None)

    kept = [e for e in result["errors"] if "recovery copy kept at " in e]
    assert kept, "the failed restore must report where the recovery data is"
    snapshot = Path(kept[0].rsplit("recovery copy kept at ", 1)[1])
    try:
        assert (snapshot / ".claude" / "settings.json").is_symlink(), "the snapshot holding the link was deleted"
        assert (snapshot / ".claude" / "settings.json").readlink() == external
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)


def test_the_interrupt_test_depends_on_the_rollback_it_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FB-01-KI1-RACE r5b pin: with the rollback stubbed to do nothing, the parked link is NOT restored. So
    test_interrupt_after_parking_restores_the_link is proven to exercise _rollback, not pass vacuously."""
    root, link, _external = _project_with_link(tmp_path)
    monkeypatch.setattr(_update_project, "_run_core_update_phases", _interrupt)
    monkeypatch.setattr(_update_project, "_rollback", lambda *_a, **_k: None)

    with pytest.raises(KeyboardInterrupt):
        _apply(root)

    assert not link.is_symlink(), "a do-nothing rollback must leave the parked link missing"
