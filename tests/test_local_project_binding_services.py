"""Local service defaults follow the temporary project-root binding."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.state._project_root_binding import project_bound


def test_local_init_learn_and_recall_use_bound_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.services import local_surface_service, orchestration_service

    project = tmp_path / "project"
    project.mkdir()
    expected = project / ".trw"
    monkeypatch.setattr("trw_mcp.state._paths_permissions.harden_trw_tree", lambda *_a, **_kw: None)
    monkeypatch.setattr("trw_mcp.state._paths_permissions.harden_dir_mode", lambda *_a, **_kw: None)
    with project_bound(project):
        initialized = orchestration_service.scaffold_run_directory("task", run_id="fixed")
        assert Path(initialized["run_path"]).is_relative_to(expected / "runs")

        learned: dict[str, Any] = {}
        monkeypatch.setattr("trw_mcp.tools._learn_impl.execute_learn", lambda **kw: learned.update(kw) or {})
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: object())
        orchestration_service.write_local_learning("summary", "detail")
        assert learned["trw_dir"] == expected

        recalled: dict[str, Any] = {}
        monkeypatch.setattr(
            "trw_mcp.tools._recall_impl.execute_recall",
            lambda *args, **_kw: recalled.update({"trw_dir": args[1]}) or {"learnings": []},
        )
        monkeypatch.setattr("trw_mcp.sync._fresh_pull.finish_inflight", lambda: False)
        local_surface_service.run_local_recall("query")
        assert recalled["trw_dir"] == expected
