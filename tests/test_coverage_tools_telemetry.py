from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.telemetry import _write_telemetry_record, _write_tool_event


class TestTelemetryExceptionPaths:
    """Lines 100-101 and 135-136 in tools/telemetry.py."""

    def test_fr04_telemetry_write_record_exception_suppressed(self, tmp_path: Path) -> None:
        cfg = TRWConfig()
        object.__setattr__(cfg, "telemetry_enabled", True)
        object.__setattr__(cfg, "telemetry", True)

        def bomb_fn() -> dict[str, object]:
            return {"ok": True}

        from trw_mcp.tools.telemetry import log_tool_call

        wrapped = log_tool_call(bomb_fn)

        with (
            patch("trw_mcp.tools.telemetry.get_config", return_value=cfg),
            patch("trw_mcp.tools.telemetry._get_cached_run_dir", return_value=None),
            patch("trw_mcp.tools.telemetry._write_tool_event"),
            patch("trw_mcp.tools.telemetry._write_telemetry_record", side_effect=RuntimeError("telemetry write failed")),
        ):
            result = wrapped()

        assert result == {"ok": True}

    def test_write_tool_event_fallback_exception_suppressed(self, tmp_path: Path) -> None:
        with (
            patch("trw_mcp.tools.telemetry._get_cached_run_dir", return_value=None),
            patch("trw_mcp.tools.telemetry.resolve_trw_dir", side_effect=RuntimeError("no trw dir")),
        ):
            _write_tool_event("test_tool", 12.5, True, None)

    def test_write_telemetry_record_writes_to_logs(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        with patch("trw_mcp.tools.telemetry.resolve_trw_dir", return_value=trw_dir):
            _write_telemetry_record("my_tool", (), {}, 42.0, {"result": "ok"}, True)

        from trw_mcp.models.config import get_config as _get_config

        telemetry_file = trw_dir / "logs" / _get_config().telemetry_file
        assert telemetry_file.exists()
