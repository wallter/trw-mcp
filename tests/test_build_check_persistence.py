"""PRD-FIX-077 — persist build_check_result into ceremony-state.json.

These tests verify that trw_build_check() populates the ceremony-state.json
file consumed by the pre-tool-deliver-gate hook (fallback path).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.state._ceremony_progress_state import (
    mark_build_check,
    read_ceremony_state,
)


def _invoke_build_check(tmp_project: Path, *, tests_passed: bool, mypy_clean: bool = True) -> dict:
    """Invoke trw_build_check tool via the FastMCP server fixture."""
    from tests.conftest import extract_tool_fn, make_test_server

    server = make_test_server("build")
    fn = extract_tool_fn(server, "trw_build_check")

    import trw_mcp.tools.build._registration as reg_mod

    # Force the tool to operate under the tmp_project .trw/ dir.
    original_resolve = reg_mod.resolve_trw_dir
    reg_mod.resolve_trw_dir = lambda: tmp_project / ".trw"  # type: ignore[assignment]
    try:
        return fn(  # type: ignore[no-any-return]
            tests_passed=tests_passed,
            test_count=10,
            coverage_pct=95.0,
            mypy_clean=mypy_clean,
            scope="full",
        )
    finally:
        reg_mod.resolve_trw_dir = original_resolve  # type: ignore[assignment]


def test_mark_build_check_writes_passed(tmp_project: Path) -> None:
    trw_dir = tmp_project / ".trw"
    mark_build_check(trw_dir, True)
    state = read_ceremony_state(trw_dir)
    assert state.build_check_result == "passed"
    assert state.last_build_check_ts is not None
    # ISO-8601 parse check
    from datetime import datetime

    parsed = datetime.fromisoformat(state.last_build_check_ts)
    assert parsed.tzinfo is not None


def test_mark_build_check_writes_failed(tmp_project: Path) -> None:
    trw_dir = tmp_project / ".trw"
    mark_build_check(trw_dir, False)
    state = read_ceremony_state(trw_dir)
    assert state.build_check_result == "failed"
    assert state.last_build_check_ts is not None


def test_trw_build_check_passing_persists_result(tmp_project: Path) -> None:
    _invoke_build_check(tmp_project, tests_passed=True)
    state = read_ceremony_state(tmp_project / ".trw")
    assert state.build_check_result == "passed"
    assert state.last_build_check_ts is not None


def test_trw_build_check_failing_persists_result(tmp_project: Path) -> None:
    _invoke_build_check(tmp_project, tests_passed=False, mypy_clean=True)
    state = read_ceremony_state(tmp_project / ".trw")
    assert state.build_check_result == "failed"


def test_ceremony_state_backward_compat_no_ts_field(tmp_project: Path) -> None:
    """Loading pre-FR02 JSON (no last_build_check_ts) must default to None."""
    state_path = tmp_project / ".trw" / "context" / "ceremony-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = {"session_started": True, "build_check_result": "passed"}
    state_path.write_text(json.dumps(legacy))
    state = read_ceremony_state(tmp_project / ".trw")
    assert state.build_check_result == "passed"
    assert state.last_build_check_ts is None


def test_build_check_persist_failure_does_not_raise(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If mark_build_check raises (e.g., read-only fs), tool still returns status."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(
        "trw_mcp.state._ceremony_progress_state.mark_build_check",
        _boom,
    )
    result = _invoke_build_check(tmp_project, tests_passed=True)
    assert result["tests_passed"] is True


def test_atomic_write_produces_no_partial_file(tmp_project: Path) -> None:
    """After write_ceremony_state returns, the file is always valid JSON."""
    trw_dir = tmp_project / ".trw"
    state_path = trw_dir / "context" / "ceremony-state.json"
    for i in range(20):
        mark_build_check(trw_dir, i % 2 == 0)
        # File must parse at every step
        data = json.loads(state_path.read_text())
        assert data["build_check_result"] in ("passed", "failed")
