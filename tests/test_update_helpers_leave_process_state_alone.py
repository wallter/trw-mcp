"""An install never changes process-wide state other threads read (PRD-CORE-305-FR07, sol round 1).

Three leaks the context-local binding alone did not close:

* ``get_config()`` returned an already-loaded process singleton (the server's
  config) inside an install, so the install read the SERVER's settings;
* ``reload_config()`` inside an install cleared that singleton for every thread;
* the CLAUDE.md sync and auto-maintenance helpers ``chdir``-ed the whole process
  into the target, so an overlapping thread resolved the target through its cwd.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import pytest

from tests._path_isolation import REAL_RESOLVE_PROJECT_ROOT
from trw_mcp.bootstrap import _ide_targets_finalize, _update_external, _update_project
from trw_mcp.models import config as config_pkg
from trw_mcp.models.config import TRWConfig, _loader, get_config, reload_config
from trw_mcp.state._project_root_binding import installing_into


def _projects(tmp_path: Path) -> tuple[Path, Path]:
    target, server = (tmp_path / "update-target").resolve(), (tmp_path / "server-project").resolve()
    for project, assess in ((target, "true"), (server, "false")):
        (project / ".trw").mkdir(parents=True)
        (project / ".trw" / "config.yaml").write_text(f"assess_enabled: {assess}\n", encoding="utf-8")
    return target, server


@pytest.fixture
def server_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, TRWConfig]:
    """A process whose own project is *server* (by cwd, no TRW_PROJECT_ROOT) with its config already loaded."""
    target, server = _projects(tmp_path)
    for var in ("TRW_PROJECT_ROOT", "TRW_ASSESS_ENABLED", "TRW_JEV_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(server)
    loaded = TRWConfig(assess_enabled=False)
    reload_config(loaded)
    return target, server, loaded


def _overlapping(probe: dict[str, Any]) -> None:
    """What a concurrent request thread (a fresh context) sees right now."""

    def _request() -> None:
        probe["cwd"] = Path(os.getcwd()).resolve()
        probe["root"] = REAL_RESOLVE_PROJECT_ROOT()
        probe["config"] = get_config()

    worker = threading.Thread(target=_request)
    worker.start()
    worker.join()


def _apply(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_update_project, "_run_post_update_phases", lambda *_a, **_k: None)
    result: dict[str, list[str]] = {"errors": [], "warnings": [], "preserved": [], "updated": [], "created": []}
    _update_project._apply_update(root, root, result, ide=None, on_progress=None, dirty=None, reprovision=None)


def test_install_reads_the_targets_config_even_when_the_server_config_is_loaded(
    server_process: tuple[Path, Path, TRWConfig],
) -> None:
    target, _server, loaded = server_process
    with installing_into(target):
        inside = get_config()
    assert inside.assess_enabled is True, "the install read the server's already-loaded config"
    assert get_config() is loaded


def test_reset_inside_an_install_leaves_the_process_singleton_alone(
    server_process: tuple[Path, Path, TRWConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    target, _server, loaded = server_process
    probe: dict[str, Any] = {}

    def _writer_phase(*_a: object, **_k: object) -> None:
        reload_config()  # what the update helpers do before reading the target's config
        _overlapping(probe)

    monkeypatch.setattr(_update_project, "_run_core_update_phases", _writer_phase)
    _apply(target, monkeypatch)

    assert probe["config"] is loaded, "an overlapping thread lost the server's config during the update"
    assert _loader._singleton is loaded, "the update cleared the process-wide config"


def test_claude_md_sync_never_moves_the_process_cwd(
    server_process: tuple[Path, Path, TRWConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    target, server, loaded = server_process
    probe: dict[str, Any] = {}
    inside: dict[str, Any] = {}

    def _fake_sync(**kwargs: Any) -> dict[str, int]:
        inside["root"] = REAL_RESOLVE_PROJECT_ROOT()
        inside["assess"] = kwargs["config"].assess_enabled
        _overlapping(probe)
        return {"learnings_promoted": 0}

    monkeypatch.setattr("trw_mcp.state.claude_md.execute_claude_md_sync", _fake_sync)
    result: dict[str, list[str]] = {"updated": [], "warnings": []}
    _ide_targets_finalize._run_claude_md_sync(target, result)

    assert probe["cwd"] == server, "the CLAUDE.md sync moved this process's cwd into the target"
    assert probe["root"] == server, "an overlapping thread resolved the update target"
    assert probe["config"] is loaded
    assert _loader._singleton is loaded, "the sync reset the process-wide config"
    assert inside == {"root": target, "assess": True}, "the sync must still resolve the target and read its config"
    assert Path(os.getcwd()).resolve() == server


def test_auto_maintenance_never_moves_the_process_cwd(
    server_process: tuple[Path, Path, TRWConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    target, server, loaded = server_process
    probe: dict[str, Any] = {}
    inside: dict[str, Any] = {}
    real_get_config = config_pkg.get_config

    def _probing_get_config() -> TRWConfig:
        inside["root"] = REAL_RESOLVE_PROJECT_ROOT()
        _overlapping(probe)
        config = real_get_config()
        inside["assess"] = config.assess_enabled
        return config

    monkeypatch.setattr(config_pkg, "get_config", _probing_get_config)
    result: dict[str, list[str]] = {"updated": [], "warnings": []}
    _update_external._run_auto_maintenance(target, result)

    assert probe["cwd"] == server, "auto-maintenance moved this process's cwd into the target"
    assert probe["root"] == server, "an overlapping thread resolved the update target"
    assert probe["config"] is loaded
    assert _loader._singleton is loaded, "auto-maintenance reset the process-wide config"
    assert inside == {"root": target, "assess": True}, "auto-maintenance must still read the target's config"
