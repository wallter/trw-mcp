"""PRD-CORE-311-FR07: the ``sync_health`` row of ``trw-mcp doctor``.

``sync_health_row`` wraps the EXISTING ``step_sync_health`` read (the same one
``trw_session_start`` already performs) unchanged -- no new detection, only a
new surface. Every case here monkeypatches ``step_sync_health`` at its ORIGIN
module (``trw_mcp.tools._sync_health``) rather than re-deriving a
degraded/healthy verdict, since that derivation is FR07's own dependency, not
what this row adds; ``_doctor_sync_health.py`` imports it lazily inside the
function body, so patching the origin is what actually takes effect.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

import trw_mcp.tools._sync_health as sync_health_module
from trw_mcp.models.config import TRWConfig
from trw_mcp.server import _doctor_sync_health
from trw_mcp.server._subcommands_doctor import _CHECKS, CheckResult, _check_sync_health, _doctor_core

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("stub_cli_version_probes")]


def _stub(monkeypatch: pytest.MonkeyPatch, result: dict[str, object]) -> None:
    monkeypatch.setattr(sync_health_module, "step_sync_health", lambda trw_dir, cfg, degradations=None: result)


def test_degraded_read_maps_to_warn_with_the_step_advisory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub(
        monkeypatch,
        {
            "degraded": True,
            "consecutive_failures": 12,
            "last_push_at": None,
            "advisory": "Backend sync-push is degraded: 12 consecutive failures; last successful push never.",
        },
    )
    status, message = _doctor_sync_health.sync_health_row(tmp_path, TRWConfig())

    assert status == "WARN"
    assert "12 consecutive failures" in message


def test_not_measured_read_maps_to_skip_never_a_false_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub(
        monkeypatch,
        {
            "status": "not_measured",
            "reason": "sync_state_absent",
            "consecutive_failures": 0,
            "last_push_at": None,
            "advisory": "sync health not measured: sync_state_absent",
        },
    )
    status, message = _doctor_sync_health.sync_health_row(tmp_path, TRWConfig())

    assert status == "SKIP"
    assert "sync_state_absent" in message


def test_healthy_read_maps_to_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub(
        monkeypatch,
        {
            "degraded": False,
            "consecutive_failures": 0,
            "last_push_at": "2026-09-26T00:00:00+00:00",
            "advisory": "",
        },
    )
    status, message = _doctor_sync_health.sync_health_row(tmp_path, TRWConfig())

    assert status == "PASS"
    assert "healthy" in message.lower()


def test_check_sync_health_wraps_the_row_into_a_named_check_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The doctor-facing wrapper names the row ``sync_health`` (FR07's row name)."""
    _stub(monkeypatch, {"degraded": False, "consecutive_failures": 0, "last_push_at": None, "advisory": ""})

    result = _check_sync_health(tmp_path, TRWConfig())

    assert result == CheckResult("sync_health", "PASS", "sync push healthy: no degraded signal")


def test_sync_health_is_appended_after_every_pre_existing_row() -> None:
    """Appended-last rule: it follows every row that existed before it (dispatch_credentials).
    Later-appended rows (e.g. retired_artifacts) may follow it, so this doesn't pin it to -1."""
    names = [name for name, _ in _CHECKS]
    assert ("sync_health", "_check_sync_health") in _CHECKS
    assert names.index("sync_health") > names.index("dispatch_credentials")


def test_full_doctor_run_surfaces_the_degraded_row(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """End-to-end through ``_doctor_core``: a degraded sync-state.json produces a WARN row."""
    _stub(
        monkeypatch,
        {
            "degraded": True,
            "consecutive_failures": 99,
            "last_push_at": None,
            "advisory": "Backend sync-push is degraded: 99 consecutive failures; last successful push never.",
        },
    )
    results = _doctor_core(tmp_path, TRWConfig())

    rows = {r.name: r for r in cast("list[CheckResult]", results)}
    assert "sync_health" in rows
    assert rows["sync_health"].status == "WARN"


def test_doctor_reads_the_real_sync_state_under_dot_trw(tmp_path: Path) -> None:
    """Sol r1 P2: the doctor passed the project root, so a degraded state under .trw read as absent (SKIP).

    No stub: the real ``step_sync_health`` reads ``<project>/.trw/sync-state.json``.
    """
    import json

    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "sync-state.json").write_text(
        json.dumps({"consecutive_failures": 99, "last_push_at": None}), encoding="utf-8"
    )

    result = _check_sync_health(tmp_path, TRWConfig())

    assert result.status == "WARN", result
