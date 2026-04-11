from __future__ import annotations

import inspect
from pathlib import Path

import trw_mcp.state.ceremony_progress as ceremony_progress
from trw_mcp.tools import (
    _ceremony_helpers,
    _learn_impl,
    _legacy_ceremony_nudge,
    _orchestration_lifecycle,
    _recall_impl,
    _session_recall_helpers,
    ceremony,
    orchestration,
    requirements,
    review,
)
from trw_mcp.tools._ceremony_status import append_ceremony_status


def test_live_tool_modules_do_not_depend_on_legacy_nudge_wiring() -> None:
    modules = (
        ceremony,
        orchestration,
        requirements,
        review,
        _ceremony_helpers,
        _learn_impl,
        _orchestration_lifecycle,
        _recall_impl,
        _session_recall_helpers,
    )

    for module in modules:
        source = inspect.getsource(module)
        assert "state.ceremony_nudge" not in source
        assert "append_ceremony_nudge" not in source
        assert "NudgeContext" not in source


def test_live_ceremony_progress_is_isolated_from_legacy_nudge_backend() -> None:
    progress_source = inspect.getsource(ceremony_progress)
    assert "_nudge_state" not in progress_source
    assert "_ceremony_progress_state" in progress_source


def test_legacy_nudge_surface_is_quarantined_in_dedicated_module() -> None:
    source = inspect.getsource(_legacy_ceremony_nudge)
    assert "append_ceremony_status" in source
    assert "Compatibility adapters" in source


def test_append_ceremony_status_adds_summary_when_state_exists(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)

    result = append_ceremony_status({"status": "ok"}, trw_dir)

    assert result["status"] == "ok"
    assert isinstance(result["ceremony_status"], str)
    assert "checkpoints=0" in result["ceremony_status"]
    assert "learnings=0" in result["ceremony_status"]
