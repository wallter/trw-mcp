"""PRD-FIX-077 — persist build_check_result into ceremony-state.json.

These tests verify that trw_build_check() populates the ceremony-state.json
file consumed by the pre-tool-deliver-gate hook (fallback path).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.state._ceremony_progress_state import (
    mark_build_check,
    read_ceremony_state,
)
from trw_mcp.state._ceremony_state_model import CeremonyState


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


def test_trw_build_check_passing_persists_result(
    tmp_project: Path,
    build_check_invoke: Any,
) -> None:
    build_check_invoke(
        tests_passed=True,
        test_count=10,
        coverage_pct=95.0,
        mypy_clean=True,
    )
    state = read_ceremony_state(tmp_project / ".trw")
    assert state.build_check_result == "passed"
    assert state.last_build_check_ts is not None


def test_trw_build_check_failing_persists_result(
    tmp_project: Path,
    build_check_invoke: Any,
) -> None:
    build_check_invoke(
        tests_passed=False,
        test_count=10,
        coverage_pct=95.0,
        mypy_clean=True,
    )
    state = read_ceremony_state(tmp_project / ".trw")
    assert state.build_check_result == "failed"


def test_ceremony_state_backward_compat_no_ts_field(tmp_project: Path) -> None:
    """Pre-FR02 JSON predates pool_cooldowns (PRD-CORE-296 R2-010), so it now
    fails the schema check and fails open to defaults rather than being
    partially parsed — last_build_check_ts is None because the whole state
    resets, not because the field was individually defaulted."""
    state_path = tmp_project / ".trw" / "context" / "ceremony-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = {"session_started": True, "build_check_result": "passed"}
    state_path.write_text(json.dumps(legacy))
    state = read_ceremony_state(tmp_project / ".trw")
    assert state.build_check_result is None
    assert state.last_build_check_ts is None
    assert state == CeremonyState()


def test_build_check_persist_failure_does_not_raise(
    build_check_invoke: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If mark_build_check raises (e.g., read-only fs), tool still returns status."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(
        "trw_mcp.state._ceremony_progress_state.mark_build_check",
        _boom,
    )
    result = build_check_invoke(tests_passed=True, test_count=10, coverage_pct=95.0)
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


# --- PRD-FIX-144 FR04 / NFR06: session observation, never per-learning credit ---


def _observations(tmp_project: Path) -> list[dict[str, Any]]:
    path = tmp_project / ".trw" / "logs" / "session_outcomes.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_build_check_appends_session_observation_not_credit(tmp_project: Path, build_check_invoke: Any) -> None:
    tracking = tmp_project / ".trw" / "logs" / "recall_tracking.jsonl"
    tracking.parent.mkdir(parents=True, exist_ok=True)
    tracking.write_text(json.dumps({"learning_id": "L-x", "query": "q", "timestamp": 1.0, "outcome": None}) + "\n")
    tracking_before = tracking.read_bytes()

    failed = build_check_invoke(tests_passed=False, test_count=4, failure_count=1, static_checks_clean=True)
    passed = build_check_invoke(tests_passed=True, test_count=5, scope="quick", static_checks_clean=False)

    rows = _observations(tmp_project)
    assert [r["tests_passed"] for r in rows] == [False, True]
    assert rows[0]["session_id"] == rows[1]["session_id"] != ""
    assert rows[0]["process_session_id"] == rows[1]["process_session_id"] != ""
    assert [r["test_count"] for r in rows] == [4, 5]
    assert [r["static_checks_clean"] for r in rows] == [True, False]
    assert [r["scope"] for r in rows] == ["full", "quick"]
    assert all(r["event"] == "build_check" and r["timestamp"] for r in rows)
    assert all("learning_id" not in r for r in rows)
    assert tracking.read_bytes() == tracking_before  # R10: never the recall log
    assert "session_observation" not in json.dumps([failed, passed])  # no response key


def test_build_check_disabled_writes_no_observation(
    tmp_project: Path, build_check_invoke: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.models.config import get_config

    monkeypatch.setattr(get_config(), "build_check_enabled", False)
    result = build_check_invoke(tests_passed=True)
    assert result["status"] == "skipped"
    assert _observations(tmp_project) == []


def test_build_check_reviewer_role_writes_no_observation(
    tmp_project: Path, build_check_invoke: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    assert build_check_invoke(tests_passed=True)["tests_passed"] is True  # the build itself still recorded
    assert _observations(tmp_project) == []


def test_session_outcomes_log_has_no_production_reader() -> None:
    """NFR06: only the build registration module names the observation log."""
    import trw_mcp

    src = Path(trw_mcp.__file__).parent
    naming = sorted(
        str(path.relative_to(src)) for path in src.rglob("*.py") if "session_outcomes" in path.read_text("utf-8")
    )
    assert naming == ["tools/build/_registration.py"]
