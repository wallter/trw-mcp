"""update-project names its target context-locally, never through ``os.environ`` (PRD-CORE-305-FR07, B71-118).

``_apply_update`` used to set process-wide ``TRW_PROJECT_ROOT`` for its whole
writer phase, so an overlapping in-process operation (an MCP request thread, a
second install) resolved the update's target as its own project. The target is
now bound with ``installing_into`` -- the context variable ``init_project``
already uses -- which ``resolve_project_root`` consults first.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import pytest

from tests._path_isolation import REAL_RESOLVE_PROJECT_ROOT, REAL_RESOLVE_TRW_DIR
from trw_mcp.bootstrap import _ide_targets_finalize, _update_project
from trw_mcp.state import _paths


def resolve_project_root() -> Path:
    """What BOTH the genuine resolver and the suite's isolated stand-in answer (they must agree)."""
    genuine, isolated = REAL_RESOLVE_PROJECT_ROOT(), _paths.resolve_project_root()
    assert genuine == isolated, f"genuine resolver {genuine} and test stand-in {isolated} disagree"
    return genuine


def resolve_trw_dir() -> Path:
    return REAL_RESOLVE_TRW_DIR()


def _apply(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Run the real writer-phase frame with the post phases stubbed out (the core phase is each test's probe)."""
    monkeypatch.setattr(_update_project, "_run_post_update_phases", lambda *_a, **_k: None)
    result: dict[str, list[str]] = {"errors": [], "warnings": [], "preserved": [], "updated": [], "created": []}
    _update_project._apply_update(root, root, result, ide=None, on_progress=None, dirty=None, reprovision=None)
    return result


def _projects(tmp_path: Path) -> tuple[Path, Path]:
    target = tmp_path / "update-target"
    other = tmp_path / "server-project"
    for project in (target, other):
        (project / ".trw").mkdir(parents=True)
    return target.resolve(), other.resolve()


def test_overlapping_thread_keeps_its_own_project_during_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another thread resolves ITS project mid-update; the env is never touched; update's code sees the target."""
    target, other = _projects(tmp_path)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(other))
    seen: dict[str, Any] = {}

    def _writer_phase(root: Path, *_args: object, **_kwargs: object) -> None:
        seen["inside_root"] = resolve_project_root()
        seen["inside_trw_dir"] = resolve_trw_dir()
        seen["inside_env"] = os.environ.get("TRW_PROJECT_ROOT")

        def _overlapping_request() -> None:
            seen["other_thread_root"] = resolve_project_root()
            seen["other_thread_env"] = os.environ.get("TRW_PROJECT_ROOT")

        worker = threading.Thread(target=_overlapping_request)
        worker.start()
        worker.join()

    monkeypatch.setattr(_update_project, "_run_core_update_phases", _writer_phase)
    _apply(target, monkeypatch)

    assert seen["other_thread_root"] == other, "an overlapping thread resolved the update's target"
    assert seen["other_thread_env"] == str(other)
    assert seen["inside_env"] == str(other), "update-project mutated process-wide TRW_PROJECT_ROOT"
    assert seen["inside_root"] == target, "update's own renderers must still resolve the target"
    assert seen["inside_trw_dir"] == target / ".trw"
    assert os.environ.get("TRW_PROJECT_ROOT") == str(other)
    assert REAL_RESOLVE_PROJECT_ROOT() == other, "the binding must not outlive the update"


def test_env_unset_is_left_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, _other = _projects(tmp_path)
    monkeypatch.delenv("TRW_PROJECT_ROOT", raising=False)
    seen: dict[str, Any] = {}

    def _writer_phase(root: Path, *_args: object, **_kwargs: object) -> None:
        seen["env_present"] = "TRW_PROJECT_ROOT" in os.environ
        seen["inside_root"] = resolve_project_root()

    monkeypatch.setattr(_update_project, "_run_core_update_phases", _writer_phase)
    _apply(target, monkeypatch)

    assert seen == {"env_present": False, "inside_root": target}
    assert "TRW_PROJECT_ROOT" not in os.environ


def test_claude_md_sync_resolves_the_update_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The instruction sync resolves the update target even with TRW_PROJECT_ROOT naming another project."""
    target, other = _projects(tmp_path)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(other))
    seen: dict[str, Path] = {}

    def _fake_sync(**_kwargs: object) -> dict[str, int]:
        seen["sync_root"] = resolve_project_root()
        return {"learnings_promoted": 0}

    monkeypatch.setattr("trw_mcp.state.claude_md.execute_claude_md_sync", _fake_sync)

    def _writer_phase(root: Path, _data: Path, result: dict[str, list[str]], *_a: object, **_k: object) -> None:
        _ide_targets_finalize._run_claude_md_sync(root, result)

    monkeypatch.setattr(_update_project, "_run_core_update_phases", _writer_phase)
    _apply(target, monkeypatch)

    assert seen["sync_root"] == target, "the CLAUDE.md sync resolved a project other than the update's"


def test_config_loaded_during_update_stays_in_the_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A config first loaded mid-update is the target's there, and never becomes the process-wide singleton."""
    from trw_mcp.models.config import get_config, reload_config

    target, other = _projects(tmp_path)
    (target / ".trw" / "config.yaml").write_text("assess_enabled: true\n", encoding="utf-8")
    (other / ".trw" / "config.yaml").write_text("assess_enabled: false\n", encoding="utf-8")
    for var in ("TRW_ASSESS_ENABLED", "TRW_JEV_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(other))
    reload_config()
    seen: dict[str, bool] = {}

    def _writer_phase(root: Path, *_args: object, **_kwargs: object) -> None:
        reload_config()  # what the instruction sync / auto-maintenance do before reading the target's config
        seen["inside"] = get_config().assess_enabled

        def _overlapping_request() -> None:
            seen["other_thread"] = get_config().assess_enabled

        worker = threading.Thread(target=_overlapping_request)
        worker.start()
        worker.join()

    monkeypatch.setattr(_update_project, "_run_core_update_phases", _writer_phase)
    _apply(target, monkeypatch)

    assert seen["inside"] is True, "update's own code must read the target's config"
    assert seen["other_thread"] is False, "an overlapping thread read the update's config"
    assert get_config().assess_enabled is False, "the update's config leaked into the process-wide singleton"
