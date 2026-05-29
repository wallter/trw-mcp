"""Telemetry session summaries preserve language-neutral build status."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.tools._deferred_steps_telemetry import _step_telemetry

pytestmark = pytest.mark.unit


class _DummyPipeline:
    def stop(self, *, drain: bool, timeout: float) -> None:
        assert drain is True
        assert timeout == 10.0


class _DummyTelemetryClient:
    @classmethod
    def from_config(cls) -> _DummyTelemetryClient:
        return cls()

    def record_event(self, _event: object) -> None:
        return None

    def flush(self) -> None:
        return None


def _wire_telemetry_dependencies(monkeypatch: pytest.MonkeyPatch, trw_dir: Path) -> None:
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: TRWConfig())
    monkeypatch.setattr("trw_mcp.state._paths.resolve_installation_id", lambda: "install-test")
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr(
        "trw_mcp.state.analytics.report.compute_ceremony_score", lambda *_args, **_kwargs: {"score": 100}
    )
    monkeypatch.setattr("trw_mcp.telemetry.client.TelemetryClient", _DummyTelemetryClient)
    monkeypatch.setattr("trw_mcp.telemetry.pipeline.TelemetryPipeline.get_instance", lambda: _DummyPipeline())


def _session_summary(trw_dir: Path) -> dict[str, object]:
    events_path = trw_dir / "context" / "session-events.jsonl"
    records = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    summaries = [record for record in records if record["event"] == "session_summary"]
    assert len(summaries) == 1
    return summaries[0]


def test_session_summary_includes_static_checks_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    FileStateWriter().write_yaml(
        trw_dir / "context" / "build-status.yaml",
        {
            "tests_passed": True,
            "static_checks_clean": False,
            "mypy_clean": True,
            "coverage_pct": 87.5,
        },
    )
    _wire_telemetry_dependencies(monkeypatch, trw_dir)

    result = _step_telemetry(None)

    assert result["status"] == "success"
    summary = _session_summary(trw_dir)
    assert summary["tests_passed"] is True
    assert summary["static_checks_clean"] is False
    assert summary["mypy_clean"] is True
    assert summary["coverage_pct"] == 87.5


def test_session_summary_backfills_static_checks_from_legacy_mypy_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    FileStateWriter().write_yaml(
        trw_dir / "context" / "build-status.yaml",
        {
            "tests_passed": True,
            "mypy_clean": False,
            "coverage_pct": 91.0,
        },
    )
    _wire_telemetry_dependencies(monkeypatch, trw_dir)

    _step_telemetry(None)

    summary = _session_summary(trw_dir)
    assert summary["static_checks_clean"] is False
    assert summary["mypy_clean"] is False
