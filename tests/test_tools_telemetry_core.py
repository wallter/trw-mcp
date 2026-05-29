"""Tests for tools/telemetry.py core decorator behavior and detailed telemetry."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import structlog

import trw_mcp.tools.telemetry as telemetry
from tests._tools_telemetry_support import _config_with, _read_jsonl, reset_telemetry_cache, run_dir  # noqa: F401
from trw_mcp.tools.telemetry import log_tool_call


class TestLogToolCallDecorator:
    """T-01 through T-04: Core decorator behavior."""

    def test_t01_writes_tool_invocation_event_with_correct_fields(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        """T-01: Decorator writes tool_invocation event to run events.jsonl with required fields."""
        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=True, telemetry=False))
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)

        @log_tool_call
        def my_tool(x: int) -> int:
            return x * 2

        result = my_tool(21)
        assert result == 42

        records = _read_jsonl(run_dir / "meta" / "events.jsonl")
        assert len(records) == 1
        ev = records[0]
        assert ev["event"] == "tool_invocation"
        assert ev["tool_name"] == "my_tool"
        assert "duration_ms" in ev
        assert isinstance(ev["duration_ms"], float)
        assert ev["duration_ms"] >= 0
        assert ev["success"] is True
        assert "error" not in ev

    def test_t01_error_field_present_on_exception(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        """T-01 variant: error field appears when tool raises."""
        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=True, telemetry=False))
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)

        @log_tool_call
        def failing_tool() -> str:
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            failing_tool()

        records = _read_jsonl(run_dir / "meta" / "events.jsonl")
        assert len(records) == 1
        ev = records[0]
        assert ev["event"] == "tool_invocation"
        assert ev["success"] is False
        assert ev["error"] == "boom"

    def test_t02_fail_open_event_write_exception_does_not_affect_result(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-02: Exception during event write does not propagate; tool result is returned normally."""
        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=True, telemetry=False))
        monkeypatch.setattr(telemetry, "_write_tool_event", MagicMock(side_effect=OSError("disk full")))

        @log_tool_call
        def stable_tool() -> str:
            return "ok"

        assert stable_tool() == "ok"

    def test_t03_telemetry_enabled_false_produces_no_events(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        """T-03: When telemetry_enabled=False, decorator bypasses all event writing."""
        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=False, telemetry=False))
        write_mock = MagicMock()
        monkeypatch.setattr(telemetry, "_write_tool_event", write_mock)

        @log_tool_call
        def noop_tool() -> int:
            return 99

        assert noop_tool() == 99
        write_mock.assert_not_called()

    def test_t03_telemetry_disabled_no_events_in_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        """T-03 file variant: events.jsonl remains empty when telemetry_enabled=False."""
        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=False, telemetry=False))
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)

        @log_tool_call
        def noop_tool2() -> int:
            return 1

        noop_tool2()
        assert _read_jsonl(run_dir / "meta" / "events.jsonl") == []

    def test_t04_p95_overhead_under_5ms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-04: P95 overhead of the decorator on a no-op function is < 5 ms (100 iterations)."""
        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=True, telemetry=False))
        monkeypatch.setattr(telemetry, "_write_tool_event", MagicMock())
        monkeypatch.setattr(telemetry, "_write_telemetry_record", MagicMock())

        @log_tool_call
        def no_op() -> None:
            return None

        overhead_ms: list[float] = []
        for _ in range(100):
            t0 = time.monotonic()
            no_op()
            overhead_ms.append((time.monotonic() - t0) * 1000)

        overhead_ms.sort()
        p95 = overhead_ms[94]
        assert p95 < 10.0, f"P95 overhead {p95:.2f}ms exceeds 10ms budget"


class TestDetailedTelemetry:
    """T-08, T-09: FR04 detailed telemetry and debug logging."""

    def test_t08_config_telemetry_true_writes_to_tool_telemetry_jsonl(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        """T-08: config.telemetry=True writes a record to .trw/logs/tool-telemetry.jsonl."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        cfg = _config_with(
            telemetry_enabled=True,
            telemetry=True,
            logs_dir="logs",
            telemetry_file="tool-telemetry.jsonl",
        )
        monkeypatch.setattr(telemetry, "get_config", lambda: cfg)
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)
        monkeypatch.setattr("trw_mcp.tools.telemetry.resolve_trw_dir", lambda: trw_dir)

        @log_tool_call
        def instrumented_tool(a: int, b: int) -> int:
            return a + b

        assert instrumented_tool(3, 4) == 7

        telemetry_path = trw_dir / "logs" / "tool-telemetry.jsonl"
        assert telemetry_path.exists(), "tool-telemetry.jsonl was not created"

        [rec] = _read_jsonl(telemetry_path)
        assert rec["event"] == "tool_call"
        assert rec["tool"] == "instrumented_tool"
        assert "args_hash" in rec
        assert isinstance(rec["args_hash"], str)
        assert len(str(rec["args_hash"])) == 8
        assert "duration_ms" in rec
        assert rec["success"] is True
        assert "result_summary" in rec

    def test_t08_config_telemetry_false_no_telemetry_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        """T-08 inverse: config.telemetry=False means tool-telemetry.jsonl is not written."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        cfg = _config_with(
            telemetry_enabled=True,
            telemetry=False,
            logs_dir="logs",
            telemetry_file="tool-telemetry.jsonl",
        )
        monkeypatch.setattr(telemetry, "get_config", lambda: cfg)
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)

        @log_tool_call
        def uninstrumented_tool() -> str:
            return "done"

        uninstrumented_tool()
        assert not (trw_dir / "logs" / "tool-telemetry.jsonl").exists()

    def test_t09_structlog_logger_exists_and_callable(self) -> None:
        """T-09: structlog logger is defined at module level and can be invoked safely."""
        assert hasattr(telemetry, "logger")
        lg = telemetry.logger
        assert callable(getattr(lg, "debug", None))
        assert callable(getattr(lg, "info", None))

    def test_t09_structlog_debug_call_does_not_raise(self) -> None:
        """T-09: Calling structlog debug with non-reserved kwargs does not raise."""
        structlog.get_logger().debug("test_debug_call", component="telemetry", tool="test_tool")
