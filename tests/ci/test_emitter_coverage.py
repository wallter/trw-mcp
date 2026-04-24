"""CI emitter-coverage gate — PRD-HPO-MEAS-001 FR-3 + FR-10 / NFR-8.

This suite keeps the registry-parity checks and additionally proves that
the *production* dispatch path is live for the sprint-96 substrate pieces
that were previously only scaffold-checked:

* ``trw_session_start`` → ``SurfaceRegistered`` + ``session_start``
* wrapped ``@server.tool()`` dispatch → ``ToolCallEvent`` persistence

That closes the original gap where import-resolvable symbols existed but
the real server wrapping path never wrote events end-to-end.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
import importlib
from pathlib import Path
from typing import Any, Final

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state._paths import pin_active_run, unpin_active_run
from trw_mcp.telemetry.event_base import EVENT_TYPE_REGISTRY, HPOTelemetryEvent
from trw_mcp.telemetry.tool_call_timing import clear_pricing_cache

#: FR-10 §1-10 capabilities + import-resolvable scaffolding paths.
#: When a capability's module lands, add its import path here so this
#: gate starts asserting the symbol exists.
_FR10_CAPABILITIES: Final[tuple[tuple[str, str, str], ...]] = (
    ("ceremony",         "trw_mcp.telemetry.event_base",          "CeremonyEvent"),
    ("contract",         "trw_mcp.telemetry.event_base",          "ContractEvent"),
    ("phase_exposure",   "trw_mcp.telemetry.event_base",          "PhaseExposureEvent"),
    ("observer",         "trw_mcp.telemetry.event_base",          "ObserverEvent"),
    ("mcp_security",     "trw_mcp.telemetry.event_base",          "MCPSecurityEvent"),
    ("meta_tune",        "trw_mcp.telemetry.event_base",          "MetaTuneEvent"),
    ("thrashing",        "trw_mcp.telemetry.event_base",          "ThrashingEvent"),
    ("artifact_registry","trw_mcp.telemetry.artifact_registry",   "SurfaceRegistry"),
    ("surface_manifest", "trw_mcp.telemetry.surface_manifest",    "stamp_session"),
    ("tool_call_timing", "trw_mcp.telemetry.tool_call_timing",    "wrap_tool"),
)


class TestEventTypeRegistryParity:
    """NFR-3 schema rigor: every subclass is registered."""

    def test_registry_is_non_empty(self) -> None:
        assert len(EVENT_TYPE_REGISTRY) >= 12

    def test_every_registry_value_is_subclass(self) -> None:
        for event_type, cls in EVENT_TYPE_REGISTRY.items():
            assert issubclass(cls, HPOTelemetryEvent), f"{event_type} → {cls} not a subclass"

    def test_every_registry_key_matches_class_default(self) -> None:
        for event_type, cls in EVENT_TYPE_REGISTRY.items():
            default = cls.model_fields["event_type"].default
            assert default == event_type, f"registry key {event_type!r} ≠ class default {default!r}"

    def test_no_orphan_subclasses(self) -> None:
        # Walk HPOTelemetryEvent subclasses; every one must appear in the registry.
        def _all_subclasses(cls: type) -> set[type]:
            result: set[type] = set()
            for sub in cls.__subclasses__():
                result.add(sub)
                result.update(_all_subclasses(sub))
            return result

        registered = set(EVENT_TYPE_REGISTRY.values())
        # `H1ObserveModeWarning` subclasses `ObserverEvent` — both must appear
        # in the registry (which they do via their distinct event_type strings).
        missing: list[str] = []
        for sub in _all_subclasses(HPOTelemetryEvent):
            if sub not in registered:
                missing.append(sub.__name__)
        assert not missing, (
            f"HPOTelemetryEvent subclasses missing from EVENT_TYPE_REGISTRY: {missing}. "
            "Every subclass must register via its event_type literal or be explicitly "
            "marked as abstract in the PRD."
        )


class TestFR10CapabilityResolvability:
    """FR-10 §1-10: each declared emitter capability must be import-resolvable.

    This is a scaffolding gate — does not prove production dispatch. The
    deeper reachability test in Wave 2c exercises real tool invocations.
    """

    @pytest.mark.parametrize(("capability", "module_path", "symbol_name"), _FR10_CAPABILITIES)
    def test_capability_symbol_importable(
        self, capability: str, module_path: str, symbol_name: str
    ) -> None:
        try:
            mod = importlib.import_module(module_path)
        except ImportError as exc:
            pytest.fail(
                f"FR-10 capability {capability!r} — module {module_path!r} not importable: {exc}"
            )
        assert hasattr(mod, symbol_name), (
            f"FR-10 capability {capability!r} — symbol {symbol_name!r} missing from "
            f"{module_path}. Declared capability has no wiring."
        )


class TestEventTypeLiteralsAreStable:
    """Guard against accidental event_type renames that break jsonl consumers."""

    _EXPECTED_LITERALS: Final[frozenset[str]] = frozenset(
        {
            "ceremony",
            "contract",
            "phase_exposure",
            "observer",
            "mcp_security",
            "meta_tune",
            "thrashing",
            "llm_call",
            "tool_call",
            "session_start",
            "session_end",
            "ceremony_compliance",
            "h1_observe_mode_warning",
            "surface_registered",
        }
    )

    def test_registry_matches_expected_literals(self) -> None:
        assert set(EVENT_TYPE_REGISTRY.keys()) == self._EXPECTED_LITERALS, (
            "EVENT_TYPE_REGISTRY keys drifted from expected set. If you added a "
            "subclass, update both EVENT_TYPE_REGISTRY AND this test's "
            "_EXPECTED_LITERALS, and bump the PRD-HPO-MEAS-001 FR-3 schema."
        )


def _get_production_tool_fn(tool_name: str) -> Any:
    import trw_mcp.server._tools  # noqa: F401 - eager registration side effect
    from trw_mcp.server._app import mcp

    components = getattr(getattr(mcp, "_local_provider"), "_components", {})
    for key, component in components.items():
        if key.startswith(f"tool:{tool_name}@"):
            fn = getattr(component, "fn", None) or getattr(component, "func", None)
            if callable(fn):
                return fn
    pytest.fail(f"Production MCP tool {tool_name!r} not found in FastMCP component registry.")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def meas_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Path, Path]]:
    """Minimal workspace for production-dispatch telemetry tests."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    run_dir = trw_dir / "runs" / "task" / "run-123"
    meta_dir = run_dir / "meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "events.jsonl").write_text("", encoding="utf-8")
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
    (meta_dir / "run_surface_snapshot.yaml").write_text(
        "snapshot_id: snap-123\nartifacts: []\n",
        encoding="utf-8",
    )

    cfg = TRWConfig()
    _reset_config(cfg)
    clear_pricing_cache()
    monkeypatch.setenv("TRW_SESSION_ID", "sess-123")

    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools.ceremony.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools._ceremony_helpers.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools.build._registration.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools.ceremony._find_active_run_compat", lambda _ctx: run_dir)

    pin_active_run(run_dir, session_id="sess-123")
    try:
        yield trw_dir, run_dir
    finally:
        unpin_active_run(session_id="sess-123")
        _reset_config(None)
        clear_pricing_cache()


class TestProductionDispatchReachability:
    """FR-10: real production entry points must write their declared events."""

    def test_trw_session_start_emits_surface_registered_and_session_start(
        self,
        meas_workspace: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        trw_dir, run_dir = meas_workspace
        tool_fn = _get_production_tool_fn("trw_session_start")

        monkeypatch.setattr("trw_mcp.telemetry.boot_audit.run_boot_audit", lambda **_: [])
        result = tool_fn()
        assert result["success"] is True

        unified_files = sorted((run_dir / "meta").glob("events-*.jsonl"))
        assert unified_files, "session_start production path wrote no unified events file"
        records = _read_jsonl(unified_files[0])
        event_types = {str(rec["event_type"]) for rec in records}
        assert "surface_registered" in event_types
        assert "session_start" in event_types

        registry_log = run_dir / "meta" / "artifact_registry.jsonl"
        assert registry_log.exists()
        registry_rows = _read_jsonl(registry_log)
        assert registry_rows
        assert all(str(row["run_id"]) == "run-123" for row in registry_rows)

        session_rows = [rec for rec in records if rec["event_type"] == "session_start"]
        assert session_rows
        assert all(str(rec["surface_snapshot_id"]) for rec in session_rows)

    def test_wrapped_build_check_emits_tool_call_event_on_production_path(
        self,
        meas_workspace: tuple[Path, Path],
    ) -> None:
        _, run_dir = meas_workspace
        tool_fn = _get_production_tool_fn("trw_build_check")

        result = tool_fn(
            tests_passed=True,
            test_count=3,
            coverage_pct=97.5,
            mypy_clean=True,
            scope="full",
            run_path=str(run_dir),
        )
        assert result["tests_passed"] is True

        unified_files = sorted((run_dir / "meta").glob("events-*.jsonl"))
        assert unified_files, "tool wrapper wrote no unified events file"
        records = _read_jsonl(unified_files[0])
        tool_rows = [rec for rec in records if rec["event_type"] == "tool_call"]
        assert tool_rows, "production wrapper did not persist any ToolCallEvent rows"

        row = tool_rows[-1]
        assert row["session_id"] == "sess-123"
        assert row["run_id"] == "run-123"
        assert row["surface_snapshot_id"] == "snap-123"
        assert row["payload"]["tool"] == "trw_build_check"
        assert row["payload"]["wall_ms"] >= 0
        assert row["payload"]["pricing_version"]

    def test_three_production_tools_emit_three_tool_call_rows(
        self,
        meas_workspace: tuple[Path, Path],
    ) -> None:
        trw_dir, run_dir = meas_workspace
        build_check = _get_production_tool_fn("trw_build_check")
        query_events = _get_production_tool_fn("trw_query_events")
        surface_diff = _get_production_tool_fn("trw_surface_diff")

        other_run = trw_dir / "runs" / "task" / "run-456" / "meta"
        other_run.mkdir(parents=True)
        (other_run / "run_surface_snapshot.yaml").write_text(
            "\n".join(
                (
                    "snapshot_id: snap-456",
                    "artifacts:",
                    "  - surface_id: FRAMEWORK.md",
                    "    content_hash: " + ("aa" * 32),
                    "    version: v1",
                    "    discovered_at: 2026-04-24T00:00:00Z",
                    "    source_path: FRAMEWORK.md",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        build_check(
            tests_passed=True,
            test_count=3,
            coverage_pct=97.5,
            mypy_clean=True,
            scope="full",
            run_path=str(run_dir),
        )
        query_events(session_id="sess-123")
        surface_diff(snapshot_id_a="snap-123", snapshot_id_b="snap-456")

        unified_files = sorted((run_dir / "meta").glob("events-*.jsonl"))
        assert unified_files, "production wrappers wrote no unified events file"
        records = _read_jsonl(unified_files[0])
        tool_rows = [rec for rec in records if rec["event_type"] == "tool_call"]
        observed_tools = {str(row["payload"]["tool"]) for row in tool_rows}
        assert {"trw_build_check", "trw_query_events", "trw_surface_diff"} <= observed_tools

    def test_wrapped_build_check_error_path_populates_error_fields(
        self,
        meas_workspace: tuple[Path, Path],
    ) -> None:
        _, run_dir = meas_workspace
        tool_fn = _get_production_tool_fn("trw_build_check")

        with pytest.raises(ValueError, match="tests_passed is required"):
            tool_fn(run_path=str(run_dir))

        unified_files = sorted((run_dir / "meta").glob("events-*.jsonl"))
        assert unified_files, "tool wrapper wrote no unified events file on error path"
        records = _read_jsonl(unified_files[0])
        tool_rows = [rec for rec in records if rec["event_type"] == "tool_call"]
        assert tool_rows

        row = tool_rows[-1]
        assert row["payload"]["tool"] == "trw_build_check"
        assert row["payload"]["outcome"] == "error"
        assert row["payload"]["error_class"] == "ValueError"
