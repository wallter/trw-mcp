"""SCALE-001 FR13 consumer wiring — trw_init runs the Scout on the real path.

Proves the orchestration anchor (``tools/orchestration.py::trw_init``) actually
READS a scaling decision: it invokes the Scout, writes
``meta/session_profile.yaml`` (FR03), and honors a ``--planning-mode`` override
(FR13). Then the live H2 resolver consumes that overlay end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from tests.conftest import extract_tool_fn, make_test_server


@pytest.fixture
def _init_fn(tmp_project: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """trw_init bound to a temp project root."""
    monkeypatch.chdir(tmp_project)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_project)
    return extract_tool_fn(make_test_server("orchestration"), "trw_init")


def _session_profile_path(result: dict[str, str]) -> Path:
    return Path(result["run_path"]) / "meta" / "session_profile.yaml"


def test_init_writes_session_profile_and_surfaces_mode(_init_fn, tmp_project: Path) -> None:  # type: ignore[no-untyped-def]
    """FR01/FR03: trw_init runs the Scout and writes the overlay on the real path."""
    result = _init_fn(task_name="typo-fix", objective="fix a typo")
    # Scout decision surfaced on the init result.
    assert "planning_mode" in result
    assert "scout_ceremony_tier" in result
    # Overlay written to the run dir (the H2 session-layer contract path).
    path = _session_profile_path(result)
    assert path.exists()
    data = YAML(typ="safe").load(path.read_text())
    assert data["ceremony_tier"] in ("MINIMAL", "STANDARD", "COMPREHENSIVE")
    assert "planning_mode=" in data["rationale"]


def test_init_planning_mode_override_is_honored(_init_fn, tmp_project: Path) -> None:  # type: ignore[no-untyped-def]
    """FR13: --planning-mode override forces the written overlay tier."""
    result = _init_fn(
        task_name="forced-task",
        objective="force comprehensive",
        planning_mode="TRIANGULATED",
    )
    assert result["planning_mode"] == "TRIANGULATED"
    assert result["scout_ceremony_tier"] == "COMPREHENSIVE"
    data = YAML(typ="safe").load(_session_profile_path(result).read_text())
    assert data["ceremony_tier"] == "COMPREHENSIVE"
    assert "planning_mode=2" in data["rationale"]
    assert "user_override" in data["rationale"]


def test_init_overlay_consumed_by_h2_resolver(_init_fn, tmp_project: Path) -> None:  # type: ignore[no-untyped-def]
    """End-to-end: init-written overlay supersedes in the live H2 resolver (FR03)."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.profile import resolve_session_profile

    result = _init_fn(task_name="e2e-task", objective="x", planning_mode="DIRECT")
    run_dir = Path(result["run_path"])
    config = TRWConfig(trw_dir=str(tmp_project / ".trw"))
    resolved = resolve_session_profile(config, run_dir=run_dir)
    assert resolved.profile.ceremony_tier == "MINIMAL"
    assert "session" in resolved.layers_applied


def test_init_scout_kill_switch_skips_overlay(_init_fn, tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR14 kill switch: scout_enabled=False writes no overlay, no planning_mode."""
    monkeypatch.setenv("TRW_SCOUT_ENABLED", "false")
    from trw_mcp.models.config._loader import _reset_config

    _reset_config()
    result = _init_fn(task_name="no-scout", objective="x")
    assert "planning_mode" not in result
    assert not _session_profile_path(result).exists()
    _reset_config()


def test_scout_kill_switch_via_config_object(tmp_project: Path) -> None:
    """FR14 round-2 (S1-F03): the config field alone (no env var) gates the Scout.

    Proves ``run_scout_for_init`` short-circuits to ``None`` purely from
    ``TRWConfig(scout_enabled=False)`` — the kill switch is a config-object
    invariant, not an env-var artifact. No overlay is written and the result
    is never mutated.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._orchestration_scaling import run_scout_for_init

    config = TRWConfig(scout_enabled=False)
    run_root = tmp_project / "runs" / "task" / "run-killswitch"
    (run_root / "meta").mkdir(parents=True)
    result: dict[str, str] = {}

    classification = run_scout_for_init(
        config,
        task_name="x",
        objective="y",
        prd_scope=None,
        run_root=run_root,
        project_root=tmp_project,
        trw_dir=tmp_project / ".trw",
        planning_mode=None,
        result=result,
    )

    assert classification is None
    assert result == {}
    assert not (run_root / "meta" / "session_profile.yaml").exists()
