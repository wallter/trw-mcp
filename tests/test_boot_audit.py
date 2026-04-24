"""Tests for boot_audit — PRD-HPO-MEAS-001 NFR-12 + FR-13 boot gate."""

from __future__ import annotations

import pytest

from trw_mcp.telemetry.boot_audit import (
    ResolutionFailure,
    _check_event_type_registry,
    _check_hash_algorithm,
    _check_pricing_yaml,
    check_defaults,
    run_boot_audit,
)
from trw_mcp.telemetry.event_base import DefaultResolutionError


class TestResolutionFailure:
    def test_is_frozen_dataclass(self) -> None:
        f = ResolutionFailure(
            key="x", expected="a", actual="b", remediation="fix"
        )
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

    def test_returns_failures_when_raise_on_failure_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
        self, tmp_path, monkeypatch
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
            repo_root=None,
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
