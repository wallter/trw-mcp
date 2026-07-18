"""Tests for tool_call_timing — PRD-HPO-MEAS-001 FR-4."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest

from trw_mcp.telemetry.event_base import ToolCallEvent, validate_parent_within_run
from trw_mcp.telemetry.tool_call_timing import (
    _usd_cost_estimate,
    build_tool_call_event,
    clear_pricing_cache,
    wrap_tool,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    clear_pricing_cache()
    yield
    clear_pricing_cache()


class TestBuildToolCallEvent:
    def test_basic_construction(self) -> None:
        start = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
        end = start + timedelta(milliseconds=250)
        ev = build_tool_call_event(
            tool="trw_recall",
            start_ts=start,
            end_ts=end,
            session_id="s1",
        )
        assert isinstance(ev, ToolCallEvent)
        assert ev.session_id == "s1"
        assert ev.payload["tool"] == "trw_recall"
        assert ev.payload["wall_ms"] == 250
        assert ev.payload["outcome"] == "success"

    def test_wall_ms_clamped_to_nonneg(self) -> None:
        start = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
        end = start - timedelta(milliseconds=5)  # clock skew
        ev = build_tool_call_event(
            tool="x",
            start_ts=start,
            end_ts=end,
            session_id="s1",
        )
        assert ev.payload["wall_ms"] == 0

    def test_includes_pricing_version(self) -> None:
        ev = build_tool_call_event(
            tool="x",
            start_ts=datetime.now(tz=timezone.utc),
            end_ts=datetime.now(tz=timezone.utc),
            session_id="s1",
        )
        assert "pricing_version" in ev.payload
        # Real pricing.yaml ships with a date-stamped version.
        assert ev.payload["pricing_version"]

    def test_propagates_error_class(self) -> None:
        ev = build_tool_call_event(
            tool="x",
            start_ts=datetime.now(tz=timezone.utc),
            end_ts=datetime.now(tz=timezone.utc),
            session_id="s1",
            outcome="error",
            error_class="RuntimeError",
        )
        assert ev.payload["outcome"] == "error"
        assert ev.payload["error_class"] == "RuntimeError"


class TestUsdCostEstimate:
    def test_zero_when_model_unknown(self) -> None:
        assert _usd_cost_estimate(model_id="gpt-5", input_tokens=1000, output_tokens=1000) == 0.0

    def test_zero_when_model_none(self) -> None:
        assert _usd_cost_estimate(model_id=None, input_tokens=1000, output_tokens=1000) == 0.0

    def test_opus_4_7_rate(self) -> None:
        # opus: $0.015/1K in, $0.075/1K out → 1000+1000 = 0.015 + 0.075 = 0.090
        usd = _usd_cost_estimate(model_id="claude-opus-4-7", input_tokens=1000, output_tokens=1000)
        assert usd == pytest.approx(0.090, abs=1e-6)


class TestWrapTool:
    def test_wrapper_returns_original_value(self) -> None:
        def my_tool(a: int, b: int) -> int:
            return a + b

        wrapped = wrap_tool(my_tool)
        assert wrapped(2, 3) == 5

    def test_wrapper_preserves_exception(self) -> None:
        def my_tool() -> int:
            raise RuntimeError("boom")

        wrapped = wrap_tool(my_tool)
        with pytest.raises(RuntimeError, match="boom"):
            wrapped()

    def test_wrapper_uses_explicit_tool_name(self) -> None:
        def anon() -> None:
            return None

        wrapped = wrap_tool(anon, tool_name="named_override")
        wrapped()
        assert wrapped.__name__ == "anon"  # functools.wraps preserves __name__

    def test_wrapper_calls_session_resolver(self) -> None:
        calls: list[bool] = []

        def resolver() -> str:
            calls.append(True)
            return "s42"

        def my_tool() -> int:
            return 1

        wrapped = wrap_tool(my_tool, session_id_resolver=resolver)
        wrapped()
        assert calls == [True]

    def test_wrapper_swallows_resolver_errors(self) -> None:
        def resolver() -> str:
            raise RuntimeError("resolver down")

        def my_tool() -> int:
            return 1

        wrapped = wrap_tool(my_tool, session_id_resolver=resolver)
        # Should NOT raise — wrapped tool's result is returned even if
        # session resolution blows up.
        assert wrapped() == 1


def test_build_tool_call_event_includes_canonical_trace_fields() -> None:
    start = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
    ev = build_tool_call_event(
        tool="trw_checkpoint",
        start_ts=start,
        end_ts=start + timedelta(milliseconds=10),
        session_id="s1",
        parent_event_id="evt_parent",
        tool_call_id="call-1",
        input_data={"message": "hello"},
        output_data={"ok": True},
        task_profile_hash="profile123",
    )

    assert ev.parent_event_id == "evt_parent"
    assert ev.payload["event_id"] == ev.event_id
    assert ev.payload["parent_event_id"] == "evt_parent"
    assert ev.payload["tool_call_id"] == "call-1"
    assert isinstance(ev.payload["turn_index"], int)
    assert isinstance(ev.payload["input_hash"], str)
    assert isinstance(ev.payload["output_hash"], str)
    assert ev.payload["task_profile_hash"] == "profile123"
    assert ev.payload["causal_relation"] == "nested"


_FR04_EXPECTED_TOOLS = frozenset(
    {
        "trw_session_start",
        "trw_status",
        "trw_heartbeat",
        "trw_adopt_run",
        "trw_pre_compact_checkpoint",
        "trw_init",
        "trw_prd_create",
        "trw_prd_validate",
        "trw_learn",
        "trw_learn_update",
        "trw_checkpoint",
        "trw_build_check",
        "trw_review",
        "trw_deliver",
        "trw_delivery_status",
        "trw_delivery_recover",
    }
)
# Only trw_deliver is operation_backed; delivery_status/recover READ the CORE-208
# journal but are synchronous_only (PRD-CORE-215 FR04 / §4 inventory).
_FR04_OPERATION_BACKED = frozenset({"trw_deliver"})
_FR04_CORE_208_OWNED = frozenset({"trw_deliver", "trw_delivery_status", "trw_delivery_recover"})


def test_prd_core_215_fr04() -> None:
    """PRD-CORE-215 FR04 — complete, fail-closed ceremony-tool execution inventory."""
    # Lookup public surface is re-exported through tool_call_timing.py (the PRD
    # reference); construction helpers live in the _ceremony_tool_manifest module.
    from trw_mcp.telemetry._ceremony_tool_manifest import (
        CeremonyToolSpec,
        DuplicateCeremonyToolError,
        build_ceremony_tool_manifest,
    )
    from trw_mcp.telemetry.tool_call_timing import (
        CeremonyExecutionClass,
        RequestIdentityPolicy,
        UnknownCeremonyToolError,
        ceremony_tool_disposition,
        ceremony_tool_names,
        ceremony_tool_spec,
    )

    # --- The inventory is EXACTLY the 16 named tools — no more, no fewer. ---
    assert ceremony_tool_names() == _FR04_EXPECTED_TOOLS
    assert len(_FR04_EXPECTED_TOOLS) == 16

    # --- Every named tool has one disposition, budget, policy, and owner. ---
    for name in _FR04_EXPECTED_TOOLS:
        spec = ceremony_tool_spec(name)
        assert isinstance(spec.disposition, CeremonyExecutionClass)
        assert spec.budget_seconds > 0
        assert isinstance(spec.request_identity, RequestIdentityPolicy)
        assert spec.owner, f"{name} has no owner store"
        assert ceremony_tool_disposition(name) is spec.disposition

    # --- Disposition classification is exact. Only trw_deliver is operation_backed. ---
    assert _FR04_OPERATION_BACKED == frozenset({"trw_deliver"})
    for name in _FR04_OPERATION_BACKED:
        spec = ceremony_tool_spec(name)
        assert spec.disposition is CeremonyExecutionClass.OPERATION_BACKED
        # operation_backed rows name the CORE-208 delivery journal owner.
        assert "CORE-208" in spec.owner
    # The delivery family all READ the CORE-208 journal (their authority), but
    # status/recover are synchronous_only — they do not mint their own handle.
    for name in _FR04_CORE_208_OWNED:
        assert "CORE-208" in ceremony_tool_spec(name).owner
    for name in ("trw_delivery_status", "trw_delivery_recover"):
        assert ceremony_tool_disposition(name) is CeremonyExecutionClass.SYNCHRONOUS_ONLY
    assert ceremony_tool_disposition("trw_prd_validate") is CeremonyExecutionClass.SYNCHRONOUS_BOUNDED
    synchronous_only = _FR04_EXPECTED_TOOLS - _FR04_OPERATION_BACKED - {"trw_prd_validate"}
    assert len(synchronous_only) == 14
    for name in synchronous_only:
        assert ceremony_tool_disposition(name) is CeremonyExecutionClass.SYNCHRONOUS_ONLY

    # --- Mutating rows require a request identity; read-only rows do not. ---
    assert ceremony_tool_spec("trw_learn").request_identity is RequestIdentityPolicy.REQUIRED
    assert ceremony_tool_spec("trw_status").request_identity is RequestIdentityPolicy.READ_ONLY
    assert ceremony_tool_spec("trw_prd_validate").request_identity is RequestIdentityPolicy.READ_ONLY

    # --- Unknown tool fails closed (typed error, never a default disposition). ---
    with pytest.raises(UnknownCeremonyToolError):
        ceremony_tool_disposition("trw_not_a_real_tool")
    with pytest.raises(UnknownCeremonyToolError):
        ceremony_tool_spec("trw_not_a_real_tool")

    # --- A tool cannot be operation_backed without an owner. ---
    with pytest.raises(ValueError, match="operation_backed requires an owner"):
        CeremonyToolSpec(
            "trw_ghost",
            CeremonyExecutionClass.OPERATION_BACKED,
            1.0,
            RequestIdentityPolicy.REQUIRED,
            "",
        )

    # --- Duplicate registration fails. ---
    dup = ceremony_tool_spec("trw_deliver")
    with pytest.raises(DuplicateCeremonyToolError):
        build_ceremony_tool_manifest([dup, dup])


def test_prd_core_215_nfr02() -> None:
    """PRD-CORE-215 NFR02 — bounded response: every ceremony manifest row declares
    a positive, finite budget (so no operation runs unbounded), and the typed
    envelope's bounded-diagnostics validators cap entry count and value length so
    a returned handle/result can never smuggle an unbounded payload."""
    import math

    from pydantic import ValidationError

    from trw_mcp.models.tool_result import (
        MAX_DIAGNOSTIC_ENTRIES,
        MAX_DIAGNOSTIC_VALUE_CHARS,
        Outcome,
        ToolResultEnvelope,
    )
    from trw_mcp.telemetry.tool_call_timing import ceremony_tool_names, ceremony_tool_spec

    # Every classified tool has a positive, finite response budget.
    names = ceremony_tool_names()
    assert names  # non-empty inventory
    for name in names:
        budget = ceremony_tool_spec(name).budget_seconds
        assert budget > 0, name
        assert math.isfinite(budget), name

    # Bounded diagnostics: exceeding the entry cap is rejected...
    too_many = {f"k{i}": "v" for i in range(MAX_DIAGNOSTIC_ENTRIES + 1)}
    with pytest.raises(ValidationError):
        ToolResultEnvelope(outcome=Outcome.COMPLETED, diagnostics=too_many)
    # ...and so is an over-long diagnostic value.
    with pytest.raises(ValidationError):
        ToolResultEnvelope(
            outcome=Outcome.COMPLETED,
            diagnostics={"note": "x" * (MAX_DIAGNOSTIC_VALUE_CHARS + 1)},
        )
    # A within-bounds envelope builds and stays bounded (positive control).
    ok = ToolResultEnvelope(outcome=Outcome.COMPLETED, diagnostics={"note": "ok"})
    assert len(ok.diagnostics) <= MAX_DIAGNOSTIC_ENTRIES
    assert all(len(v) <= MAX_DIAGNOSTIC_VALUE_CHARS for v in ok.diagnostics.values())


def test_tool_call_events_validate_parent_chain() -> None:
    start = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
    root = build_tool_call_event(tool="root", start_ts=start, end_ts=start, session_id="s1", run_id="r1")
    child = build_tool_call_event(
        tool="child",
        start_ts=start,
        end_ts=start,
        session_id="s1",
        run_id="r1",
        parent_event_id=root.event_id,
    )

    assert validate_parent_within_run([root, child], run_id="r1") == []
