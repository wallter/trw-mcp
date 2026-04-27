"""FR-14 per-field non-zero instrumentation test — NFR-7 CI gate.

Every field on every :class:`HPOTelemetryEvent` subclass that is NOT
annotated ``nullable_zero_by_design: true`` MUST have a corresponding
assertion that a realistic emission path populates it with a non-default
value. A missing per-field test is a BUILD FAILURE (DIST D11/O2 pattern
prevention).

The test runs over every subclass in ``EVENT_TYPE_REGISTRY``. Fields
whose legitimate zero/null cases are documented carry the ``nullable_zero_by_design``
marker in the PRD's nullability annex (§FR-14 body) — here we skip them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import pytest

from trw_mcp.models.config import _reset_config
from trw_mcp.state._paths import pin_active_run, unpin_active_run
from trw_mcp.telemetry.event_base import (
    EVENT_PAYLOAD_KEY_REGISTRY,
    EVENT_TYPE_REGISTRY,
    CeremonyEvent,
    ContractEvent,
    HPOCeremonyComplianceEvent,
    HPOSessionEndEvent,
    HPOSessionStartEvent,
    HPOTelemetryEvent,
    LLMCallEvent,
    MCPSecurityEvent,
    MetaTuneEvent,
    ObserverEvent,
    PhaseExposureEvent,
    SurfaceRegistered,
    ThrashingEvent,
    ToolCallEvent,
    emit_h1_observe_mode_warning,
)
from trw_mcp.telemetry.tool_call_timing import clear_pricing_cache

#: PRD-HPO-MEAS-001 §FR-14: fields legitimately zero/null by design.
_NULLABLE_BY_DESIGN: Final[frozenset[str]] = frozenset(
    {
        "run_id",  # Phase 1 pre-run cold-start
        "surface_snapshot_id",  # Phase 1 default empty-string until wiring
        "parent_event_id",  # optional for root events
    }
)

#: Realistic sample constructor per subclass — each returns a fully
#: populated event (payload carrying FR-14 meaningful keys).
_SAMPLE_BUILDERS: Final[dict[str, HPOTelemetryEvent]] = {
    "ceremony": CeremonyEvent(
        session_id="s1",
        run_id="r1",
        surface_snapshot_id="snap_a",
        payload={"phase": "IMPLEMENT"},
    ),
    "contract": ContractEvent(
        session_id="s1",
        run_id="r1",
        surface_snapshot_id="snap_a",
        payload={"contract_id": "ctx-42", "outcome": "pass"},
    ),
    "phase_exposure": PhaseExposureEvent(
        session_id="s1",
        run_id="r1",
        surface_snapshot_id="snap_a",
        payload={"phase": "VALIDATE", "duration_ms": 1200},
    ),
    "observer": ObserverEvent(
        session_id="s1",
        run_id="r1",
        surface_snapshot_id="snap_a",
        payload={"kind": "oversight_hook"},
    ),
    "mcp_security": MCPSecurityEvent(
        session_id="s1",
        run_id="r1",
        surface_snapshot_id="snap_a",
        payload={"decision": "allow", "scope": "tool_call"},
    ),
    "meta_tune": MetaTuneEvent(
        session_id="s1",
        run_id="r1",
        surface_snapshot_id="snap_a",
        payload={"proposal_id": "prop-7", "outcome": "queued"},
    ),
    "thrashing": ThrashingEvent(
        session_id="s1",
        run_id="r1",
        surface_snapshot_id="snap_a",
        payload={"retry_count": 3, "tool": "trw_recall"},
    ),
    "llm_call": LLMCallEvent(
        session_id="s1",
        run_id="r1",
        surface_snapshot_id="snap_a",
        payload={"model": "claude-opus-4-7", "input_tokens": 120, "output_tokens": 80},
    ),
    "tool_call": ToolCallEvent(
        session_id="s1",
        run_id="r1",
        surface_snapshot_id="snap_a",
        payload={
            "tool": "trw_recall",
            "start_ts": "2026-04-24T00:00:00+00:00",
            "end_ts": "2026-04-24T00:00:00.045000+00:00",
            "wall_ms": 45,
            "input_tokens": 12,
            "output_tokens": 7,
            "usd_cost_est": 0.00042,
            "outcome": "success",
            "pricing_version": "2026-04-23",
        },
    ),
    "session_start": HPOSessionStartEvent(
        session_id="s1",
        run_id="r1",
        surface_snapshot_id="snap_a",
        payload={"learnings_loaded": 42, "framework_version": "v24.6_TRW"},
    ),
    "session_end": HPOSessionEndEvent(
        session_id="s1",
        run_id="r1",
        surface_snapshot_id="snap_a",
        payload={"reason": "deliver", "duration_ms": 8700},
    ),
    "ceremony_compliance": HPOCeremonyComplianceEvent(
        session_id="s1",
        run_id="r1",
        surface_snapshot_id="snap_a",
        payload={"score": 0.91},
    ),
    "h1_observe_mode_warning": emit_h1_observe_mode_warning(
        session_id="s1",
        run_id="r1",
        emitter_name="ceremony",
        fallback_reason="h1_substrate_not_live",
        buffered_event_count_since_start=5,
        surface_snapshot_id="snap_a",
    ),
    "surface_registered": SurfaceRegistered(
        session_id="s1",
        run_id="r1",
        surface_snapshot_id="snap_a",
        payload={
            "surface_id": "agents:trw-implementer.md",
            "content_hash": "ff" * 32,
            "source_path": "agents/trw-implementer.md",
            "category": "agents",
        },
    ),
}


@pytest.mark.parametrize("event_type", sorted(EVENT_TYPE_REGISTRY.keys()))
def test_registered_event_has_sample_builder(event_type: str) -> None:
    """Every registered subclass must have a sample constructor in this test."""
    assert event_type in _SAMPLE_BUILDERS, (
        f"EVENT_TYPE_REGISTRY entry {event_type!r} has no sample in _SAMPLE_BUILDERS. "
        f"Add one so FR-14 field-population can assert on it."
    )


@pytest.mark.parametrize("event_type", sorted(EVENT_TYPE_REGISTRY.keys()))
def test_registered_event_has_payload_key_contract(event_type: str) -> None:
    """Every registered subtype must declare the payload keys FR-14 proves."""
    assert event_type in EVENT_PAYLOAD_KEY_REGISTRY, (
        f"EVENT_TYPE_REGISTRY entry {event_type!r} has no payload-key contract in EVENT_PAYLOAD_KEY_REGISTRY."
    )


@pytest.mark.parametrize("event_type", sorted(_SAMPLE_BUILDERS.keys()))
def test_non_nullable_top_level_fields_populated(event_type: str) -> None:
    """FR-14: every non-``nullable_zero_by_design`` field has a non-default value."""
    event = _SAMPLE_BUILDERS[event_type]
    for field_name, info in type(event).model_fields.items():
        if field_name in _NULLABLE_BY_DESIGN:
            continue
        value = getattr(event, field_name)
        default = info.default
        if field_name == "event_id":
            assert isinstance(value, str) and value.startswith("evt_")
            continue
        if field_name == "ts":
            assert isinstance(value, datetime)
            assert value.tzinfo is not None
            continue
        if field_name == "payload":
            assert isinstance(value, dict) and len(value) > 0, (
                f"{event_type}.payload is empty — at least one FR-14 payload key required"
            )
            continue
        assert value != default or (isinstance(value, str) and value), (
            f"{event_type}.{field_name} remained at default {default!r} "
            f"(actual {value!r}); specify a non-default sample in _SAMPLE_BUILDERS."
        )


def _payload_value_is_meaningful(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return value != 0


@pytest.mark.parametrize("event_type", sorted(_SAMPLE_BUILDERS.keys()))
def test_sample_builder_carries_canonical_payload_keys(event_type: str) -> None:
    """FR-14: schema-level proof is explicit about payload-backed subtype fields."""
    event = _SAMPLE_BUILDERS[event_type]
    required_keys = EVENT_PAYLOAD_KEY_REGISTRY[event_type]
    for key in required_keys:
        assert key in event.payload, f"{event_type}.payload missing canonical key {key!r}"
        assert _payload_value_is_meaningful(event.payload[key]), (
            f"{event_type}.payload[{key!r}] is default-like: {event.payload[key]!r}"
        )


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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def production_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
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
    monkeypatch.setattr("trw_mcp.tools.build._registration.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools.ceremony._find_active_run_compat", lambda _ctx: run_dir)
    pin_active_run(run_dir, session_id="sess-123")
    try:
        yield run_dir
    finally:
        unpin_active_run(session_id="sess-123")
        _reset_config(None)
        clear_pricing_cache()


class TestRepresentativeProductionPaths:
    """FR-14: key sprint-96 subclasses must be emitted via production paths."""

    def test_tool_call_fields_populated_via_wrapped_server_dispatch(
        self,
        production_workspace: Path,
    ) -> None:
        tool_fn = _get_production_tool_fn("trw_build_check")
        tool_fn(
            tests_passed=True,
            test_count=2,
            coverage_pct=98.0,
            mypy_clean=True,
            run_path=str(production_workspace),
        )

        events_file = next((production_workspace / "meta").glob("events-*.jsonl"))
        tool_rows = [row for row in _read_jsonl(events_file) if row["event_type"] == "tool_call"]
        assert tool_rows
        row = tool_rows[-1]
        assert row["session_id"] == "sess-123"
        assert row["run_id"] == "run-123"
        assert row["surface_snapshot_id"] == "snap-123"
        assert row["payload"]["tool"] == "trw_build_check"
        assert row["payload"]["start_ts"]
        assert row["payload"]["end_ts"]
        assert row["payload"]["pricing_version"]
        assert row["payload"]["wall_ms"] >= 0
        assert row["payload"]["usd_cost_est"] >= 0
        assert row["payload"]["outcome"] == "success"

    def test_multiple_wrapped_tools_populate_distinct_tool_names_via_production_dispatch(
        self,
        production_workspace: Path,
    ) -> None:
        build_check = _get_production_tool_fn("trw_build_check")
        query_events = _get_production_tool_fn("trw_query_events")
        surface_diff = _get_production_tool_fn("trw_surface_diff")

        other_run = production_workspace.parent.parent.parent / "task" / "run-456" / "meta"
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
            test_count=2,
            coverage_pct=98.0,
            mypy_clean=True,
            run_path=str(production_workspace),
        )
        query_events(session_id="sess-123")
        surface_diff(snapshot_id_a="snap-123", snapshot_id_b="snap-456")

        events_file = next((production_workspace / "meta").glob("events-*.jsonl"))
        tool_rows = [row for row in _read_jsonl(events_file) if row["event_type"] == "tool_call"]
        observed_tools = {str(row["payload"]["tool"]) for row in tool_rows}
        assert {"trw_build_check", "trw_query_events", "trw_surface_diff"} <= observed_tools

    def test_tool_call_error_fields_populated_via_wrapped_server_dispatch(
        self,
        production_workspace: Path,
    ) -> None:
        tool_fn = _get_production_tool_fn("trw_build_check")

        with pytest.raises(ValueError, match="tests_passed is required"):
            tool_fn(run_path=str(production_workspace))

        events_file = next((production_workspace / "meta").glob("events-*.jsonl"))
        tool_rows = [row for row in _read_jsonl(events_file) if row["event_type"] == "tool_call"]
        assert tool_rows
        row = tool_rows[-1]
        assert row["payload"]["tool"] == "trw_build_check"
        assert row["payload"]["outcome"] == "error"
        assert row["payload"]["error_class"] == "ValueError"

    def test_session_start_and_surface_registered_fields_populated_via_production_tool(
        self,
        production_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tool_fn = _get_production_tool_fn("trw_session_start")
        monkeypatch.setattr("trw_mcp.telemetry.boot_audit.run_boot_audit", lambda **_: [])

        result = tool_fn()
        assert result["success"] is True

        events_file = next((production_workspace / "meta").glob("events-*.jsonl"))
        rows = _read_jsonl(events_file)
        session_rows = [row for row in rows if row["event_type"] == "session_start"]
        surface_rows = [row for row in rows if row["event_type"] == "surface_registered"]

        assert session_rows
        assert surface_rows

        session_row = session_rows[-1]
        assert session_row["session_id"]
        assert session_row["run_id"] == "run-123"
        assert session_row["surface_snapshot_id"]
        assert session_row["payload"]["framework_version"]
        assert session_row["payload"]["learnings_loaded"] >= 0

        surface_row = surface_rows[0]
        assert surface_row["session_id"]
        assert surface_row["run_id"] == "run-123"
        assert surface_row["surface_snapshot_id"]
        assert surface_row["payload"]["surface_id"]
        assert surface_row["payload"]["content_hash"]
        assert surface_row["payload"]["source_path"]
        assert surface_row["payload"]["category"]
