"""PRD-INFRA-142 FR02 — first_session funnel marker.

``step_first_session_marker`` emits a single ``first_session`` telemetry event
on the first ``trw_session_start`` of a fresh installation, gated by a local
flag file so it never re-emits and never needs a backend round-trip on
subsequent calls. Opt-out is honored transitively (TelemetryClient is a no-op
when telemetry is disabled).

These tests exercise real on-disk behavior (flag file + flushed JSONL queue).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig, reload_config
from trw_mcp.tools._ceremony_telemetry import (
    _FIRST_SESSION_FLAG_REL,
    step_first_session_marker,
)


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, telemetry: bool) -> TRWConfig:
    """Point the config singleton + path resolvers at a temp .trw dir.

    ``resolve_trw_dir`` is cwd-relative (``project_root / config.trw_dir``), so
    we pin both consumer sites — the marker's flag path and the TelemetryClient
    output path — to this test's tmp dir for deterministic isolation.
    """
    trw_dir = tmp_path / ".trw"
    (trw_dir / "logs").mkdir(parents=True, exist_ok=True)
    cfg = TRWConfig(
        trw_dir=str(trw_dir),
        installation_id="test-inst-funnel",
        telemetry_enabled=telemetry,
        target_platforms=["cursor-ide"],
    )
    reload_config(cfg)
    # _resolve_trw_dir_compat probes ceremony.resolve_trw_dir first, then falls
    # back to _ceremony_telemetry.resolve_trw_dir; pin both, plus the client.
    monkeypatch.setattr("trw_mcp.tools.ceremony.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools._ceremony_telemetry.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.telemetry.client.resolve_trw_dir", lambda: trw_dir)
    return cfg


def _read_telemetry_events(cfg: TRWConfig) -> list[dict[str, object]]:
    path = Path(cfg.trw_dir) / cfg.logs_dir / cfg.telemetry_file
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_first_session_emits_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh install → one first_session event + flag file created."""
    cfg = _install(tmp_path, monkeypatch, telemetry=True)

    emitted = step_first_session_marker()
    assert emitted is True

    flag = Path(cfg.trw_dir) / _FIRST_SESSION_FLAG_REL
    assert flag.exists(), "first_session_emitted flag must be written after emit"

    events = _read_telemetry_events(cfg)
    first_sessions = [e for e in events if e.get("event_type") == "first_session"]
    assert len(first_sessions) == 1
    ev = first_sessions[0]
    # PRD-SEC-004-FR08: the legacy session-event path now hashes installation_id
    # at the egress boundary (parity with the pipeline path). The RAW project
    # name must NOT appear in the recorded/upload-queue event.
    from trw_mcp.telemetry.anonymizer import anonymize_installation_id

    assert ev["installation_id"] == anonymize_installation_id("test-inst-funnel")
    assert ev["installation_id"] != "test-inst-funnel"
    assert ev["profile"] == "cursor-ide"
    assert ev["framework_version"] == cfg.framework_version


def test_first_session_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Second call with the flag present emits nothing."""
    cfg = _install(tmp_path, monkeypatch, telemetry=True)

    assert step_first_session_marker() is True
    # Second call: flag exists → no-op, no new event.
    assert step_first_session_marker() is False

    events = _read_telemetry_events(cfg)
    first_sessions = [e for e in events if e.get("event_type") == "first_session"]
    assert len(first_sessions) == 1, "first_session must not double-emit"


def test_first_session_suppressed_when_telemetry_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR01: opt-out (telemetry disabled) writes no event row.

    The marker still creates the flag (it ran), but TelemetryClient is a no-op
    so no event is persisted — honoring the opt-out contract.
    """
    cfg = _install(tmp_path, monkeypatch, telemetry=False)

    step_first_session_marker()

    events = _read_telemetry_events(cfg)
    assert [e for e in events if e.get("event_type") == "first_session"] == []


def test_first_session_marker_is_failopen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure inside the marker never raises (fail-open)."""
    _install(tmp_path, monkeypatch, telemetry=True)

    def _boom() -> object:
        raise RuntimeError("simulated config failure")

    # Force the inner config lookup to explode; the marker must swallow it.
    monkeypatch.setattr("trw_mcp.models.config.get_config", _boom)
    # Must not raise.
    assert step_first_session_marker() is False


def test_session_start_invokes_first_session_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wiring: trw_session_start calls step_first_session_marker via the facade."""
    from tests.conftest import extract_tool_fn, make_test_server
    from trw_mcp.tools import ceremony as ceremony_mod

    _install(tmp_path, monkeypatch, telemetry=True)
    monkeypatch.setattr(ceremony_mod, "resolve_trw_dir", lambda: tmp_path / ".trw")

    called: dict[str, int] = {"n": 0}

    def _spy() -> bool:
        called["n"] += 1
        return True

    monkeypatch.setattr(ceremony_mod, "step_first_session_marker", _spy)

    fn = extract_tool_fn(make_test_server("ceremony"), "trw_session_start")
    fn(query="*")

    assert called["n"] == 1, "trw_session_start must invoke step_first_session_marker once"
