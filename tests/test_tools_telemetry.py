"""Tests for tools/telemetry.py — @log_tool_call decorator (PRD-CORE-031-FR02).

Covers:
- T-01: Decorator writes tool_invocation event with correct fields
- T-02: Fail-open — event write exception does not affect tool result
- T-03: telemetry_enabled=False produces no events
- T-04: P95 overhead < 5ms (100 iterations on no-op function)
- T-05: session_start event in events.jsonl when active run exists (FR01)
- T-06: session_start fallback to session-events.jsonl when no active run
- T-07: Event write failure does not cause trw_session_start to return an error
- T-08: config.telemetry=True writes to .trw/logs/tool-telemetry.jsonl (FR04)
- T-09: config.debug=True — structlog logger exists and can be called
- T-25: Decorated tool still discoverable by FastMCP
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import structlog
from fastmcp import FastMCP

from tests.conftest import get_tools_sync

import trw_mcp.tools.telemetry as telemetry
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools.telemetry import log_tool_call

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    """Read a JSONL file and return list of parsed records."""
    reader = FileStateReader()
    return reader.read_jsonl(path)


def _make_ceremony_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Create a FastMCP server with ceremony tools registered and project root patched."""
    from trw_mcp.tools.ceremony import register_ceremony_tools

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    srv = FastMCP("test")
    register_ceremony_tools(srv)
    return get_tools_sync(srv)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_telemetry_cache() -> None:
    """Reset the module-level run-dir cache before each test to avoid inter-test pollution."""
    telemetry._cached_run_dir = (0.0, None)


@pytest.fixture()
def trw_root(tmp_path: Path) -> Path:
    """Minimal .trw directory structure for telemetry tests."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "logs").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory with meta/events.jsonl ready."""
    d = tmp_path / "docs" / "task" / "runs" / "20260220T120000Z-abcd1234"
    (d / "meta").mkdir(parents=True)
    (d / "meta" / "run.yaml").write_text(
        "run_id: test-run\nstatus: active\nphase: implement\ntask_name: test-task\n",
        encoding="utf-8",
    )
    (d / "meta" / "events.jsonl").write_text("", encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# T-01 through T-04: Core decorator behaviour
# ---------------------------------------------------------------------------


class TestLogToolCallDecorator:
    """T-01 through T-04: Core decorator behavior."""

    def test_t01_writes_tool_invocation_event_with_correct_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_dir: Path,
    ) -> None:
        """T-01: Decorator writes tool_invocation event to run events.jsonl with required fields."""
        # Point the telemetry module at our tmp_path as project root so
        # resolve_trw_dir returns tmp_path / ".trw" and find_active_run
        # returns our prepared run_dir.
        monkeypatch.setattr(telemetry, "_config", _config_with(telemetry_enabled=True, telemetry=False))
        monkeypatch.setattr(
            telemetry, "_get_cached_run_dir", lambda: run_dir,
        )

        @log_tool_call
        def my_tool(x: int) -> int:
            return x * 2

        result = my_tool(21)
        assert result == 42

        events_path = run_dir / "meta" / "events.jsonl"
        records = _read_jsonl(events_path)
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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_dir: Path,
    ) -> None:
        """T-01 variant: error field appears when tool raises."""
        monkeypatch.setattr(telemetry, "_config", _config_with(telemetry_enabled=True, telemetry=False))
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
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-02: Exception during event write does not propagate; tool result is returned normally."""
        monkeypatch.setattr(telemetry, "_config", _config_with(telemetry_enabled=True, telemetry=False))

        # Make _write_tool_event raise to simulate a broken filesystem
        monkeypatch.setattr(
            telemetry,
            "_write_tool_event",
            MagicMock(side_effect=OSError("disk full")),
        )

        @log_tool_call
        def stable_tool() -> str:
            return "ok"

        # Must not raise; result must be the original return value
        result = stable_tool()
        assert result == "ok"

    def test_t03_telemetry_enabled_false_produces_no_events(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_dir: Path,
    ) -> None:
        """T-03: When telemetry_enabled=False, decorator bypasses all event writing."""
        monkeypatch.setattr(
            telemetry, "_config", _config_with(telemetry_enabled=False, telemetry=False),
        )
        # Patch _write_tool_event to assert it is never called
        write_mock = MagicMock()
        monkeypatch.setattr(telemetry, "_write_tool_event", write_mock)

        @log_tool_call
        def noop_tool() -> int:
            return 99

        result = noop_tool()
        assert result == 99
        write_mock.assert_not_called()

    def test_t03_telemetry_disabled_no_events_in_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_dir: Path,
    ) -> None:
        """T-03 file variant: events.jsonl remains empty when telemetry_enabled=False."""
        monkeypatch.setattr(
            telemetry, "_config", _config_with(telemetry_enabled=False, telemetry=False),
        )
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)

        @log_tool_call
        def noop_tool2() -> int:
            return 1

        noop_tool2()

        events_path = run_dir / "meta" / "events.jsonl"
        records = _read_jsonl(events_path)
        assert records == []

    def test_t04_p95_overhead_under_5ms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-04: P95 overhead of the decorator on a no-op function is < 5 ms (100 iterations)."""
        monkeypatch.setattr(telemetry, "_config", _config_with(telemetry_enabled=True, telemetry=False))
        # Suppress actual disk writes so we measure pure decorator overhead
        monkeypatch.setattr(telemetry, "_write_tool_event", MagicMock())
        monkeypatch.setattr(telemetry, "_write_telemetry_record", MagicMock())

        @log_tool_call
        def no_op() -> None:
            return None

        overhead_ms: list[float] = []
        for _ in range(100):
            t0 = time.monotonic()
            no_op()
            elapsed = (time.monotonic() - t0) * 1000
            overhead_ms.append(elapsed)

        overhead_ms.sort()
        p95 = overhead_ms[94]  # 0-indexed: index 94 is the 95th percentile of 100 samples
        assert p95 < 5.0, f"P95 overhead {p95:.2f}ms exceeds 5ms budget"


# ---------------------------------------------------------------------------
# Helpers for config patching
# ---------------------------------------------------------------------------


def _config_with(**overrides: object) -> Any:
    """Return a copy of the live TRWConfig with attribute overrides applied."""
    from trw_mcp.models.config import TRWConfig
    cfg = TRWConfig()
    for attr, val in overrides.items():
        object.__setattr__(cfg, attr, val)
    return cfg


# ---------------------------------------------------------------------------
# T-05 through T-07: FR01 session_start event logging
# ---------------------------------------------------------------------------


class TestSessionStartEvent:
    """T-05 through T-07: FR01 session_start event logging."""

    def _setup_trw_project(self, tmp_path: Path) -> Path:
        """Create minimal .trw/ structure needed for trw_session_start."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        (trw_dir / "logs").mkdir(parents=True)
        return trw_dir

    def test_t05_session_start_event_written_to_run_events_jsonl(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_dir: Path,
    ) -> None:
        """T-05: session_start event is written to run's events.jsonl when a run exists."""
        trw_dir = self._setup_trw_project(tmp_path)
        tools = _make_ceremony_tools(monkeypatch, tmp_path)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is True

        events_path = run_dir / "meta" / "events.jsonl"
        records = _read_jsonl(events_path)
        session_events = [r for r in records if r.get("event") == "session_start"]
        assert len(session_events) == 1

        ev = session_events[0]
        assert "ts" in ev
        assert "learnings_recalled" in ev
        assert "run_detected" in ev
        assert ev["run_detected"] is True

    def test_t06_session_start_fallback_to_session_events_jsonl(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-06: When no active run, session_start event falls back to session-events.jsonl."""
        trw_dir = self._setup_trw_project(tmp_path)
        tools = _make_ceremony_tools(monkeypatch, tmp_path)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is True

        fallback_path = trw_dir / "context" / "session-events.jsonl"
        assert fallback_path.exists(), "session-events.jsonl fallback was not created"

        records = _read_jsonl(fallback_path)
        session_events = [r for r in records if r.get("event") == "session_start"]
        assert len(session_events) == 1

        ev = session_events[0]
        assert ev["run_detected"] is False
        assert "learnings_recalled" in ev

    def test_t07_event_write_failure_does_not_cause_session_start_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-07: Event write failure in FR01 block does not affect trw_session_start result."""
        trw_dir = self._setup_trw_project(tmp_path)
        tools = _make_ceremony_tools(monkeypatch, tmp_path)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
            # Make the FileEventLogger.log_event raise inside the ceremony module
            patch(
                "trw_mcp.tools.ceremony._events",
                MagicMock(log_event=MagicMock(side_effect=OSError("write failure"))),
            ),
        ):
            result = tools["trw_session_start"].fn()

        # Tool must succeed despite the event write failure
        assert result["success"] is True
        # No error attributable to the event write should appear
        assert all("session_start" not in e for e in result.get("errors", []))

    def test_t07_second_resolve_trw_dir_failure_is_silenced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-07 variant: If resolve_trw_dir raises inside FR01 fallback block, no error surfaces."""
        trw_dir = self._setup_trw_project(tmp_path)
        tools = _make_ceremony_tools(monkeypatch, tmp_path)

        call_count: list[int] = [0]

        def resolve_trw_dir_side_effect() -> Path:
            call_count[0] += 1
            if call_count[0] == 1:
                # First call (Step 1 recall) succeeds
                return trw_dir
            # Subsequent calls (in FR01 block) fail
            raise OSError("no trw dir")

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", side_effect=resolve_trw_dir_side_effect),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
        ):
            result = tools["trw_session_start"].fn()

        # Tool must succeed — FR01 block failure is caught by the bare except/pass
        assert isinstance(result, dict)
        assert "success" in result


# ---------------------------------------------------------------------------
# T-08, T-09: FR04 detailed telemetry and debug logging
# ---------------------------------------------------------------------------


class TestDetailedTelemetry:
    """T-08, T-09: FR04 detailed telemetry and debug logging."""

    def test_t08_config_telemetry_true_writes_to_tool_telemetry_jsonl(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_dir: Path,
    ) -> None:
        """T-08: config.telemetry=True writes a record to .trw/logs/tool-telemetry.jsonl."""
        trw_dir = tmp_path / ".trw"
        logs_dir = trw_dir / "logs"
        logs_dir.mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        cfg = _config_with(
            telemetry_enabled=True,
            telemetry=True,  # FR04 flag
            logs_dir="logs",
            telemetry_file="tool-telemetry.jsonl",
        )
        monkeypatch.setattr(telemetry, "_config", cfg)
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)
        monkeypatch.setattr(
            "trw_mcp.tools.telemetry.resolve_trw_dir", lambda: trw_dir,
        )

        @log_tool_call
        def instrumented_tool(a: int, b: int) -> int:
            return a + b

        result = instrumented_tool(3, 4)
        assert result == 7

        telemetry_path = trw_dir / "logs" / "tool-telemetry.jsonl"
        assert telemetry_path.exists(), "tool-telemetry.jsonl was not created"

        records = _read_jsonl(telemetry_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["event"] == "tool_call"
        assert rec["tool"] == "instrumented_tool"
        assert "args_hash" in rec
        assert isinstance(rec["args_hash"], str)
        assert len(str(rec["args_hash"])) == 8
        assert "duration_ms" in rec
        assert rec["success"] is True
        assert "result_summary" in rec

    def test_t08_config_telemetry_false_no_telemetry_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_dir: Path,
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
        monkeypatch.setattr(telemetry, "_config", cfg)
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)

        @log_tool_call
        def uninstrumented_tool() -> str:
            return "done"

        uninstrumented_tool()

        telemetry_path = trw_dir / "logs" / "tool-telemetry.jsonl"
        assert not telemetry_path.exists(), "tool-telemetry.jsonl should not be written when telemetry=False"

    def test_t09_structlog_logger_exists_and_callable(self) -> None:
        """T-09: structlog logger is defined at module level and can be invoked safely."""
        # The telemetry module creates a structlog logger at import time.
        # Verify it is a bound logger (or equivalent) that can be called.
        assert hasattr(telemetry, "logger")
        lg = telemetry.logger
        # structlog loggers expose debug/info/warning/error callables
        assert callable(getattr(lg, "debug", None))
        assert callable(getattr(lg, "info", None))

    def test_t09_structlog_debug_call_does_not_raise(self) -> None:
        """T-09: Calling structlog debug with non-reserved kwargs does not raise."""
        lg = structlog.get_logger()
        # Must not raise — note 'event' is reserved; use 'msg' or descriptive kwarg
        lg.debug("test_debug_call", component="telemetry", tool="test_tool")


# ---------------------------------------------------------------------------
# T-25: FastMCP tool discovery
# ---------------------------------------------------------------------------


class TestToolDiscovery:
    """T-25: Decorated tools remain discoverable by FastMCP."""

    def test_t25_log_tool_call_preserves_function_name(self) -> None:
        """T-25: functools.wraps ensures the decorated function retains its __name__."""

        @log_tool_call
        def my_discoverable_tool() -> str:
            return "result"

        assert my_discoverable_tool.__name__ == "my_discoverable_tool"

    def test_t25_log_tool_call_preserves_docstring(self) -> None:
        """T-25: functools.wraps ensures docstring is preserved for FastMCP schema generation."""

        @log_tool_call
        def documented_tool() -> str:
            """Return a fixed string value."""
            return "value"

        assert documented_tool.__doc__ == "Return a fixed string value."

    def test_t25_decorated_tool_registers_with_fastmcp(self) -> None:
        """T-25: A @log_tool_call-decorated function can be registered as an MCP tool."""
        srv = FastMCP("discovery-test")

        @srv.tool()
        @log_tool_call
        def api_tool(message: str) -> dict[str, str]:
            """Echo the message back."""
            return {"echo": message}

        registered = get_tools_sync(srv)
        assert "api_tool" in registered

    def test_t25_registered_tool_is_callable_via_fastmcp(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-25: A registered decorated tool can be invoked through FastMCP's tool manager."""
        monkeypatch.setattr(
            telemetry, "_config", _config_with(telemetry_enabled=False, telemetry=False),
        )

        srv = FastMCP("discovery-invoke-test")

        @srv.tool()
        @log_tool_call
        def compute_tool(x: int) -> int:
            """Multiply x by 3."""
            return x * 3

        registered = get_tools_sync(srv)
        result = registered["compute_tool"].fn(x=5)
        assert result == 15

    def test_t25_ceremony_tools_discoverable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-25: trw_session_start and trw_deliver decorated with @log_tool_call are registered."""
        tools = _make_ceremony_tools(monkeypatch, tmp_path)
        assert "trw_session_start" in tools
        assert "trw_deliver" in tools

    def test_t25_tool_call_with_kwargs_works_correctly(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-25: Decorated tool accepts keyword arguments and returns correct result."""
        monkeypatch.setattr(
            telemetry, "_config", _config_with(telemetry_enabled=False, telemetry=False),
        )

        @log_tool_call
        def kwargs_tool(a: int = 0, b: int = 0) -> int:
            return a + b

        assert kwargs_tool(a=10, b=20) == 30
        assert kwargs_tool(5, 7) == 12


# ---------------------------------------------------------------------------
# Additional edge-case tests for _get_cached_run_dir
# ---------------------------------------------------------------------------


class TestRunDirCache:
    """Verify the TTL cache in _get_cached_run_dir behaves correctly."""

    def test_cache_is_used_within_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When cache is fresh, find_active_run is not called again."""
        sentinel_path = Path("/sentinel/run")
        # Seed the cache with a fresh timestamp so TTL has not expired
        telemetry._cached_run_dir = (time.monotonic(), sentinel_path)

        call_count: list[int] = [0]

        def counting_find() -> Path | None:
            call_count[0] += 1
            return None

        monkeypatch.setattr(telemetry, "find_active_run", counting_find)

        result = telemetry._get_cached_run_dir()
        assert result is sentinel_path
        assert call_count[0] == 0, "find_active_run should not be called when cache is fresh"

    def test_cache_refreshes_after_ttl_expires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When cache timestamp is older than TTL, find_active_run is called to refresh."""
        # Seed cache with an expired timestamp
        telemetry._cached_run_dir = (0.0, Path("/stale/run"))

        fresh_path = Path("/fresh/run")

        def mock_find() -> Path:
            return fresh_path

        monkeypatch.setattr(telemetry, "find_active_run", mock_find)

        result = telemetry._get_cached_run_dir()
        assert result is fresh_path

    def test_cache_stores_new_value_after_refresh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After a refresh, the cached value is updated to the new result."""
        telemetry._cached_run_dir = (0.0, None)
        new_path = Path("/new/run/dir")
        monkeypatch.setattr(telemetry, "find_active_run", lambda: new_path)

        telemetry._get_cached_run_dir()

        _ts, cached = telemetry._cached_run_dir
        assert cached is new_path


# ---------------------------------------------------------------------------
# Additional coverage: _write_tool_event fallback path (no active run)
# ---------------------------------------------------------------------------


class TestWriteToolEventFallback:
    """Verify _write_tool_event uses session-events.jsonl when no run is active."""

    def test_fallback_creates_session_events_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When run_dir is None, _write_tool_event writes to context/session-events.jsonl."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)

        monkeypatch.setattr(telemetry, "_config", _config_with(
            telemetry_enabled=True,
            telemetry=False,
            context_dir="context",
        ))
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: None)
        monkeypatch.setattr("trw_mcp.tools.telemetry.resolve_trw_dir", lambda: trw_dir)

        @log_tool_call
        def fallback_tool() -> str:
            return "fallback"

        fallback_tool()

        fallback_path = trw_dir / "context" / "session-events.jsonl"
        assert fallback_path.exists()
        records = _read_jsonl(fallback_path)
        assert len(records) == 1
        assert records[0]["event"] == "tool_invocation"
        assert records[0]["tool_name"] == "fallback_tool"
        assert records[0]["success"] is True

    def test_fallback_skipped_if_run_dir_meta_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_dir: Path,
    ) -> None:
        """When run_dir exists but meta/ does not, code falls through to session-events.jsonl."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)

        # Remove meta directory to simulate a partially-created run
        import shutil
        shutil.rmtree(run_dir / "meta")

        monkeypatch.setattr(telemetry, "_config", _config_with(
            telemetry_enabled=True,
            telemetry=False,
            context_dir="context",
        ))
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)
        monkeypatch.setattr("trw_mcp.tools.telemetry.resolve_trw_dir", lambda: trw_dir)

        @log_tool_call
        def partial_run_tool() -> str:
            return "partial"

        partial_run_tool()

        # Should have fallen back to session-events.jsonl
        fallback_path = trw_dir / "context" / "session-events.jsonl"
        assert fallback_path.exists()
        records = _read_jsonl(fallback_path)
        assert len(records) >= 1
        assert records[0]["event"] == "tool_invocation"


# ---------------------------------------------------------------------------
# Integration tests T-18, T-21, T-23 (Finding 2)
# ---------------------------------------------------------------------------


class TestTelemetryIntegration:
    """Integration tests for telemetry decorator and session_start event."""

    def test_t18_full_ceremony_flow_produces_events(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-18: session_start + checkpoint + deliver flow produces ceremony events."""
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260220T120000Z-integ001"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: integ-test\nstatus: active\nphase: implement\ntask_name: test\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        monkeypatch.setattr(telemetry, "_config", _config_with(
            telemetry_enabled=True, telemetry=False,
        ))
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

        records = _read_jsonl(run_dir / "meta" / "events.jsonl")
        tool_events = [r for r in records if r.get("event") == "tool_invocation"]
        assert len(tool_events) == 3
        tool_names = [str(r.get("tool_name", "")) for r in tool_events]
        assert "trw_session_start" in tool_names
        assert "trw_checkpoint" in tool_names
        assert "trw_deliver" in tool_names

    def test_t21_telemetry_kill_switch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_dir: Path,
    ) -> None:
        """T-21: telemetry_enabled=False prevents all tool_invocation events."""
        monkeypatch.setattr(telemetry, "_config", _config_with(
            telemetry_enabled=False, telemetry=False,
        ))
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: run_dir)

        @log_tool_call
        def guarded_tool() -> str:
            return "result"

        guarded_tool()
        guarded_tool()
        guarded_tool()

        records = _read_jsonl(run_dir / "meta" / "events.jsonl")
        tool_events = [r for r in records if r.get("event") == "tool_invocation"]
        assert len(tool_events) == 0, "Kill switch should prevent all tool_invocation events"

    def test_t23_meta_dir_removed_during_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-23: meta/ removed during session — decorator fails silently, tool returns normally."""
        import shutil

        rd = tmp_path / "docs" / "task" / "runs" / "20260220T120000Z-vanish01"
        (rd / "meta").mkdir(parents=True)
        (rd / "meta" / "run.yaml").write_text(
            "run_id: vanish\nstatus: active\n", encoding="utf-8",
        )
        (rd / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)

        monkeypatch.setattr(telemetry, "_config", _config_with(
            telemetry_enabled=True, telemetry=False, context_dir="context",
        ))
        monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda: rd)
        monkeypatch.setattr("trw_mcp.tools.telemetry.resolve_trw_dir", lambda: trw_dir)

        # Remove meta/ AFTER caching the run_dir
        shutil.rmtree(rd / "meta")

        @log_tool_call
        def resilient_tool() -> str:
            return "still works"

        # Must not raise — tool returns normally despite missing meta/
        result = resilient_tool()
        assert result == "still works"
