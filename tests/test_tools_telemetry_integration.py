"""Tests for telemetry fallback, integration flow, and trace fields."""

from __future__ import annotations

from pathlib import Path

import pytest

import trw_mcp.tools.telemetry as telemetry
from tests._tools_telemetry_support import _config_with, _read_jsonl, reset_telemetry_cache, run_dir  # noqa: F401
from trw_mcp.tools.telemetry import log_tool_call


class TestWriteToolEventFallback:
    """Verify _write_tool_event uses session-events.jsonl when no run is active."""

    def test_fallback_creates_session_events_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When run_dir is None, _write_tool_event writes to context/session-events.jsonl."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)

        monkeypatch.setattr(
            telemetry,
            "get_config",
            lambda: _config_with(telemetry_enabled=True, telemetry=False, context_dir="context"),
        )
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: None)
        monkeypatch.setattr("trw_mcp.tools.telemetry.resolve_trw_dir", lambda: trw_dir)

        @log_tool_call
        def fallback_tool() -> str:
            return "fallback"

        fallback_tool()

        records = _read_jsonl(trw_dir / "context" / "session-events.jsonl")
        assert len(records) == 1
        assert records[0]["event"] == "tool_invocation"
        assert records[0]["tool_name"] == "fallback_tool"
        assert records[0]["success"] is True

    def test_fallback_skipped_if_run_dir_meta_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        """When run_dir exists but meta/ does not, code falls through to session-events.jsonl."""
        import shutil

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        shutil.rmtree(run_dir / "meta")

        monkeypatch.setattr(
            telemetry,
            "get_config",
            lambda: _config_with(telemetry_enabled=True, telemetry=False, context_dir="context"),
        )
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)
        monkeypatch.setattr("trw_mcp.tools.telemetry.resolve_trw_dir", lambda: trw_dir)

        @log_tool_call
        def partial_run_tool() -> str:
            return "partial"

        partial_run_tool()

        records = _read_jsonl(trw_dir / "context" / "session-events.jsonl")
        assert len(records) >= 1
        assert records[0]["event"] == "tool_invocation"


class TestTelemetryIntegration:
    """Integration tests for telemetry decorator and session_start event."""

    def test_t18_full_ceremony_flow_produces_events(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-18: session_start + checkpoint + deliver flow produces ceremony events."""
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260220T120000Z-integ001"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: integ-test\nstatus: active\nphase: implement\ntask_name: test\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=True, telemetry=False))
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)

        @log_tool_call
        def trw_session_start() -> dict[str, str]:
            return {"status": "ok"}

        @log_tool_call
        def trw_checkpoint() -> dict[str, str]:
            return {"status": "ok"}

        @log_tool_call
        def trw_deliver() -> dict[str, str]:
            return {"status": "ok"}

        trw_session_start()
        trw_checkpoint()
        trw_deliver()

        tool_names = [
            str(r.get("tool_name", ""))
            for r in _read_jsonl(run_dir / "meta" / "events.jsonl")
            if r.get("event") == "tool_invocation"
        ]
        assert len(tool_names) == 3
        assert "trw_session_start" in tool_names
        assert "trw_checkpoint" in tool_names
        assert "trw_deliver" in tool_names

    def test_t21_telemetry_kill_switch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        """T-21: telemetry_enabled=False prevents all tool_invocation events."""
        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=False, telemetry=False))
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)

        @log_tool_call
        def guarded_tool() -> str:
            return "result"

        guarded_tool()
        guarded_tool()
        guarded_tool()

        tool_events = [r for r in _read_jsonl(run_dir / "meta" / "events.jsonl") if r.get("event") == "tool_invocation"]
        assert len(tool_events) == 0

    def test_t23_meta_dir_removed_during_session(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-23: meta/ removed during session — decorator fails silently, tool returns normally."""
        import shutil

        rd = tmp_path / "docs" / "task" / "runs" / "20260220T120000Z-vanish01"
        (rd / "meta").mkdir(parents=True)
        (rd / "meta" / "run.yaml").write_text("run_id: vanish\nstatus: active\n", encoding="utf-8")
        (rd / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)

        monkeypatch.setattr(
            telemetry,
            "get_config",
            lambda: _config_with(telemetry_enabled=True, telemetry=False, context_dir="context"),
        )
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: rd)
        monkeypatch.setattr("trw_mcp.tools.telemetry.resolve_trw_dir", lambda: trw_dir)
        shutil.rmtree(rd / "meta")

        @log_tool_call
        def resilient_tool() -> str:
            return "still works"

        assert resilient_tool() == "still works"


class TestToolTraceFields:
    """PRD-CORE-154: legacy tool_invocation events carry DAG trace fields."""

    def test_tool_invocation_includes_trace_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=True, telemetry=False))
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)

        @log_tool_call
        def traced_tool(secret: str) -> str:
            return f"processed:{len(secret)}"

        assert traced_tool("do-not-log") == "processed:10"
        [event] = _read_jsonl(run_dir / "meta" / "events.jsonl")

        assert isinstance(event["event_id"], str)
        assert event["parent_event_id"] is None
        assert isinstance(event["tool_call_id"], str)
        assert isinstance(event["turn_index"], int)
        assert isinstance(event["input_hash"], str)
        assert isinstance(event["output_hash"], str)
        assert event["task_profile_hash"] == ""
        assert event["causal_relation"] == "root"
        assert "do-not-log" not in str(event)

    def test_tool_invocation_reads_task_profile_hash_from_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=True, telemetry=False))
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)
        (run_dir / "meta" / "run.yaml").write_text(
            "phase: implement\ntask_profile:\n  profile_hash: abc123profile\n",
            encoding="utf-8",
        )

        @log_tool_call
        def profiled_tool() -> str:
            return "ok"

        assert profiled_tool() == "ok"
        [event] = _read_jsonl(run_dir / "meta" / "events.jsonl")
        assert event["task_profile_hash"] == "abc123profile"

    def test_tool_event_has_normalized_profile_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=True, telemetry=False))
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)
        (run_dir / "meta" / "run.yaml").write_text(
            "\n".join(
                [
                    "phase: implement",
                    "task_profile:",
                    "  profile_hash: normalized-profile",
                    "  capability_tier: frontier",
                    "  recommended_effort: high",
                    "  effort_source: task_complexity",
                    "  effort_adapter_status: advisory",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        @log_tool_call
        def profiled_tool() -> str:
            return "ok"

        assert profiled_tool() == "ok"
        [event] = _read_jsonl(run_dir / "meta" / "events.jsonl")
        assert event["capability_tier"] == "frontier"
        assert event["recommended_effort"] == "high"
        assert event["effort_source"] == "task_complexity"
        assert event["effort_adapter_status"] == "advisory"
        assert "model_tier" not in event
        assert "reasoning_effort" not in event

    def test_tool_event_reads_legacy_profile_fields_without_claiming_application(
        self,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=True, telemetry=False))
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)
        (run_dir / "meta" / "run.yaml").write_text(
            "phase: implement\ntask_profile:\n  model_tier: balanced\n  reasoning_effort: medium\n",
            encoding="utf-8",
        )

        telemetry._write_tool_event("legacy_profile_tool", 1.0, True, None)

        [event] = _read_jsonl(run_dir / "meta" / "events.jsonl")
        assert event["capability_tier"] == "balanced"
        assert event["recommended_effort"] == "medium"
        assert event["effort_source"] == ""
        assert event["effort_adapter_status"] == ""

    def test_direct_tool_event_reads_task_profile_hash_without_full_trace(
        self,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=True, telemetry=False))
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda call_ctx=None: run_dir)
        (run_dir / "meta" / "run.yaml").write_text(
            "phase: implement\ntask_profile:\n  profile_hash: direct-profile\n",
            encoding="utf-8",
        )

        telemetry._write_tool_event("direct_tool", 1.0, True, None)

        [event] = _read_jsonl(run_dir / "meta" / "events.jsonl")
        assert event["tool_name"] == "direct_tool"
        assert event["task_profile_hash"] == "direct-profile"

    def test_nested_tool_invocation_sets_parent_event_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=True, telemetry=False))
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)

        @log_tool_call
        def inner_tool() -> str:
            return "inner"

        @log_tool_call
        def outer_tool() -> str:
            return f"outer:{inner_tool()}"

        assert outer_tool() == "outer:inner"
        events = _read_jsonl(run_dir / "meta" / "events.jsonl")
        by_tool = {str(event["tool_name"]): event for event in events}

        assert by_tool["outer_tool"]["parent_event_id"] is None
        assert by_tool["inner_tool"]["parent_event_id"] == by_tool["outer_tool"]["event_id"]
        assert by_tool["inner_tool"]["causal_relation"] == "nested"
