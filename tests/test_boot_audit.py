"""Tests for boot_audit — PRD-HPO-MEAS-001 NFR-12 + FR-13 boot gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models.config import _reset_config
from trw_mcp.state._paths import pin_active_run, unpin_active_run
from trw_mcp.telemetry.boot_audit import (
    ResolutionFailure,
    _check_event_type_registry,
    _check_hash_algorithm,
    _check_pricing_yaml,
    check_defaults,
    run_boot_audit,
)
from trw_mcp.telemetry.event_base import DefaultResolutionError
from trw_mcp.telemetry.tool_call_timing import clear_pricing_cache


class TestResolutionFailure:
    def test_is_frozen_dataclass(self) -> None:
        f = ResolutionFailure(key="x", expected="a", actual="b", remediation="fix")
        with pytest.raises(Exception):
            f.key = "y"  # type: ignore[misc]


class TestIndividualChecks:
    def test_pricing_yaml_resolves_in_dev_install(self) -> None:
        # Dev install has pricing.yaml bundled; check returns None.
        assert _check_pricing_yaml() is None

    def test_hash_algorithm_resolves(self) -> None:
        assert _check_hash_algorithm() is None

    def test_event_type_registry_is_populated(self) -> None:
        assert _check_event_type_registry() is None


class TestCheckDefaults:
    def test_returns_empty_list_on_success(self) -> None:
        failures = check_defaults()
        assert failures == []


class TestRunBootAudit:
    def test_returns_empty_list_when_all_pass(self) -> None:
        out = run_boot_audit(raise_on_failure=False)
        assert out == []

    def test_does_not_raise_on_success(self) -> None:
        # Default raise_on_failure=True should still succeed because the
        # dev install has all resources available.
        out = run_boot_audit()
        assert out == []

    def test_raises_typed_error_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Force a failure by monkeypatching one check.
        from trw_mcp.telemetry import boot_audit as ba

        def _broken() -> ResolutionFailure:
            return ResolutionFailure(
                key="synthetic",
                expected="pass",
                actual="simulated failure",
                remediation="n/a — this is a test",
            )

        monkeypatch.setattr(ba, "_CHECKS", (_broken,))
        with pytest.raises(DefaultResolutionError) as excinfo:
            ba.run_boot_audit()
        assert "synthetic" in str(excinfo.value)
        assert "simulated failure" in str(excinfo.value)

    def test_returns_failures_when_raise_on_failure_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.telemetry import boot_audit as ba

        def _broken() -> ResolutionFailure:
            return ResolutionFailure(
                key="synthetic",
                expected="pass",
                actual="simulated failure",
                remediation="n/a",
            )

        monkeypatch.setattr(ba, "_CHECKS", (_broken,))
        out = ba.run_boot_audit(raise_on_failure=False)
        assert len(out) == 1
        assert out[0].key == "synthetic"


class TestSurfaceRegisteredEmission:
    """FR-10 AC-8: SurfaceRegistry.build_and_emit produces SurfaceRegistered events."""

    def test_build_and_emit_writes_surface_registered_events(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.telemetry.artifact_registry import (
            SurfaceRegistry,
            clear_snapshot_cache,
        )

        clear_snapshot_cache()

        # Fake data root with 2 agents + 1 hook.
        data_root = tmp_path / "data"
        (data_root / "agents").mkdir(parents=True)
        (data_root / "agents" / "a.md").write_text("a")
        (data_root / "agents" / "b.md").write_text("b")
        (data_root / "hooks").mkdir()
        (data_root / "hooks" / "h.sh").write_text("#!/bin/sh")

        # Run dir with meta/ so unified_events has a write target.
        run_dir = tmp_path / "run-1"
        (run_dir / "meta").mkdir(parents=True)

        registry = SurfaceRegistry.build_and_emit(
            session_id="s1",
            run_id="run-1",
            run_dir=run_dir,
            data_root=data_root,
            repo_root=tmp_path,
        )

        # Registry still comes back.
        assert len(registry.artifacts) == 3

        # Events file must exist + contain 3 SurfaceRegistered records.
        events_files = list((run_dir / "meta").glob("events-*.jsonl"))
        assert len(events_files) == 1
        import json

        lines = events_files[0].read_text().strip().splitlines()
        records = [json.loads(ln) for ln in lines]
        assert len(records) == 3
        assert all(r["event_type"] == "surface_registered" for r in records)
        assert all("surface_id" in r["payload"] for r in records)
        assert all("content_hash" in r["payload"] for r in records)
        assert all("category" in r["payload"] for r in records)

        registry_log = run_dir / "meta" / "artifact_registry.jsonl"
        assert registry_log.exists()
        registry_rows = [json.loads(line) for line in registry_log.read_text().splitlines()]
        assert len(registry_rows) == 3
        assert all(row["run_id"] == "run-1" for row in registry_rows)
        assert all(row["session_id"] == "s1" for row in registry_rows)


def _get_production_tool_fn(tool_name: str) -> Any:
    import trw_mcp.server._tools  # noqa: F401
    from trw_mcp.server._app import mcp

    components = getattr(getattr(mcp, "_local_provider"), "_components", {})
    for key, component in components.items():
        if key.startswith(f"tool:{tool_name}@"):
            fn = getattr(component, "fn", None) or getattr(component, "func", None)
            if callable(fn):
                return fn
    pytest.fail(f"Production MCP tool {tool_name!r} not found.")


class TestSessionStartBootGate:
    def test_boot_audit_failure_raises_before_startup_event_write(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        run_dir = trw_dir / "runs" / "task" / "run-123"
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)
        (meta_dir / "run.yaml").write_text(
            "\n".join(
                (
                    "run_id: run-123",
                    "status: active",
                    "phase: implement",
                    "task: task",
                    "owner_session_id: sess-123",
                    "surface_snapshot_id: snap-123",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        (meta_dir / "run_surface_snapshot.yaml").write_text("snapshot_id: snap-123\nartifacts: []\n")

        monkeypatch.setenv("TRW_SESSION_ID", "sess-123")
        monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
        monkeypatch.setattr("trw_mcp.tools.ceremony.resolve_trw_dir", lambda: trw_dir)
        monkeypatch.setattr("trw_mcp.tools._ceremony_helpers.resolve_trw_dir", lambda: trw_dir)
        pin_active_run(run_dir, session_id="sess-123")
        _reset_config(None)
        clear_pricing_cache()

        tool_fn = _get_production_tool_fn("trw_session_start")
        monkeypatch.setattr(
            "trw_mcp.telemetry.boot_audit.run_boot_audit",
            lambda **_: (_ for _ in ()).throw(DefaultResolutionError("boom")),
        )

        try:
            with pytest.raises(DefaultResolutionError, match="boom"):
                tool_fn()
        finally:
            unpin_active_run(session_id="sess-123")
            _reset_config(None)
            clear_pricing_cache()

        assert not list((run_dir / "meta").glob("events-*.jsonl"))
        assert not (run_dir / "meta" / "tool_call_events.jsonl").exists()
        assert not (trw_dir / "context" / "session-events.jsonl").exists()
