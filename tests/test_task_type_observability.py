"""Integration tests for PRD-CORE-184-FR05 — task_type observability.

Verifies task_type is surfaced in run.yaml, events.jsonl (task_type_detected),
trw_status, and the RunReport model after trw_init.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import extract_tool_fn, make_test_server
from trw_mcp.state.persistence import FileStateReader


@pytest.fixture
def init_fn(tmp_project: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """trw_init bound to tmp_project (mirrors conftest build_check_invoke wiring)."""
    import trw_mcp.state._paths as paths_mod

    monkeypatch.chdir(tmp_project)
    monkeypatch.setattr(paths_mod, "resolve_project_root", lambda: tmp_project)
    return extract_tool_fn(make_test_server("orchestration"), "trw_init")


def _run_yaml(reader: FileStateReader, run_path: str) -> dict[str, object]:
    return reader.read_yaml(Path(run_path) / "meta" / "run.yaml")


def test_trw_init_writes_task_type_to_run_yaml(init_fn, reader: FileStateReader) -> None:  # type: ignore[no-untyped-def]
    result = init_fn(task_name="implement-auth-feature", run_type="implementation")
    data = _run_yaml(reader, result["run_path"])
    assert data.get("task_type") == "coding"
    assert data.get("recall_policy") == "similarity"


def test_trw_init_detects_rca_from_keyword(init_fn, reader: FileStateReader) -> None:  # type: ignore[no-untyped-def]
    result = init_fn(task_name="debug-stacktrace-leak", run_type="implementation")
    data = _run_yaml(reader, result["run_path"])
    assert data.get("task_type") == "rca"
    assert data.get("recall_policy") == "failure_pattern"


def test_trw_init_emits_task_type_detected_event(init_fn, reader: FileStateReader) -> None:  # type: ignore[no-untyped-def]
    result = init_fn(task_name="research-competitive", run_type="research")
    events = reader.read_jsonl(Path(result["run_path"]) / "meta" / "events.jsonl")
    detected = [e for e in events if e.get("event") == "task_type_detected"]
    assert len(detected) == 1
    assert detected[0]["task_type"] == "research"
    assert detected[0]["detection_method"]
    assert detected[0]["rationale"]


def test_trw_init_result_includes_task_type(init_fn) -> None:  # type: ignore[no-untyped-def]
    result = init_fn(task_name="add-new-endpoint", run_type="implementation")
    assert result["task_type"] == "coding"


def test_trw_init_unknown_when_no_signals(init_fn, reader: FileStateReader) -> None:  # type: ignore[no-untyped-def]
    result = init_fn(task_name="zzz", run_type="custom_unmapped_run_type")
    data = _run_yaml(reader, result["run_path"])
    assert data.get("task_type") == "unknown"


def test_trw_status_includes_task_type(init_fn, reader: FileStateReader) -> None:  # type: ignore[no-untyped-def]
    init_result = init_fn(task_name="fix-the-bug", run_type="implementation")
    status_fn = extract_tool_fn(make_test_server("orchestration"), "trw_status")
    status = status_fn(run_path=init_result["run_path"])
    assert status["task_type"] == "coding"


def test_trw_status_surfaces_task_type_nudge_weights(init_fn) -> None:  # type: ignore[no-untyped-def]
    """FR04 AC: trw_status exposes effective nudge_pool_weights that shift by task type."""
    status_fn = extract_tool_fn(make_test_server("orchestration"), "trw_status")

    coding = init_fn(task_name="implement-feature-x", run_type="implementation")
    coding_status = status_fn(run_path=coding["run_path"])
    research = init_fn(task_name="research-the-landscape", run_type="research")
    research_status = status_fn(run_path=research["run_path"])

    cw = coding_status["nudge_pool_weights"]
    rw = research_status["nudge_pool_weights"]
    # FR04 AC: ceremony weight shifts >= 10pp between coding and research.
    assert cw["ceremony"] - rw["ceremony"] >= 10
    assert research_status["recall_policy"] == "breadth_first"


def test_trw_report_model_has_task_type_default() -> None:
    from trw_mcp.models.report import RunReport

    report = RunReport(run_id="r", task="t", status="active", phase="research", generated_at="now")
    assert report.task_type == "unknown"
    report2 = RunReport(run_id="r", task="t", status="active", phase="research", generated_at="now", task_type="coding")
    assert report2.task_type == "coding"
