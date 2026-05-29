"""PRD-CORE-165 FR-01 — directive + context-anchor through pre-compaction.

The pre-compact checkpoint tool must PERSIST a caller-supplied directive +
context-anchor into the pre-compact state, and the recovery readback
(``_get_run_status``, consumed by ``trw_session_start`` / ``trw_status``) must
SURFACE them. Omitting them stays backward-compatible (no keys emitted).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server
from trw_mcp.tools._ceremony_runtime_helpers import _get_run_status
from trw_mcp.tools.checkpoint import _write_compact_state


def _make_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run" / "abc123"
    (run_dir / "meta").mkdir(parents=True)
    (tmp_path / ".trw" / "context").mkdir(parents=True)
    return run_dir


def _read_state(tmp_path: Path) -> dict[str, object]:
    state_file = tmp_path / ".trw" / "context" / "pre_compact_state.json"
    data: dict[str, object] = json.loads(state_file.read_text(encoding="utf-8"))
    return data


def test_directive_and_anchor_persisted(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path)
    _write_compact_state(
        tmp_path,
        run_dir,
        run_dir / "meta" / "events.jsonl",
        prd_scope=["PRD-CORE-165"],
        phase="implement",
        file_ownership_path="",
        failing_tests=[],
        ceremony_state={},
        directive="finish FR-01 then run mypy",
        context_anchor="mid-flight: tests written, build pending",
    )
    state = _read_state(tmp_path)
    assert state["directive"] == "finish FR-01 then run mypy"
    assert state["context_anchor"] == "mid-flight: tests written, build pending"


def test_omitted_directive_not_persisted(tmp_path: Path) -> None:
    """Backward-compat: omitting the new params leaves the keys absent."""
    run_dir = _make_run(tmp_path)
    _write_compact_state(
        tmp_path,
        run_dir,
        run_dir / "meta" / "events.jsonl",
        prd_scope=[],
        phase="research",
        file_ownership_path="",
        failing_tests=[],
        ceremony_state={},
    )
    state = _read_state(tmp_path)
    assert "directive" not in state
    assert "context_anchor" not in state
    # The pre-existing real-checkpoint field (FR-02) is still always present.
    assert "last_checkpoint" in state


def test_recovery_surfaces_directive_and_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _make_run(tmp_path)
    run_yaml = run_dir / "meta" / "run.yaml"
    run_yaml.write_text("phase: implement\nstatus: active\ntask: fr01\n", encoding="utf-8")
    _write_compact_state(
        tmp_path,
        run_dir,
        run_dir / "meta" / "events.jsonl",
        prd_scope=[],
        phase="implement",
        file_ownership_path="",
        failing_tests=[],
        ceremony_state={},
        directive="resume the FR-01 wiring",
        context_anchor="readback added, verifying",
    )
    # The readback resolves the state file via resolve_trw_dir().
    monkeypatch.setattr(
        "trw_mcp.state._paths.resolve_trw_dir",
        lambda: tmp_path / ".trw",
    )
    status = _get_run_status(run_dir)
    assert status["directive"] == "resume the FR-01 wiring"
    assert status["context_anchor"] == "readback added, verifying"


def test_recovery_omits_keys_when_no_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No pre-compact state -> readback emits no directive/anchor keys."""
    run_dir = _make_run(tmp_path)
    run_yaml = run_dir / "meta" / "run.yaml"
    run_yaml.write_text("phase: plan\nstatus: active\ntask: x\n", encoding="utf-8")
    monkeypatch.setattr(
        "trw_mcp.state._paths.resolve_trw_dir",
        lambda: tmp_path / ".trw",
    )
    status = _get_run_status(run_dir)
    assert "directive" not in status
    assert "context_anchor" not in status
    # Core run-status fields still resolve.
    assert status["phase"] == "plan"


# --- end-to-end through the MCP tool ---


def _tool_run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "docs" / "task" / "runs" / "20260529T120000Z-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: t\nstatus: active\nphase: implement\ntask: t\n", encoding="utf-8"
    )
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return d


def test_tool_persists_and_echoes_directive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """trw_pre_compact_checkpoint persists directive/anchor and echoes them back."""
    run_dir = _tool_run_dir(tmp_path)
    tools = make_ceremony_server(monkeypatch, tmp_path)

    with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
        result = tools["trw_pre_compact_checkpoint"].fn(
            directive="land FR-01 then deliver",
            context_anchor="readback wired, running tests",
        )

    assert result["status"] == "success"
    assert result["directive"] == "land FR-01 then deliver"
    assert result["context_anchor"] == "readback wired, running tests"

    # Persisted into the pre-compact state (resolve_project_root -> tmp_path via autouse).
    state_file = tmp_path / ".trw" / "context" / "pre_compact_state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["directive"] == "land FR-01 then deliver"
    assert state["context_anchor"] == "readback wired, running tests"


def test_tool_backward_compatible_without_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling the tool with no new args still works; no directive keys emitted."""
    run_dir = _tool_run_dir(tmp_path)
    tools = make_ceremony_server(monkeypatch, tmp_path)

    with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
        result = tools["trw_pre_compact_checkpoint"].fn()

    assert result["status"] == "success"
    assert "directive" not in result
    assert "context_anchor" not in result
    state_file = tmp_path / ".trw" / "context" / "pre_compact_state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert "directive" not in state
    assert "context_anchor" not in state


def test_recovery_omits_keys_when_state_lacks_directive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """State written without the new params -> no keys surfaced (backward-compat)."""
    run_dir = _make_run(tmp_path)
    run_yaml = run_dir / "meta" / "run.yaml"
    run_yaml.write_text("phase: implement\nstatus: active\ntask: y\n", encoding="utf-8")
    _write_compact_state(
        tmp_path,
        run_dir,
        run_dir / "meta" / "events.jsonl",
        prd_scope=[],
        phase="implement",
        file_ownership_path="",
        failing_tests=[],
        ceremony_state={},
    )
    monkeypatch.setattr(
        "trw_mcp.state._paths.resolve_trw_dir",
        lambda: tmp_path / ".trw",
    )
    status = _get_run_status(run_dir)
    assert "directive" not in status
    assert "context_anchor" not in status
