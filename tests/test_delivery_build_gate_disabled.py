"""Delivery build-gate behavior — mcp-ceremony-10 + mcp-ceremony-7.

mcp-ceremony-10: when ``config.build_check_enabled`` is False, ``trw_build_check``
returns early without logging a ``build_check_complete`` event. The delivery gate
must NOT then fire ``build_gate_warning`` for the missing event — a disabled
build-check means no build gate (mirroring ``phase_gates_build.py``). The
premature-delivery work-events guard still applies.

mcp-ceremony-7: ``_check_no_active_run_build_gate`` intentionally fails open
(returns None) when no ceremony state / no started session exists; it fires only
for a started session that recorded no passing build.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._delivery_build_gates import (
    _check_build_and_work_events,
    _check_no_active_run_build_gate,
)


def _work_event() -> dict[str, object]:
    return {"event": "file_modified", "data": {"path": "src/a.py"}}


def test_disabled_build_check_skips_build_gate_with_work_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_check_enabled=False -> no build_gate_warning even with no build event."""
    monkeypatch.setattr(
        "trw_mcp.models.config.get_config",
        lambda: TRWConfig(build_check_enabled=False),
    )
    # Events contain real work but NO passing build_check_complete event.
    build_warning, premature_warning = _check_build_and_work_events([_work_event()])
    assert build_warning is None
    assert premature_warning is None


def test_disabled_build_check_skips_build_gate_on_empty_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty events + disabled build-check must not block (no false build gate)."""
    monkeypatch.setattr(
        "trw_mcp.models.config.get_config",
        lambda: TRWConfig(build_check_enabled=False),
    )
    build_warning, _ = _check_build_and_work_events([])
    assert build_warning is None


def test_disabled_build_check_still_flags_premature_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The work-events (premature-delivery) guard is independent of build-check."""
    monkeypatch.setattr(
        "trw_mcp.models.config.get_config",
        lambda: TRWConfig(build_check_enabled=False),
    )
    ceremony_only = [{"event": "session_start", "data": {}}, {"event": "checkpoint", "data": {}}]
    _, premature_warning = _check_build_and_work_events(ceremony_only)
    assert premature_warning is not None
    assert "Premature delivery" in premature_warning


def test_enabled_build_check_still_fires_build_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: with build-check ENABLED, the missing-build gate still fires."""
    monkeypatch.setattr(
        "trw_mcp.models.config.get_config",
        lambda: TRWConfig(build_check_enabled=True),
    )
    build_warning, _ = _check_build_and_work_events([_work_event()])
    assert build_warning is not None
    assert "No successful build check found" in build_warning


# --- unpinned-session gate intentional fail-open (mcp-ceremony-7) -------------


def _write_ceremony_state(trw_dir: Path, state: dict[str, object]) -> None:
    context = trw_dir / "context"
    context.mkdir(parents=True, exist_ok=True)
    (context / "ceremony-state.json").write_text(json.dumps(state), encoding="utf-8")


def test_no_active_run_gate_fails_open_when_no_ceremony_state(tmp_path: Path) -> None:
    """No ceremony-state.json -> no session began -> intentional fail-open (None).

    Blocking here would over-block legitimate new-project/quick-task delivery
    that never opted into ceremony. The marker on this branch documents intent.
    """
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    # No ceremony-state.json written.
    assert _check_no_active_run_build_gate(trw_dir, FileStateReader()) is None


def test_no_active_run_gate_fails_open_when_session_not_started(tmp_path: Path) -> None:
    """session_started falsey -> no started session -> fail-open (None)."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    _write_ceremony_state(trw_dir, {"session_started": False, "build_check_result": None})
    assert _check_no_active_run_build_gate(trw_dir, FileStateReader()) is None


def test_no_active_run_gate_blocks_started_session_without_passing_build(tmp_path: Path) -> None:
    """The gate DOES fire when a session started but recorded no passing build."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    _write_ceremony_state(trw_dir, {"session_started": True, "build_check_result": None})
    warning = _check_no_active_run_build_gate(trw_dir, FileStateReader())
    assert warning is not None
    assert "unpinned session" in warning


def test_no_active_run_gate_passes_started_session_with_passing_build(tmp_path: Path) -> None:
    """A passing recorded build satisfies the gate (None)."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    _write_ceremony_state(trw_dir, {"session_started": True, "build_check_result": "passed"})
    assert _check_no_active_run_build_gate(trw_dir, FileStateReader()) is None
